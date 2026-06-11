import { homedir } from 'node:os'
import { join, resolve } from 'node:path'

import { BrowserWindow, app, ipcMain, safeStorage } from 'electron'

import { resolveDataDir } from './data-dir'
import { defaultSupervisorDeps, ensureEngine } from './engine'
import { EngineClient, EventStream } from './engine-client'
import { EngineManager } from './engine-manager'
import { registerIpcHandlers } from './ipc'
import { parseProtocolUrl, selectProtocolArgv } from './protocol'
import { pidIsAlive } from './quit'
import { KeyVault } from './vault'
import { PUSH_CHANNELS } from '../shared/ipc'

/**
 * Electron main entry: thin glue binding the tested modules to the app
 * lifecycle. Engine supervision, key vault, IPC, single-instance lock, and
 * the podcast-reader:// protocol handler (registered everywhere, trusted
 * nowhere — design decision 7).
 */

const PROTOCOL_SCHEME = 'podcast-reader'

const log = (message: string): void => console.log(`[podcast-reader] ${message}`)

function broadcast(channel: string, payload: unknown): void {
  for (const window of BrowserWindow.getAllWindows()) {
    window.webContents.send(channel, payload)
  }
}

let manager: EngineManager | null = null
let mainWindow: BrowserWindow | null = null
let quitting = false

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
  const dataDir = resolveDataDir(process.env, homedir())
  const vault = new KeyVault(join(app.getPath('userData'), 'vault.json'), safeStorage)
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

  manager = new EngineManager({
    ensure: () => ensureEngine(supervisorDeps),
    createClient: (handle) => new EngineClient(handle.port, handle.token),
    createStream: (client, handlers) => new EventStream(client, handlers),
    vault,
    send: broadcast,
    isAlive: pidIsAlive,
    killPid: supervisorDeps.killPid,
    sleep: supervisorDeps.sleep,
    log
  })
  registerIpcHandlers(ipcMain, manager)

  createWindow()
  await manager.start()

  // Windows cold-start protocol launch: the URL arrives in our own argv.
  const raw = selectProtocolArgv(process.argv)
  if (raw !== null) void handleProtocolUrl(raw)
}

function createWindow(): void {
  mainWindow = new BrowserWindow({
    width: 1100,
    height: 750,
    show: false,
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
