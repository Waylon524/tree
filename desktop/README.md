# TREE desktop frontend

React + TypeScript + Vite frontend for TREE. It talks to the `tre gui` FastAPI
server over its token-gated HTTP API and `/ws/progress` WebSocket.

Today it runs in the **browser**; later it will be wrapped in a **Tauri** native
shell with the Python engine bundled as a sidecar — the same `src/` is reused,
only the shell changes.

## Dev

1. Start the backend in your course workspace (note the printed token + port):

   ```bash
   tre gui --no-browser
   # TREE GUI ready: http://127.0.0.1:8799/?token=XXXXXXXX
   ```

2. Start the frontend dev server (point it at the backend if the port differs):

   ```bash
   cd desktop
   npm install
   VITE_TREE_API=http://127.0.0.1:8799 npm run dev
   ```

3. Open the dev URL with the token, e.g. `http://localhost:5173/?token=XXXXXXXX`
   (or paste the token into the connect screen).

## Scripts

- `npm run dev` — Vite dev server (HMR)
- `npm run build` — typecheck (`tsc --noEmit`) + production build to `dist/`
- `npm run preview` — serve the built `dist/`

## Notes

- Auth: every request carries `?token=`; the server binds `127.0.0.1` only.
- `VITE_TREE_API` selects the backend origin (default `http://127.0.0.1:8799`).
- The Python package does not ship this folder; it is built separately.
