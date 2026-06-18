import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import { fileURLToPath } from "node:url";

const threeRenderObjects = fileURLToPath(
  new URL(
    "./node_modules/3d-force-graph/node_modules/three-render-objects/dist/three-render-objects.min.js",
    import.meta.url,
  ),
);

// Browser-first dev server. The app talks to the `tre gui` FastAPI over its
// token-gated API/WebSocket (see src/api.ts). When this is later wrapped in a
// Tauri shell, the same build output is loaded by the native webview.
export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: {
      "three-render-objects": threeRenderObjects,
    },
  },
  server: { port: 5173 },
  build: { outDir: "dist" },
});
