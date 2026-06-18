import type { Status } from "./types";

// The `tre gui` server prints a URL like http://127.0.0.1:8799/?token=XXXX.
// In dev, point VITE_TREE_API at that origin and open this app with ?token=XXXX.
export const API_BASE: string =
  (import.meta.env.VITE_TREE_API as string | undefined)?.replace(/\/$/, "") ??
  "http://127.0.0.1:8799";

const TOKEN_KEY = "tree_token";

// Token resolution order: URL ?token= (the `tre gui` deep-link), then a token
// entered via the connect screen (persisted), then a build-time env var.
export function getToken(): string {
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
  try {
    sessionStorage.setItem(TOKEN_KEY, value);
  } catch {
    /* sessionStorage unavailable */
  }
}

function url(path: string): string {
  const u = new URL(API_BASE + path);
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
  const base = API_BASE.replace(/^http/, "ws");
  return `${base}/ws/progress?token=${encodeURIComponent(getToken())}`;
}
