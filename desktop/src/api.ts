import type { Status } from "./types";

const TOKEN_KEY = "tree_token";

// API base + token are resolved at runtime. In the Tauri shell they come from
// `app_bootstrap` / `api_config` (the shell owns the sidecar and token); in the
// browser they come from VITE_TREE_API + the URL/connect-screen token.
let resolvedBase: string | null = null;
let resolvedToken: string | null = null;

export function isTauri(): boolean {
  return typeof window !== "undefined" && "__TAURI_INTERNALS__" in window;
}

export interface DesktopApiConfig {
  base: string;
  token: string;
}

export interface ProjectSummary {
  id: string;
  name: string;
  description: string;
  path: string;
  created_at: number;
  updated_at: number;
  last_opened_at: number;
  source_count: number;
  output_count: number;
  storage_bytes: number;
}

export interface AppBootstrap {
  tauri: boolean;
  projects: ProjectSummary[];
  current_project: ProjectSummary | null;
  api: DesktopApiConfig | null;
  error: string | null;
}

export interface ProjectSelection {
  projects: ProjectSummary[];
  current_project: ProjectSummary;
  api: DesktopApiConfig;
}

async function invokeTauri<T>(command: string, args?: Record<string, unknown>): Promise<T> {
  const { invoke } = await import("@tauri-apps/api/core");
  return (await invoke(command, args)) as T;
}

export function setApiConfig(config: DesktopApiConfig): void {
  resolvedBase = config.base.replace(/\/$/, "");
  setToken(config.token);
}

function clearApiConfig(): void {
  resolvedBase = null;
  resolvedToken = null;
  try {
    sessionStorage.removeItem(TOKEN_KEY);
  } catch {
    /* sessionStorage unavailable */
  }
}

function applyBootstrapApi(bootstrap: AppBootstrap): void {
  if (bootstrap.api) {
    setApiConfig(bootstrap.api);
  } else if (bootstrap.tauri) {
    clearApiConfig();
  }
}

// Call once at startup. Inside Tauri this returns project bootstrap state and,
// when a current project exists, resolves the sidecar API. In the browser it's
// a no-op so the existing token flow remains unchanged.
export async function initApi(): Promise<AppBootstrap | null> {
  if (!isTauri()) return null;
  try {
    const bootstrap = await invokeTauri<AppBootstrap>("app_bootstrap");
    if (bootstrap.api) setApiConfig(bootstrap.api);
    return bootstrap;
  } catch (err) {
    try {
      const config = await invokeTauri<DesktopApiConfig>("api_config");
      setApiConfig(config);
      return {
        tauri: true,
        projects: [],
        current_project: null,
        api: config,
        error: null,
      };
    } catch {
      return {
        tauri: true,
        projects: [],
        current_project: null,
        api: null,
        error: err instanceof Error ? err.message : String(err),
      };
    }
  }
}

export function apiBase(): string {
  if (resolvedBase) return resolvedBase;
  return (
    (import.meta.env.VITE_TREE_API as string | undefined)?.replace(/\/$/, "") ??
    "http://127.0.0.1:8799"
  );
}

// Token resolution order: Tauri api_config, URL ?token= (the `tre gui` deep-link),
// a token entered via the connect screen (persisted), then a build-time env var.
export function getToken(): string {
  if (resolvedToken) return resolvedToken;
  const fromUrl = new URL(window.location.href).searchParams.get("token");
  if (fromUrl) return fromUrl;
  try {
    const stored = sessionStorage.getItem(TOKEN_KEY);
    if (stored) return stored;
  } catch {
    /* sessionStorage unavailable */
  }
  return (import.meta.env.VITE_TREE_TOKEN as string | undefined) ?? "";
}

export function setToken(value: string): void {
  resolvedToken = value;
  try {
    sessionStorage.setItem(TOKEN_KEY, value);
  } catch {
    /* sessionStorage unavailable */
  }
}

export async function listProjects(): Promise<ProjectSummary[]> {
  return invokeTauri<ProjectSummary[]>("list_projects");
}

export async function createProject(name: string): Promise<ProjectSelection> {
  const selection = await invokeTauri<ProjectSelection>("create_project", { name });
  setApiConfig(selection.api);
  return selection;
}

export async function selectProject(id: string): Promise<ProjectSelection> {
  const selection = await invokeTauri<ProjectSelection>("select_project", { id });
  setApiConfig(selection.api);
  return selection;
}

export async function renameProject(
  id: string,
  name: string,
  description: string,
): Promise<AppBootstrap> {
  const bootstrap = await invokeTauri<AppBootstrap>("rename_project", { id, name, description });
  applyBootstrapApi(bootstrap);
  return bootstrap;
}

export async function deleteProject(id: string, confirmation: string): Promise<AppBootstrap> {
  const bootstrap = await invokeTauri<AppBootstrap>("delete_project", { id, confirmation });
  applyBootstrapApi(bootstrap);
  return bootstrap;
}

export interface ProjectArchiveResult {
  path: string;
  bytes: number;
}

export async function chooseWorkspaceDirectory(): Promise<string | null> {
  if (!isTauri()) return null;
  const { open } = await import("@tauri-apps/plugin-dialog");
  const selected = await open({
    directory: true,
    multiple: false,
    title: "Choose existing TREE workspace",
  });
  return typeof selected === "string" ? selected : null;
}

export async function chooseProjectArchiveDestination(projectName: string): Promise<string | null> {
  if (!isTauri()) return null;
  const { save } = await import("@tauri-apps/plugin-dialog");
  const safeName = projectName.replace(/[\\/:*?"<>|]+/g, "_").trim() || "TREE";
  const selected = await save({
    title: "Save parent tree archive",
    defaultPath: `${safeName}.zip`,
    filters: [{ name: "TREE Parent Tree", extensions: ["zip"] }],
  });
  return typeof selected === "string" ? selected : null;
}

export async function chooseParentTreeArchive(): Promise<string | null> {
  if (!isTauri()) return null;
  const { open } = await import("@tauri-apps/plugin-dialog");
  const selected = await open({
    directory: false,
    multiple: false,
    title: "Choose parent tree archive",
    filters: [{ name: "TREE Parent Tree", extensions: ["zip"] }],
  });
  return typeof selected === "string" ? selected : null;
}

export async function importExistingProject(
  sourcePath: string,
  name?: string,
): Promise<ProjectSelection> {
  const selection = await invokeTauri<ProjectSelection>("import_existing_project", {
    sourcePath,
    name,
  });
  setApiConfig(selection.api);
  return selection;
}

export async function exportProjectArchive(
  id: string,
  destinationPath: string,
): Promise<ProjectArchiveResult> {
  return invokeTauri<ProjectArchiveResult>("export_project_archive", { id, destinationPath });
}

export async function transplantProject(
  id: string,
  destinationPath: string,
  confirmation: string,
): Promise<AppBootstrap> {
  const bootstrap = await invokeTauri<AppBootstrap>("transplant_project", {
    id,
    destinationPath,
    confirmation,
  });
  applyBootstrapApi(bootstrap);
  return bootstrap;
}

export async function importParentTreeArchive(
  archivePath: string,
  name?: string,
): Promise<ProjectSelection> {
  const selection = await invokeTauri<ProjectSelection>("import_parent_tree_archive", {
    archivePath,
    name,
  });
  setApiConfig(selection.api);
  return selection;
}

function url(path: string): string {
  const u = new URL(apiBase() + path);
  u.searchParams.set("token", getToken());
  return u.toString();
}

async function expectOk(resp: Response): Promise<Response> {
  if (!resp.ok) {
    let detail = "";
    try {
      const data = (await resp.clone().json()) as { detail?: unknown };
      if (typeof data.detail === "string") detail = data.detail;
      else if (data.detail) detail = JSON.stringify(data.detail);
    } catch {
      try {
        detail = (await resp.clone().text()).slice(0, 500);
      } catch {
        detail = "";
      }
    }
    throw new Error(`${resp.status} ${resp.statusText}${detail ? `: ${detail}` : ""}`);
  }
  return resp;
}

export async function fetchStatus(): Promise<Status> {
  const resp = await expectOk(await fetch(url("/api/status")));
  return (await resp.json()) as Status;
}

export async function runPipeline(): Promise<void> {
  await expectOk(await fetch(url("/api/run"), { method: "POST" }));
}

export async function stopPipeline(): Promise<void> {
  await expectOk(await fetch(url("/api/stop"), { method: "POST" }));
}

export async function fetchOutputs(): Promise<string[]> {
  const resp = await expectOk(await fetch(url("/api/outputs")));
  const data = (await resp.json()) as { files: string[] };
  return data.files;
}

export async function fetchOutputHtml(name: string): Promise<string> {
  const resp = await expectOk(await fetch(url(`/outputs/${encodeURIComponent(name)}`)));
  return resp.text();
}

export interface RawOutput {
  name: string;
  markdown: string;
  size_bytes: number;
  updated_at: string;
}

export async function fetchOutputRaw(name: string): Promise<RawOutput> {
  const resp = await expectOk(await fetch(url(`/api/outputs/${encodeURIComponent(name)}/raw`)));
  return (await resp.json()) as RawOutput;
}

export interface ExportItem {
  name: string;
  destination?: string;
  reason?: string;
  error?: string;
}

export interface ExportResult {
  exported: ExportItem[];
  skipped: ExportItem[];
  failed: ExportItem[];
}

export async function chooseExportDirectory(): Promise<string | null> {
  if (!isTauri()) return null;
  const { open } = await import("@tauri-apps/plugin-dialog");
  const selected = await open({
    directory: true,
    multiple: false,
    title: "Choose export folder",
  });
  return typeof selected === "string" ? selected : null;
}

export async function exportOutputs(destination: string, files: string[]): Promise<ExportResult> {
  const resp = await expectOk(
    await fetch(url("/api/exports"), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ destination, files, mode: "copy" }),
    }),
  );
  return (await resp.json()) as ExportResult;
}

export async function openDag(): Promise<string> {
  const resp = await expectOk(await fetch(url("/api/open-dag"), { method: "POST" }));
  return resp.text();
}

export type DagNodeStatus = "locked" | "ready" | "running" | "complete" | "failed";
export type DagNodeReadingStatus = "unread" | "recommended" | "reading" | "read";

export interface DagNode {
  id: string;
  title: string;
  label: string;
  status: DagNodeStatus;
  generation_status: DagNodeStatus;
  reading_status: DagNodeReadingStatus;
  recommended: boolean;
  affected_by_feedback: boolean;
  learning_ready: boolean;
  recommendation_reason: string;
  last_opened_at?: string | null;
  read_at?: string | null;
  last_revised_at?: string | null;
  last_feedback_error?: string | null;
  feedback_count: number;
  defines: string[];
  collections: string[];
  summary: string;
  prerequisites: string[];
  dependents: string[];
  source_order_index: number;
  output_paths: string[];
}

export interface DagEdge {
  from: string;
  to: string;
  relation: string;
  confidence: number;
  required_defines: string[];
}

export interface DagPayload {
  nodes: DagNode[];
  edges: DagEdge[];
  roots: string[];
  learning_ready: boolean;
  stats: {
    nodes: number;
    edges: number;
    statuses: Record<DagNodeStatus, number>;
    reading_statuses: Record<DagNodeReadingStatus, number>;
  };
  updated_at: string;
}

export async function fetchDag(): Promise<DagPayload> {
  const resp = await expectOk(await fetch(url("/api/dag")));
  return (await resp.json()) as DagPayload;
}

export interface LearningNodeState {
  reading_status: DagNodeReadingStatus | "unread";
  last_opened_at?: string | null;
  read_at?: string | null;
  affected_by_feedback: boolean;
  last_revised_at?: string | null;
  last_feedback_error?: string | null;
  feedback_history: Array<Record<string, unknown>>;
}

export async function openLearningNode(nodeId: string): Promise<LearningNodeState> {
  const resp = await expectOk(
    await fetch(url(`/api/learning/nodes/${encodeURIComponent(nodeId)}/open`), { method: "POST" }),
  );
  return ((await resp.json()) as { state: LearningNodeState }).state;
}

export async function markLearningNodeRead(nodeId: string, read = true): Promise<LearningNodeState> {
  const resp = await expectOk(
    await fetch(url(`/api/learning/nodes/${encodeURIComponent(nodeId)}/read`), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ read }),
    }),
  );
  return ((await resp.json()) as { state: LearningNodeState }).state;
}

export interface FeedbackRevisionResult {
  node_id: string;
  output: string;
  status: string;
  backup_path: string;
  revised_at: string;
}

export async function submitLearningFeedback(
  nodeId: string,
  feedback: string,
): Promise<FeedbackRevisionResult> {
  const resp = await expectOk(
    await fetch(url(`/api/learning/nodes/${encodeURIComponent(nodeId)}/feedback`), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ feedback }),
    }),
  );
  return (await resp.json()) as FeedbackRevisionResult;
}

export async function regrowNode(nodeId: string): Promise<void> {
  await expectOk(
    await fetch(url(`/api/nodes/${encodeURIComponent(nodeId)}/regrow`), { method: "POST" }),
  );
}

export async function listMaterials(): Promise<string[]> {
  const resp = await expectOk(await fetch(url("/api/materials")));
  return ((await resp.json()) as { materials: string[] }).materials;
}

export type ImportedFileStatus = "active" | "missing";

export interface ImportedFile {
  id: string;
  original_name: string;
  stored_name: string;
  relative_path: string;
  collection: string;
  size_bytes: number;
  sha256: string;
  imported_at: string;
  status: ImportedFileStatus;
}

export async function fetchImportedFiles(): Promise<ImportedFile[]> {
  const resp = await expectOk(await fetch(url("/api/imported-files")));
  return ((await resp.json()) as { files: ImportedFile[] }).files;
}

export async function uploadMaterials(
  collection: string,
  files: FileList,
): Promise<{ saved: string[]; skipped: string[] }> {
  const form = new FormData();
  form.append("collection", collection || "default");
  for (const file of Array.from(files)) form.append("files", file);
  const resp = await expectOk(await fetch(url("/api/materials"), { method: "POST", body: form }));
  return (await resp.json()) as { saved: string[]; skipped: string[] };
}

export interface EmbeddingState {
  status: string;
  backend: string;
  phase: string;
  detail: string;
}

export async function getEmbedding(): Promise<EmbeddingState> {
  const resp = await expectOk(await fetch(url("/api/embedding")));
  return (await resp.json()) as EmbeddingState;
}

export async function startEmbedding(): Promise<void> {
  await expectOk(await fetch(url("/api/embedding/start"), { method: "POST" }));
}

export async function stopEmbedding(): Promise<void> {
  await expectOk(await fetch(url("/api/embedding/stop"), { method: "POST" }));
}

export interface ExtensionState {
  installed: boolean;
  status: string;
  phase: string;
  progress: number;
  message: string;
  model: string;
  runtime: string;
}

export async function fetchExtension(): Promise<ExtensionState> {
  const resp = await expectOk(await fetch(url("/api/extension")));
  return (await resp.json()) as ExtensionState;
}

export async function installExtension(): Promise<ExtensionState> {
  const resp = await expectOk(await fetch(url("/api/extension/install"), { method: "POST" }));
  return (await resp.json()) as ExtensionState;
}

export type RoleKey = "examiner" | "student" | "writer" | "archivist" | "dagger";
export type RoleModels = Record<RoleKey, string>;

export interface SettingsData {
  config_path: string;
  llm_api_key_configured: boolean;
  llm_base_url: string;
  llm_model: string;
  role_models: RoleModels;
  paddleocr_api_token_configured: boolean;
  paddleocr_api_url: string;
  paddleocr_model: string;
  llama_server_ctx: number;
  source_mtu_chunk_tokens: number;
}

export interface SettingsSave {
  llm_api_key: string;
  llm_base_url: string;
  llm_model: string;
  role_models: RoleModels;
  paddleocr_api_token: string;
  llama_server_ctx: string;
  source_mtu_chunk_tokens: string;
}

export async function fetchSettings(): Promise<SettingsData> {
  const resp = await expectOk(await fetch(url("/api/settings")));
  return (await resp.json()) as SettingsData;
}

export async function saveSettings(fields: SettingsSave): Promise<SettingsData> {
  const resp = await expectOk(
    await fetch(url("/api/settings"), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(fields),
    }),
  );
  return (await resp.json()) as SettingsData;
}

export async function saveSetup(fields: Record<string, string>): Promise<string> {
  const resp = await expectOk(
    await fetch(url("/api/setup"), {
      method: "POST",
      headers: { "Content-Type": "application/x-www-form-urlencoded" },
      body: new URLSearchParams(fields),
    }),
  );
  return resp.text();
}

export function wsUrl(): string {
  const base = apiBase().replace(/^http/, "ws");
  return `${base}/ws/progress?token=${encodeURIComponent(getToken())}`;
}
