import { defineConfig } from "vite";
import { svelte } from "@sveltejs/vite-plugin-svelte";

// The dev server is fixed to 1420 because tauri.conf.json points `devUrl`
// there; a port that moves would make `tauri dev` load a blank window.
export default defineConfig({
  plugins: [svelte()],
  clearScreen: false,
  server: {
    port: 1420,
    strictPort: true,
    host: "127.0.0.1",
    watch: { ignored: ["**/src-tauri/**"] },
  },
  build: {
    // Vite 8 minifies with oxc; naming esbuild here would pull in a second
    // toolchain for no gain. The webview is always the system WebView2 on this
    // machine, so the default modern target is already correct.
    minify: true,
    sourcemap: false,
    chunkSizeWarningLimit: 300,
  },
});
