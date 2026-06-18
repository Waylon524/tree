# TREE Productization Plan

**Goal:** turn TREE from a workspace-oriented developer tool into a project-centered
desktop app. Users import source files, run TREE, inspect the growing knowledge
DAG, read generated Markdown/LaTeX knowledge files in-app, and export selected
outputs to a folder they choose. Internal folders such as `materials/`,
`outputs/`, and `.tree/runtime/` should disappear from the primary user model.

**Date:** 2026-06-18

## Product Direction

### Locked Direction
- Users think in **projects**, not folders.
- A project owns its imported source files, runtime state, DAG, and generated
  outputs.
- Source import is a copy/import action. TREE keeps an internal project copy.
- Generated files live inside the project until the user exports them.
- The internal pipeline remains observable, but the internal file layout becomes
  a black box.
- Global settings remain global where they are truly user-level:
  - LLM API key
  - PaddleOCR API key
  - shared embedding runtime/model cache
  - default model/provider settings
- Per-project state remains isolated:
  - imported source materials
  - planner artifacts
  - vector/RAG store
  - DAG
  - generated knowledge files
  - progress and pipeline state

### Product Model
The app should feel like this:

1. Open TREE.
2. Install embedding extension if needed.
3. Choose or create a project.
4. Import files into that project.
5. Run generation.
6. Watch progress and DAG growth.
7. Read generated knowledge files from DAG nodes.
8. Export outputs to any user-selected folder.

## Current Project Baseline

The current implementation already gives us useful building blocks:

- The Python FastAPI GUI is root-scoped through `create_app(root, token=...)`.
- Tauri spawns the Python sidecar with `tre serve --root <folder>`.
- Current workspace layout is:
  - `materials/` for user-added inputs
  - `outputs/` for generated files and `knowledge-dag.svg`
  - `.tree/runtime/` for planner, progress, state, RAG, OCR, drafts, services
- Current React app uses lightweight page state, not React Router.
- Existing tabs are:
  - `Overview`
  - `Materials`
  - `Outputs`
  - `DAG`
  - `Settings`
- Existing useful APIs include:
  - `GET /api/status`
  - `POST /api/run`
  - `POST /api/stop`
  - `GET/POST /api/materials`
  - `GET /api/outputs`
  - `GET /outputs/{name}`
  - `GET /api/dag`
  - `GET/POST /api/settings`
  - `GET/POST /api/extension`
- `GET /api/dag` already returns `output_paths` per node, which is exactly the
  bridge needed for node-to-reader navigation.
- The DAG tab is already lazy-loaded with `React.lazy`, so the heavy 3D chunk is
  bundled locally but loaded only when the user enters DAG.

This means the first productization pass does **not** need a deep engine rewrite.
We can map each project to an internal root and keep the engine contract mostly
unchanged.

## Target Architecture

### Storage Layout
First version should use a simple per-user project registry:

```text
~/.tree/
  config.env
  services/
  bin/
  projects/
    index.json
    <project-id>/
      project.json
      materials/
      outputs/
      .tree/
        runtime/
```

`<project-id>` should be a stable generated id, not the display name. Project
renames should not move data.

### Project Metadata
`project.json` should contain reader-facing metadata and migration/version data:

```json
{
  "schema": "tree.project",
  "version": 1,
  "id": "proj_...",
  "name": "Calculus Notes",
  "created_at": "2026-06-18T12:00:00Z",
  "updated_at": "2026-06-18T12:30:00Z",
  "last_opened_at": "2026-06-18T12:30:00Z",
  "description": "",
  "source_count": 12,
  "output_count": 8,
  "last_run_status": "stopped"
}
```

`index.json` should be a compact registry for fast Project Library rendering:

```json
{
  "schema": "tree.project-index",
  "version": 1,
  "current_project_id": "proj_...",
  "projects": [
    {
      "id": "proj_...",
      "name": "Calculus Notes",
      "path": "~/.tree/projects/proj_...",
      "last_opened_at": "2026-06-18T12:30:00Z"
    }
  ]
}
```

### Ownership Boundary
Use the Tauri shell as the first owner of project switching:

- The shell can show Project Library before a Python sidecar is running.
- Selecting a project starts/restarts the sidecar with that project's internal
  root via `tre serve --root`.
- Switching project stops the current project engine first, then restarts the
  sidecar against the new root.
- The Python engine continues to operate on one root at a time.

Later, if browser-only `tre gui` needs project switching, the project registry
logic can be mirrored or moved into Python. For desktop-first productization,
Tauri ownership is the smallest safe step.

## UX Surfaces

### Project Library
New first-class screen shown before the app dashboard when no project is active.

Expected actions:
- Create Project
- Open Project
- Rename Project
- Delete Project, with strong confirmation
- Switch Project
- Import Existing TREE Workspace, migration path

Project cards should show:
- name
- last opened time
- imported file count
- generated output count
- last run status

No filesystem paths should be prominent by default. An advanced "Show storage"
action is acceptable for debugging.

### Main App
Keep the current tab model:

- `Overview`: run/stop, progress, health, recent activity
- `Materials`: imported files in this project
- `Outputs`: generated files in this project, export controls
- `DAG`: 3D knowledge tree, node inspector
- `Settings`: global provider/API settings, plus later project-specific options

The UI should stop presenting `materials/` and `outputs/` as user-managed
folders. Use terms like "Imported files" and "Generated files".

### Import Flow
User-facing behavior:

1. User clicks Import.
2. Native file picker opens.
3. Selected files are copied into the current project.
4. UI shows imported file list and validation errors.

Implementation notes:
- Keep `POST /api/materials` for compatibility.
- Add a project-aware import manifest later:

```json
{
  "schema": "tree.material-manifest.ui",
  "files": [
    {
      "id": "src_...",
      "original_name": "lecture.pdf",
      "stored_name": "lecture.pdf",
      "collection": "default",
      "imported_at": "2026-06-18T12:00:00Z",
      "size_bytes": 12345,
      "sha256": "..."
    }
  ]
}
```

Collision strategy:
- Preserve original name when possible.
- If a file name collides, append a stable suffix:
  - `lecture.pdf`
  - `lecture 2.pdf`
  - `lecture 3.pdf`
- Use content hash for duplicate detection, but do not block duplicate imports
  without a clear user choice.

### Export Flow
User-facing behavior:

1. User clicks Export from Outputs or Reader.
2. Native folder picker opens.
3. TREE copies selected generated files into the chosen folder.
4. UI shows success, destination, and skipped/failed files.

Backend/API options:

```http
POST /api/exports
Content-Type: application/json

{
  "destination": "/Users/example/Desktop/TREE Export",
  "files": ["001.Foundations.md", "002.Ready Branch.md"],
  "mode": "copy"
}
```

Response:

```json
{
  "exported": [
    {
      "name": "001.Foundations.md",
      "destination": "/Users/example/Desktop/TREE Export/001.Foundations.md"
    }
  ],
  "skipped": [],
  "failed": []
}
```

Security rules:
- Only export files from the project's `outputs/` directory.
- Normalize and validate output file names.
- Do not allow arbitrary source paths in the request.
- Destination is chosen by Tauri dialog in normal desktop use.

## DAG Node Reader

### Product Behavior
Each DAG node inspector should add an output action:

- `locked`, `ready`, `running`, `failed`: disabled, "Output not ready"
- `complete` with no output paths: disabled, "No output file found"
- `complete` with one output: `Read Output`
- `complete` with multiple outputs: `Read Outputs` with a small list

Clicking opens an in-app reader page for that generated Markdown file.

### Reader Requirements
The Reader page should support:

- Markdown headings, tables, blockquotes, lists, code blocks
- LaTeX inline math and block math
- Chinese and English text
- Back to DAG
- Back to Outputs
- Export this file
- Optional table of contents
- Optional copy selected text / copy source

### Reader Architecture
Use page state before introducing a router:

```ts
type Page =
  | "overview"
  | "materials"
  | "outputs"
  | "dag"
  | "reader"
  | "settings";
```

Reader state:

```ts
interface ReaderTarget {
  name: string;
  from: "dag" | "outputs";
  nodeId?: string;
}
```

Suggested APIs:

```http
GET /api/outputs/{name}/raw
```

Response:

```json
{
  "name": "001.Foundations.md",
  "markdown": "# Foundations\n\n...",
  "size_bytes": 12345,
  "updated_at": "2026-06-18T12:00:00Z"
}
```

Optional node-centric convenience endpoint:

```http
GET /api/dag/nodes/{node_id}/outputs
```

Response:

```json
{
  "node_id": "n1",
  "outputs": [
    {
      "name": "001.Foundations.md",
      "path": "outputs/001.Foundations.md",
      "title": "Foundations"
    }
  ]
}
```

### Markdown/LaTeX Rendering Choice
Preferred frontend stack:

- `react-markdown`
- `remark-gfm`
- `remark-math`
- `rehype-katex`
- `katex`

Reasons:
- Keeps raw Markdown available to frontend features.
- Handles embedded LaTeX locally.
- Avoids expanding Python server HTML rendering.
- Fits the current React/Vite desktop app.

Keep the existing `/outputs/{name}` HTML endpoint for backward compatibility.

## Implementation Phases

### Phase 0 - Stabilize Current Desktop Baseline
Status: baseline complete; legacy GUI decision remains open.

Tasks:
- [x] Settings page split with global config.
- [x] Embedding extension gate.
- [x] DAG tab split from Overview.
- [x] 3D DAG using `react-force-graph-3d`.
- [x] Lazy-load DAG chunk with `React.lazy`.
- [x] Re-run desktop package build after final UI changes.
- [ ] Decide whether to keep the legacy htmx GUI long-term.

Acceptance:
- Frontend build passes.
- Full backend tests pass.
- Tauri app opens with sidecar.
- DAG tab renders without blocking first screen.

### Phase 1A - Project Registry and Project Library, minimal slice
Goal: make projects the app's first-level object while preserving the current
root-scoped Python engine.

Tasks:
- [x] Add project registry storage under `~/.tree/projects/index.json`.
- [x] Add Tauri commands:
  - `app_bootstrap`
  - `list_projects`
  - `create_project`
  - `select_project`
  - `api_config` compatibility
- [x] Add project root creation:
  - `~/.tree/projects/<project-id>/project.json`
  - `materials/`
  - `outputs/`
  - `.tree/runtime/`
- [x] Add Project Library screen before Dashboard.
- [x] Add sidecar restart on project switch.
- [x] Stop current project engine before switching.
- [x] Persist last selected project.
- [x] Rebuild PyInstaller sidecar.
- [x] Rebuild Tauri `.app` and `.dmg`.
- [x] Manual smoke verification passed.

Deferred to later polish/migration:
- [x] Add `rename_project`.
- [x] Add `delete_project`.
- [ ] Add independent `current_project` command if needed.
- [x] Add "Import Existing TREE Workspace" migration action.

Acceptance:
- App opens to Project Library when no project is selected.
- User can create a project and enter Dashboard.
- User can switch between two projects.
- Each project has independent materials, outputs, progress, DAG, and runtime.
- Global settings remain shared.

Tests:
- Rust/Tauri command tests where possible.
- Browser smoke test for create/switch project.
- Python regression tests unchanged because `tre serve --root` still works.

Risks:
- Sidecar lifecycle bugs during project switch.
- Old project engine may continue running if not stopped before sidecar restart.
- Project registry corruption if writes are not atomic.

Mitigation:
- Atomic JSON writes.
- Strict sidecar state machine in Rust.
- UI disables project switch while engine is stopping/starting.

### Phase 2 - Import and Export Semantics
Goal: remove user-facing folder management from the normal workflow.

Tasks:
- [x] Rename UI copy from "Materials" to "Imported Files" where appropriate.
- [x] Keep the existing `GET/POST /api/materials` but make copy/import semantics explicit.
- [x] Add import manifest for original names, hashes, sizes, and import timestamps.
- [x] Hide the default internal collection from the main Imported Files flow.
- [x] Use the native file chooser for import in desktop/webview mode.
- [x] Keep browser file input fallback for web/dev mode.
- [x] Add `POST /api/exports`.
- [x] Add export controls to Generated Files.
- [x] Add export all / export selected.
- [x] Add export result toast/status.
- [x] Ensure exported files are copies, not moved.

Acceptance:
- User can import files without seeing project storage paths.
- User can export generated files to a selected folder.
- Exported files match internal outputs byte-for-byte.
- Invalid file names cannot escape project boundaries.

Tests:
- Backend API tests for export safety and collision behavior.
- Frontend build.
- Desktop smoke test with Tauri dialog, if available.
- Browser fallback test for upload.
- [x] Backend API tests for export safety and import manifest reconciliation.
- [x] Frontend build passes.
- [x] Desktop manual smoke verification passed.
- [x] Browser/dev upload fallback preserved.

### Phase 3 - DAG Node Reader
Goal: make the DAG a navigation surface for generated knowledge.

Tasks:
- [x] Add raw output API:
  - `GET /api/outputs/{name}/raw`
- [ ] Optionally add node output API:
  - `GET /api/dag/nodes/{node_id}/outputs`
- [x] Extend `DagWorkbench` inspector:
  - output-ready action
  - disabled state for incomplete nodes
  - multiple output selector
- [x] Add `Reader` page state and component.
- [x] Add Markdown + LaTeX rendering dependencies.
- [x] Add reader controls:
  - Back to DAG
  - Back to Outputs
  - Export this file
- [x] Preserve selected DAG node when returning from Reader.

Deferred:
- [ ] Add `GET /api/dag/nodes/{node_id}/outputs` only if DAG payload `output_paths`
      becomes insufficient for Reader navigation.

Acceptance:
- Complete DAG node with output opens Reader.
- Reader renders Markdown tables and code blocks.
- Reader renders inline and block LaTeX.
- Reader can export the current output file.
- Back returns to DAG without losing context.

Tests:
- Backend raw output auth and path-safety tests.
- Frontend build.
- Browser smoke test:
  - complete node shows Read Output
  - click opens Reader
  - math renders
  - back returns to DAG
- [x] Backend raw output auth and path-safety tests.
- [x] Frontend build passes.
- [x] Desktop manual smoke verification passed.
- [x] Reader opens from complete DAG node and Generated Files.
- [x] Reader renders Markdown and LaTeX.
- [x] Reader export current file verified.
- [x] Back navigation to DAG/Outputs verified.

Risks:
- Markdown renderer may allow unsafe HTML.
- Math rendering CSS may collide with app styles.
- Large generated files may slow the reader.

Mitigation:
- Disable or sanitize raw HTML by default.
- Scope KaTeX/reader CSS.
- Add size-aware loading state.

### Phase 4 - Project Polish and Migration
Goal: make project-centered usage feel durable and understandable.

Tasks:
- [x] Add project settings panel:
  - name
  - description
  - created/updated times
  - storage size
  - output count
- [x] Add project deletion flow with typed confirmation.
- [ ] Add "Duplicate Project" if useful.
- [ ] Add "Archive Project" later if deletion feels too risky.
- [x] Add migration for existing root workspaces:
  - copy existing workspace into managed project storage
  - preserve `materials/`, `outputs/`, `.tree/runtime/`
  - generate `project.json`
- [x] Update README and packaging docs around project workflow.

Acceptance:
- [x] Existing users can migrate a current TREE workspace.
- [x] New users never need to create or choose a raw filesystem workspace.
- [x] Project data remains inspectable for debugging but not central to UX.

## API Summary

Implemented additions:

```http
GET /api/imported-files
GET /api/outputs/{name}/raw
POST /api/exports
```

Implemented Tauri commands:

```ts
app_bootstrap()
list_projects()
create_project({ name })
rename_project({ id, name, description })
delete_project({ id, confirmation })
import_existing_project({ sourcePath, name })
select_project({ id })
api_config()
```

Likely next additions:

```http
GET /api/dag/nodes/{node_id}/outputs
```

Likely Tauri commands:

```ts
current_project()
choose_import_files()
choose_export_directory()
```

Existing APIs to preserve:

```http
GET /api/status
POST /api/run
POST /api/stop
GET /api/materials
POST /api/materials
GET /api/outputs
GET /outputs/{name}
GET /api/dag
GET /api/settings
POST /api/settings
```

## Testing Strategy

### Backend
- Project root compatibility:
  - existing `tre serve --root` still works
  - current tests remain green
- Export safety:
  - rejects path traversal
  - copies only known outputs
  - reports skipped/failed files
- Imported Files manifest:
  - records original/stored names, hash, size, collection, timestamp
  - reconciles active, missing, and legacy disk files
- Reader raw output:
  - requires token
  - returns Markdown
  - rejects invalid names
- DAG node output mapping:
  - complete nodes expose output paths
  - incomplete nodes do not expose a read action

### Frontend
- `npm run build`
- Project Library:
  - create project
  - switch project
  - delete confirmation
- Import flow:
  - file picker/browser upload
  - validation errors
  - list refresh
- Export flow:
  - select destination
  - export selected/all
  - success/failure state
- Reader:
  - Markdown
  - LaTeX
  - back navigation
  - export current file
- DAG:
  - lazy chunk still works
  - node inspector actions match node status

### Desktop / Packaging
- Tauri dev build opens Project Library.
- Project switch restarts sidecar.
- Sidecar stops on app quit.
- Packaging includes lazy DAG chunk and Markdown/KaTeX assets.
- macOS app bundle includes all Vite chunks.

## Migration and Compatibility

### Keep CLI Compatibility
The engine should continue supporting root-based CLI/browser usage:

```bash
tre serve --root /path/to/workspace
```

Project Library is a desktop UX layer over this root model, not an immediate
replacement for the engine contract.

### Existing Workspace Migration
Migration should support:

1. User selects an existing TREE workspace.
2. App detects `materials/`, `outputs/`, `.tree/runtime/`.
3. App offers:
   - Copy into managed project storage
   - Adopt in place, advanced option
4. App writes `project.json`.
5. App registers the project.

Default should be copy into managed storage, because it supports the "folders are
not the user model" direction.

## Open Questions

- Should Project Library appear before or after the embedding extension gate?
  - Current implemented path: choose project first, then reuse existing extension gate.
  - Cleaner long-term path: global extension gate before project selection.
- Should project registry be owned by Tauri, Python, or both?
  - Recommended first step: Tauri owns it for desktop.
  - Later: move shared registry helpers into Python if browser `tre gui` needs it.
- Should output export preserve nested folders if outputs ever become nested?
  - First version can keep flat output names.
- Should Reader allow editing?
  - Not in this plan. Read/export only.
- Should project data be a single `.treeproject` bundle?
  - Not first. Folder-backed storage is simpler and safer for large embeddings/RAG.

## Suggested Next Concrete Step

Continue with **Phase 4A packaging and manual smoke verification**.

Phase 4A project lifecycle controls are implemented in code. The next pass
should rebuild the desktop package and manually verify create, rename, delete,
project switch, and existing workspace migration in the packaged app before
adding optional Duplicate/Archive actions.
