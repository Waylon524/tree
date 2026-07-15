import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// In browser development the app talks to `tre gui` over its token-gated
// API/WebSocket. Production builds are loaded by the Tauri native webview.
export default defineConfig({
  plugins: [react()],
  resolve: {
    dedupe: ["three"],
  },
  server: { port: 5173 },
  build: { outDir: "dist" },
});
