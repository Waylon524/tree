import type { ExtensionState } from "../api";
import type { Status } from "../types";

export type GrowBlockReason = "loading" | "extension" | "materials" | null;

export function getGrowBlockReason(
  status: Status | null,
  extension: ExtensionState | null,
): GrowBlockReason {
  if (!status) return "loading";
  if (!extension?.installed) return "extension";
  if (status.materials === 0) return "materials";
  return null;
}
