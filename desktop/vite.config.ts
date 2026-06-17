import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Browser-first dev server. The app talks to the `tre gui` FastAPI over its
// token-gated API/WebSocket (see src/api.ts). When this is later wrapped in a
// Tauri shell, the same build output is loaded by the native webview.
export default defineConfig({
  plugins: [react()],
  server: { port: 5173 },
  build: { outDir: "dist" },
});
