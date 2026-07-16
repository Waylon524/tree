export type RecommendationReasonCode =
  | "root_ready"
  | "prerequisites_read"
  | "suggested_start";

const REASON_CODES = new Set<RecommendationReasonCode>([
  "root_ready",
  "prerequisites_read",
  "suggested_start",
]);

const LEGACY_REASON_CODES: Record<string, RecommendationReasonCode> = {
  "Root node; ready to start.": "root_ready",
  "All prerequisite nodes have been read.": "prerequisites_read",
  "Suggested starting point.": "suggested_start",
};

export function recommendationReasonCode(reason: unknown): RecommendationReasonCode | null {
  if (typeof reason === "string") return LEGACY_REASON_CODES[reason] ?? null;
  if (!reason || typeof reason !== "object") return null;
  const code = (reason as { code?: unknown }).code;
  return typeof code === "string" && REASON_CODES.has(code as RecommendationReasonCode)
    ? (code as RecommendationReasonCode)
    : null;
}
