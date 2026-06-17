import type { Status } from "./types";

// The `tre gui` server prints a URL like http://127.0.0.1:8799/?token=XXXX.
// In dev, point VITE_TREE_API at that origin and open this app with ?token=XXXX.
export const API_BASE: string =
  (import.meta.env.VITE_TREE_API as string | undefined)?.replace(/\/$/, "") ??
  "http://127.0.0.1:8799";

export function getToken(): string {
  const fromUrl = new URL(window.location.href).searchParams.get("token");
  if (fromUrl) return fromUrl;
  return (import.meta.env.VITE_TREE_TOKEN as string | undefined) ?? "";
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

export function dagUrl(): string {
  return url("/dag.svg");
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
