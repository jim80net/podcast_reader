import { defineConfig, externalizeDepsPlugin } from 'electron-vite'

// Three build targets (design decision 1): main = node, preload = isolated
// CJS (sandboxed preloads cannot be ESM), renderer = browser. Entry points are
// the electron-vite defaults: src/main/index.ts, src/preload/index.ts,
// src/renderer/index.html.
export default defineConfig({
  main: {
    plugins: [externalizeDepsPlugin()]
  },
  preload: {
    plugins: [externalizeDepsPlugin()]
  },
  renderer: {}
})
