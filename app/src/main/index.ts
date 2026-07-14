import { homedir } from 'node:os'
import { join, resolve } from 'node:path'

import { BrowserWindow, app, dialog, ipcMain, protocol, safeStorage, shell } from 'electron'
import { autoUpdater } from 'electron-updater'

import { AppConfigStore } from './app-config'
import { broadcastTo } from './broadcast'
import { YOUTUBE_REFERER, YOUTUBE_URL_FILTER, isExternalWebUrl } from './external-links'
import { resolveDataDir } from './data-dir'
import { defaultSupervisorDeps, ensureEngine } from './engine'
import { EngineClient, EventStream } from './engine-client'
import { EngineManager } from './engine-manager'
import { RespawnPolicy } from './respawn-policy'
import { registerIpcHandlers } from './ipc'
import { createMediaProtocolHandler } from './media-protocol'
import { PrivateWebController } from './private-web'
import { parseProtocolUrl, selectProtocolArgv } from './protocol'
import { pidIsAlive } from './quit'
import { ServeOwnershipJournal } from './serve-journal'
import { ServeManager } from './serve-manager'
import { defaultServeManagerDeps } from './serve-process'
import { BUILD_SIGNED, UpdaterController, updaterGate } from './updater'
import { KeyVault } from './vault'
import { PUSH_CHANNELS } from '../shared/ipc'
import type { UpdaterAccess } from './ipc'
import type { UpdateStatus } from '../shared/ipc'

/**
 * Electron main entry: thin glue binding the tested modules to the app
 * lifecycle. Engine supervision, key vault, IPC, single-instance lock, and
 * the podcast-reader:// protocol handler (registered everywhere, trusted
 * nowhere — design decision 7).
 */

const PROTOCOL_SCHEME = 'podcast-reader'
/** Internal in-app resource scheme for media bytes (app-shell spec, F3). */
const MEDIA_SCHEME = 'app'

const log = (message: string): void => console.log(`[podcast-reader] ${message}`)

// Privileged-scheme registration MUST run at module top level, before the
// app's ready event — calling registerSchemesAsPrivileged after ready silently
// no-ops (design F3). standard + secure + stream + supportFetchAPI lets the
// <video>/<audio> elements load and seek app://media/<id> like https. This is
// SEPARATE from the external podcast-reader:// deep-link (setAsDefaultProtocolClient
// below) — different layer, no overlap.
protocol.registerSchemesAsPrivileged([
  {
    scheme: MEDIA_SCHEME,
    privileges: { standard: true, secure: true, stream: true, supportFetchAPI: true }
  }
])

function broadcast(channel: string, payload: unknown): void {
  broadcastTo(BrowserWindow.getAllWindows(), channel, payload)
}

let manager: EngineManager | null = null
let mainWindow: BrowserWindow | null = null
let quitting = false
let updater: UpdaterController | null = null
let updaterDisabled: UpdateStatus = { state: 'disabled', reason: 'updater not initialized' }

// Test seam: an explicit userData override keeps the vault and the
// single-instance lock (which keys off userData) isolated per run. Gated to
// unpackaged builds (the e2e harness launches unpackaged) so packaged
// installs expose no env knob that relocates credential storage (R5).
const userDataOverride = process.env['PODCAST_READER_USER_DATA_DIR']
if (!app.isPackaged && userDataOverride !== undefined && userDataOverride !== '') {
  app.setPath('userData', resolve(userDataOverride))
}

// ---- single instance + protocol registration (per P8) ----------------------

const isPrimaryInstance = app.requestSingleInstanceLock()
if (!isPrimaryInstance) {
  app.quit()
} else {
  if (process.defaultApp && process.argv[1] !== undefined) {
    // Dev mode: electron is the executable and the app path is an argument,
    // so both must be registered for the protocol to round-trip (per P8).
    app.setAsDefaultProtocolClient(PROTOCOL_SCHEME, process.execPath, [
      resolve(process.argv[1])
    ])
  } else {
    app.setAsDefaultProtocolClient(PROTOCOL_SCHEME)
  }

  app.on('second-instance', (_event, commandLine) => {
    focusMainWindow()
    // Per P8: select the matching commandLine entry — never pop blindly.
    const raw = selectProtocolArgv(commandLine)
    if (raw !== null) void handleProtocolUrl(raw)
  })

  app.on('open-url', (event, url) => {
    event.preventDefault()
    focusMainWindow()
    void handleProtocolUrl(url)
  })

  void app.whenReady().then(start)
}

// ---- startup ----------------------------------------------------------------

async function start(): Promise<void> {
  let dataDir: string
  try {
    dataDir = resolveDataDir(process.env, homedir())
  } catch (err) {
    // e.g. DataDirError for the unsupported ~user form: fail loudly at
    // startup instead of supervising an engine against the wrong directory.
    const message = err instanceof Error ? err.message : String(err)
    log(`fatal: ${message}`)
    dialog.showErrorBox('Podcast Reader cannot start', message)
    app.quit()
    return
  }
  const vault = new KeyVault(join(app.getPath('userData'), 'vault.json'), safeStorage, log)
  if (vault.mode === 'session-memory') {
    log('safeStorage encryption unavailable: keys are held in memory for this session only')
  }

  const supervisorDeps = defaultSupervisorDeps({
    dataDir,
    env: process.env as Record<string, string | undefined>,
    resourcesPath: app.isPackaged ? process.resourcesPath : null,
    // Dev fallback `uv run podcast-reader serve` runs from the repo root
    // (app/ is <repo>/app, so one level up from the app path).
    devCwd: app.isPackaged ? null : resolve(app.getAppPath(), '..'),
    log
  })

  const appConfig = new AppConfigStore(join(app.getPath('userData'), 'app-config.json'), log)
  const serveManager = new ServeManager(
    defaultServeManagerDeps({
      journal: new ServeOwnershipJournal(
        join(app.getPath('userData'), 'private-web-serve.json')
      ),
      env: process.env as Record<string, string | undefined>,
      resourcesPath: app.isPackaged ? process.resourcesPath : null,
      devCwd: app.isPackaged ? null : resolve(app.getAppPath(), '..'),
      platform: process.platform,
      fileExists: supervisorDeps.fileExists,
      log
    })
  )
  const privateWeb = new PrivateWebController({
    config: appConfig,
    serve: serveManager,
    enginePort: () => manager?.port ?? null,
    send: (status) => broadcast(PUSH_CHANNELS.privateWebStatus, status),
    log
  })

  manager = new EngineManager({
    ensure: () => ensureEngine(supervisorDeps),
    createClient: (handle) => new EngineClient(handle.port, handle.token),
    createStream: (client, handlers) => new EventStream(client, handlers),
    vault,
    send: broadcast,
    isAlive: pidIsAlive,
    killPid: supervisorDeps.killPid,
    sleep: supervisorDeps.sleep,
    now: Date.now,
    // Jitter (0–250ms) de-syncs simultaneous crash bursts; Math.random is fine
    // here — this is backoff spread, not anything security-sensitive.
    policy: new RespawnPolicy({ jitter: () => Math.floor(Math.random() * 250) }),
    privateWeb,
    log
  })
  registerIpcHandlers(ipcMain, manager, setupUpdater(), appConfig, privateWeb)

  // Install the app://media handler now that the engine manager (token source)
  // exists. The handler reads loopback coordinates lazily on each request, so
  // it answers 503 before ready and after quit (manager.media returns null).
  const mediaManager = manager
  const mediaHandler = createMediaProtocolHandler(() => mediaManager.media)
  protocol.handle(MEDIA_SCHEME, (request) => mediaHandler(request))

  createWindow()
  await manager.start()

  // Windows cold-start protocol launch: the URL arrives in our own argv.
  const raw = selectProtocolArgv(process.argv)
  if (raw !== null) void handleProtocolUrl(raw)
}

// ---- auto-update (design decisions 9, 10) -----------------------------------

function setupUpdater(): UpdaterAccess {
  const gate = updaterGate({
    isPackaged: app.isPackaged,
    buildSigned: BUILD_SIGNED,
    env: process.env as Record<string, string | undefined>
  })
  if (gate.enabled) {
    updater = new UpdaterController({
      autoUpdater,
      confirm: async (version) => {
        const result = await dialog.showMessageBox({
          type: 'info',
          title: 'Update ready',
          message: `Podcast Reader ${version} has been downloaded.`,
          detail: 'Restart now to install? The local engine is shut down first.',
          buttons: ['Restart and install', 'Later'],
          defaultId: 0,
          cancelId: 1
        })
        return result.response === 0
      },
      quitEngine: async () => {
        // quitAndInstall re-enters before-quit; the flag stops that handler
        // from preventDefault-ing the install.
        quitting = true
        await manager?.quit()
      },
      send: broadcast,
      log
    })
    updater.start()
  } else {
    updaterDisabled = { state: 'disabled', reason: gate.reason }
    log(`auto-update: ${gate.reason}`)
  }
  return {
    status: () => updater?.status ?? updaterDisabled,
    installNow: () => updater?.installNow() ?? Promise.resolve()
  }
}

/**
 * Resolve the branded window icon (native-app-first-impression). Packaged: the
 * `build/icon.png` shipped via extraResources sits at `<resources>/icon.png`.
 * Dev/unpackaged: it is `<app>/build/icon.png` (getAppPath() is the app/ dir).
 * macOS draws the dock/window from the bundle `.icns` and ignores this, so it
 * mainly brands the Linux/Windows window + taskbar and every dev run.
 */
function windowIconPath(): string {
  return app.isPackaged
    ? join(process.resourcesPath, 'icon.png')
    : join(app.getAppPath(), 'build', 'icon.png')
}

function createWindow(): void {
  mainWindow = new BrowserWindow({
    // Wide enough that the Reader's full editorial layout — chapter-nav
    // sidebar + transcript + key-points gutter — clears the artifact's 1200px
    // gutter breakpoint out of the box (the gutter hides below it).
    width: 1360,
    height: 860,
    minWidth: 720,
    show: false,
    icon: windowIconPath(),
    webPreferences: {
      preload: join(__dirname, '../preload/index.js'),
      // Credential-free renderer (design decision 7): isolated, sandboxed,
      // no node. The engine token never has a path into this process.
      contextIsolation: true,
      sandbox: true,
      nodeIntegration: false
    }
  })
  mainWindow.on('ready-to-show', () => mainWindow?.show())
  mainWindow.on('closed', () => {
    mainWindow = null
  })

  // External links (the "Watch on YouTube" fallback, provider key-docs links)
  // open in the OS default browser — where the user is logged in — never a
  // chromeless in-app window. window.open (target="_blank") is denied here and
  // handed to the shell; top-level navigations to web URLs are caught too, so
  // the single file:// document can never navigate away from the app.
  const openExternal = (url: string): void => {
    // A rejection (no default browser, handler unavailable) must not become an
    // unhandled promise rejection — log and move on.
    shell.openExternal(url).catch((err: unknown) => log(`openExternal failed: ${String(err)}`))
  }
  mainWindow.webContents.setWindowOpenHandler(({ url }) => {
    if (isExternalWebUrl(url)) openExternal(url)
    return { action: 'deny' }
  })
  mainWindow.webContents.on('will-navigate', (event, url) => {
    if (isExternalWebUrl(url)) {
      event.preventDefault()
      openExternal(url)
    }
  })

  // YouTube's embedded player rejects requests without a valid HTTP Referer
  // (Error 153, enforced late 2025). The file:// renderer sends none, so inject
  // one on YouTube-bound requests only — scoped by host filter so engine
  // (127.0.0.1) and app://media traffic are never given a spoofed Referer.
  mainWindow.webContents.session.webRequest.onBeforeSendHeaders(
    YOUTUBE_URL_FILTER,
    (details, callback) => {
      callback({ requestHeaders: { ...details.requestHeaders, Referer: YOUTUBE_REFERER } })
    }
  )

  if (!app.isPackaged && process.env['ELECTRON_RENDERER_URL'] !== undefined) {
    void mainWindow.loadURL(process.env['ELECTRON_RENDERER_URL'])
  } else {
    void mainWindow.loadFile(join(__dirname, '../renderer/index.html'))
  }
}

function focusMainWindow(): void {
  if (mainWindow === null) return
  if (mainWindow.isMinimized()) mainWindow.restore()
  mainWindow.focus()
}

// ---- protocol handling (design decision 7) ----------------------------------

async function handleProtocolUrl(raw: string): Promise<void> {
  const request = parseProtocolUrl(raw)
  if (request === null) {
    log(`rejected protocol URL: ${raw}`)
    return
  }
  const client = manager?.client ?? null
  if (client === null) {
    log(`protocol request before engine ready, dropped: ${request.url}`)
    return
  }
  try {
    // Never auto-executes: requires_confirmation journals the job in
    // awaiting-confirmation until the user explicitly confirms.
    const job = await client.submitJob({
      source: request.url,
      title: null,
      requires_confirmation: true
    })
    log(`protocol request awaiting confirmation: ${request.url} (job ${job.id})`)
    broadcast(PUSH_CHANNELS.protocolRequest, job)
    focusMainWindow()
  } catch (err) {
    log(`protocol job submission failed: ${String(err)}`)
  }
}

// ---- quit sequence (design decision 3, per P1/P7) ----------------------------

app.on('before-quit', (event) => {
  if (quitting || manager === null) return
  event.preventDefault()
  quitting = true
  void manager
    .quit()
    .catch((err: unknown) => log(`quit sequence failed: ${String(err)}`))
    .finally(() => app.quit())
})

app.on('window-all-closed', () => {
  // Single-window shell on all platforms for now: closing it quits (and the
  // quit path shuts the engine down — single-ownership model, per P7).
  app.quit()
})
