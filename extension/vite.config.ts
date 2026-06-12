import { resolve } from 'node:path'
import { defineConfig } from 'vite'

/**
 * MV3 build (design decision 1): plain vite, two entries — the popup page
 * and the service worker. `root` is `src/` so `popup.html` lands at the
 * dist root where the manifest expects it; `public/` carries the manifest
 * and icons verbatim. Entry names are pinned (`sw.js`) because the manifest
 * references them by exact path; shared chunks stay ES modules, which the
 * `"type": "module"` service worker can import.
 */
export default defineConfig({
  root: 'src',
  publicDir: '../public',
  build: {
    outDir: '../dist',
    emptyOutDir: true,
    target: 'chrome120',
    modulePreload: false,
    rollupOptions: {
      input: {
        popup: resolve(import.meta.dirname, 'src/popup.html'),
        sw: resolve(import.meta.dirname, 'src/sw.ts')
      },
      output: {
        entryFileNames: '[name].js',
        chunkFileNames: 'chunks/[name]-[hash].js',
        assetFileNames: 'assets/[name]-[hash][extname]'
      }
    }
  }
})
