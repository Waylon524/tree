import type { Status } from "./types";

const TOKEN_KEY = "tree_token";

// API base + token are resolved at runtime. In the Tauri shell they come from the
// `api_config` command (the shell spawned the sidecar and owns the token); in the
// browser they come from VITE_TREE_API + the URL/connect-screen token.
let resolvedBase: string | null = null;
let resolvedToken: string | null = null;

function isTauri(): boolean {
  return typeof window !== "undefined" && "__TAURI_INTERNALS__" in window;
}

// Call once at startup. Inside Tauri this fetches the sidecar base+token so the
// connect screen is skipped; in the browser it's a no-op.
export async function initApi(): Promise<void> {
  if (!isTauri()) return;
  try {
    const { invoke } = await import("@tauri-apps/api/core");
    const config = (await invoke("api_config")) as { base: string; token: string };
    resolvedBase = config.base.replace(/\/$/, "");
    resolvedToken = config.token;
  } catch {
    /* fall back to browser resolution */
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

function url(path: string): string {
  const u = new URL(apiBase() + path);
  u.searchParams.set("token", getToken());
  return u.toString();
}

async function expectOk(resp: Response): Promise<Response> {
  if (!resp.ok) throw new Error(`${resp.status} ${resp.statusText}`);
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

export async function openDag(): Promise<string> {
  const resp = await expectOk(await fetch(url("/api/open-dag"), { method: "POST" }));
  return resp.text();
}

export async function listMaterials(): Promise<string[]> {
  const resp = await expectOk(await fetch(url("/api/materials")));
  return ((await resp.json()) as { materials: string[] }).materials;
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
}

export interface SettingsSave {
  llm_api_key: string;
  llm_base_url: string;
  llm_model: string;
  role_models: RoleModels;
  paddleocr_api_token: string;
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
