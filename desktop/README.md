# TREE desktop frontend

React + TypeScript + Vite frontend for TREE. In browser/dev mode it talks to a
manually started `tre gui` / `tre serve` FastAPI server over its token-gated HTTP
API and `/ws/progress` WebSocket.

In the Tauri desktop shell, the same React app starts from Project Library. The
Rust shell owns `~/.tree/projects/index.json`, creates managed project roots,
starts the Python sidecar with the selected project root, and exposes the API
base/token through `app_bootstrap()`.

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

For Tauri development, run the desktop shell instead of manually passing a token:

```bash
cd desktop
npm exec tauri dev
```

The desktop shell supports create/open project, rename project, typed-confirm
delete, and importing an existing TREE workspace by copying its `materials/`,
`outputs/`, and `.tree/` roots into managed project storage.

## Scripts

- `npm run dev` — Vite dev server (HMR)
- `npm run build` — typecheck (`tsc --noEmit`) + production build to `dist/`
- `npm run preview` — serve the built `dist/`

## Notes

- Auth: every request carries `?token=`; the server binds `127.0.0.1` only.
- `VITE_TREE_API` selects the backend origin (default `http://127.0.0.1:8799`).
- Tauri project storage defaults to `~/.tree/projects/`; project display names
  can change without moving their stable `proj_<uuid>` directories.
- The Python package does not ship this folder; it is built separately.
