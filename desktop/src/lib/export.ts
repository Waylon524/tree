import { chooseExportDirectory, isTauri } from "../api";

// Pick an export destination folder: a native dialog in the Tauri shell, a
// prompt in the browser. Shared by the Fruits list and the Reader.
export async function chooseExportDestination(): Promise<string | null> {
  if (isTauri()) return chooseExportDirectory();
  return window.prompt("Export destination folder path")?.trim() || null;
}
