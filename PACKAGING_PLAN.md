# TREE Desktop — Packaging & Distribution Plan

**Goal:** ship per-platform desktop installers (macOS / Windows / Linux) on GitHub
Releases. A user downloads the installer for their OS, installs it, opens the app —
the embedding model is fetched and started automatically on first run, and every
function previously done via the `tre` CLI is available in-app. **No terminal, ever.**

## Locked decisions
- **Windows:** ship **unsigned** for now; document the SmartScreen "More info → Run
  anyway" workaround. Revisit a code-signing cert later.
- **macOS:** build, sign, notarize, staple, and validate on the local release Mac;
  `notarytool` reads credentials from a Keychain profile. GitHub Actions never
  receives the Developer ID or App Store Connect private key.
- **Embedding model:** **download on first run** with in-app progress (not bundled
  in the installer — it's ~600 MB). Reuses the M2 llama-server + Qwen3 GGUF
  auto-download.

## Target architecture
- **Tauri** shell (Rust + system WebView) loads the built **React SPA** (`desktop/`).
- The **Python engine** is bundled with **PyInstaller** and shipped as a Tauri
  **sidecar**. The shell spawns it on launch as a headless server (`tre serve`),
  and talks to it over loopback HTTP + WebSocket with a per-launch token.
- On first run the sidecar downloads the llama-server binary + Qwen3 GGUF and starts
  the embedding server (M2 path), surfaced with progress in the UI.
- **Distribution:** GitHub Actions builds the unsigned Windows installers; the
  validated local macOS DMG is uploaded to the same draft release.

## Current state (done)
- React SPA over the FastAPI: run/stop, live progress (WebSocket), DAG (open in
  system viewer), outputs reader, setup form, engine-status pill.
- Cross-platform process mgmt (`spawn_detached` / `pid_alive` / `terminate_pid`).
- Embedding via auto-downloaded llama-server binary (no native C-extension to bundle).
- `[gui]` extra; loopback-only, token-gated server.

## Phases

### Phase 1 — Packaging spike (de-risk the Python sidecar) — HIGHEST RISK — ✅ PASSED
- [x] Headless `tre serve --host --port --token` entry (GUI server, no browser open,
      caller-supplied token).
- [x] PyInstaller entry script + spec under `packaging/` (`tre_entry.py`,
      `tre-engine.spec`, `build_sidecar.sh`). GUI templates/static declared as explicit
      `datas` (collect_data_files missed them under editable install); `collect_all` for
      qdrant_client + huggingface_hub; `collect_submodules('uvicorn')` + `websockets`.
- [x] Built standalone binary on macOS (onedir, ~109 MB). Ran with `env -i PATH=/usr/bin:/bin`
      (no Python/venv): `/api/status` 200 + valid JSON, no-token 403, `/` 200, static 200.
      Bundle reports `embedding_backend: llama-server (auto-download on first run)` →
      embedding uses the downloaded binary (no Python), live download not exercised.
- **Acceptance: MET.** The bundled binary serves the API standalone; embedding path
  resolves to the downloadable llama-server (verified by resolution, not a live 600 MB pull).

### Phase 2 — In-app feature completeness (browser-testable, no new toolchain) — in progress
- [~] Workspace selection — DEFERRED to Phase 3: handled by the Tauri native folder
      picker launching the sidecar with `--root <folder>` (the picker lives at the shell
      level). For now `tre serve --root` selects it and `create_app` runs
      `ensure_workspace_dirs` on open (== `/init`).
- [x] Add materials: `GET/POST /api/materials` (multipart upload into
      `materials/<collection>`, extension-validated, collection basename-sanitized) +
      React `Materials` card (collection field, file picker, live list).
- [x] Embedding lifecycle: `GET /api/embedding`, `POST /api/embedding/{start,stop}`
      (start runs off-thread; first run downloads). React auto-starts embedding on open
      + Start/Stop controls + status.
- [x] Embedding bringup status (phase: preparing/downloading/starting/running/failed)
      written by the service, surfaced in `/api/embedding` + the React control, so
      first-run download/startup is visible.  [ ] byte-level progress bar (later refinement).
- [x] `/clean` → `POST /api/clean`; `/init` covered by ensure-dirs-on-serve; status shown.
- **Acceptance (browser):** add materials → run → watch → read outputs → open DAG, no CLI.
  Folder selection via `tre serve --root` until the Phase-3 native picker lands.

### Phase 3 — Tauri shell — in progress
- [x] Installed Rust (cargo 1.96); scaffolded `desktop/src-tauri/` (Tauri v2) via the CLI,
      loads the React dist / Vite dev server.
- [x] Rust shell spawns the engine sidecar (`tre serve` with a generated port+token+root)
      and exposes base+token to the frontend via an `api_config` command; kills it on exit.
      Sidecar path from `TREE_SIDECAR_BIN` or the dev PyInstaller build; `TREE_API_*` env
      overrides for dev with a manually-run server.
- [x] React is Tauri-aware: `initApi()` pulls base+token from `api_config` inside the shell
      (connect screen skipped), falls back to browser resolution otherwise.
- [ ] Verify `cargo build` / `tauri dev` compiles + opens a window (in progress).
- [x] Bundle the sidecar: PyInstaller onedir bundled as a Tauri **resource**
      (`bundle.resources`), spawned from `resource_dir/tre-engine/` in prod (cleaner than
      onefile for a Python app). Rust compiles clean.
- [x] Project Library replaces raw workspace picking in the desktop UX: managed project
      roots live under `~/.tree/projects/`; create/open switches sidecar root; existing
      TREE workspaces can be imported by copying `materials/`, `outputs/`, and `.tree/`.
- [x] Project lifecycle polish: rename updates metadata without moving the stable project
      path, and delete uses typed confirmation before removing managed project files.
- [ ] Verify the actual bundled window (`tauri dev` / `tauri build`) at runtime — only
      compile-verified so far.
- **Acceptance:** `cargo tauri dev` opens a native window that does the full flow.

### Phase 4 — CI + installers + Releases — ready for 0.3.7 validation
- [x] `.github/workflows/release.yml` runs release-doctor plus Python, frontend,
      and Rust tests before building Windows installers and attaching them to a
      draft GitHub Release.
- [x] Direct Python/PyInstaller release dependencies are constrained in
      `packaging/release-constraints.txt`; npm and Cargo use their lock files.
- [x] Windows release assets include SHA-256 checksums and a Python dependency inventory.
- [x] `packaging/release_macos.sh` builds from an exact clean tag, signs nested
      sidecar code, submits with a Keychain notary profile, staples, and validates
      the DMG before optional upload.
- [x] Windows unsigned (release notes document the SmartScreen workaround).
- [ ] Run the first 0.3.7 release candidate from an exact tag and retain its
      release-doctor, checksum, codesign, stapler, Gatekeeper, and DMG verification evidence.
- **Acceptance:** one tested tag produces validated macOS and Windows installers.

### Phase 5 — Distribution polish
- [ ] README "Download & install" section (per-OS), incl. Windows unsigned note.
- [ ] Optional: Tauri auto-updater.
- [ ] Decide whether the desktop app supersedes the htmx `tre gui` (avoid maintaining two).

## Risks / open items
- PyInstaller hidden imports for qdrant-client/grpcio, pydantic-core, huggingface_hub —
  Phase 1 confirms or surfaces the wall early.
- Sidecar and Tauri bundles must be built **on each OS** (no cross-compile).
- First-run model download size/time; offer the `hf-mirror.com` endpoint.
- macOS notarization is intentionally local; keep the Keychain profile and Developer ID
  identity available only on the designated release Mac.

## Progress log
- 2026-06-18: Plan created. Decisions locked (Win unsigned / macOS notarize / model on
  first run). Starting Phase 1.
- 2026-06-18: Phase 1 PASSED. `tre serve` headless entry added; PyInstaller onedir bundle
  (~109 MB) serves the API standalone with no Python on PATH (status 200 / auth 403 / static
  200). Python-sidecar packaging is viable — the single-installer plan is unblocked.
- 2026-06-18: 0.3.1 released (engine-state pill, open-DAG, headless serve + earlier fixes).
  Phase 2 in progress: materials upload (API + React Materials card), embedding controls +
  auto-start-on-open, /clean, init-on-serve. Workspace folder picker deferred to Phase 3.
- 2026-06-18: 0.3.2 prepared with project library/lifecycle controls, imported-file
  manifest UI, export flow, DAG 3D tab, Reader, and simplified Imported Files import UX.
  279 tests green, frontend builds. Remaining: embedding download progress (refinement).
