import type { Status, StageRow } from "../types";
import { useT } from "../i18n";
import { StageGlyph } from "./illustrations";
import type { StageKey } from "./illustrations";

const STAGE_KEYS: StageKey[] = ["ocr", "clean", "cut", "embed", "cluster", "link", "noderun"];

function isStageKey(value: string | undefined): value is StageKey {
  return Boolean(value) && STAGE_KEYS.includes(value as StageKey);
}

export function ProgressPanel({ status }: { status: Status | null }) {
  const t = useT();
  if (!status) return <p className="muted">{t("grow.waiting")}</p>;
  const hasErrors = status.errors.length > 0;
  return (
    <div>
      <div className="status">
        <span className="kv">
          {t("grow.materials")} <b>{status.materials}</b>
        </span>
        <span className="kv">
          {t("grow.nodes")} <b>{status.nodes}</b>
        </span>
        <span className="kv">
          {t("grow.edges")} <b>{status.edges}</b>
        </span>
        <span className="kv">
          {t("grow.active")} <b>{status.active}</b>
        </span>
      </div>
      <div className="stages" role="list">
        {status.rows.map((row) => (
          <StageRowView key={row.key ?? row.label} row={row} t={t} />
        ))}
      </div>
      {hasErrors ? (
        <div className="errors" role="alert">
          <div className="errors-title">{t("grow.errors")}</div>
          <p>{t("grow.errorHint")}</p>
          <ul>
            {status.errors.map((err, i) => (
              <ErrorItem key={i} error={err} t={t} />
            ))}
          </ul>
        </div>
      ) : null}
    </div>
  );
}

function ErrorItem({ error, t }: { error: string; t: ReturnType<typeof useT> }) {
  const marker = " — Action: ";
  const markerIndex = error.indexOf(marker);
  if (markerIndex < 0) return <li>{error}</li>;

  const detail = error.slice(0, markerIndex);
  const rawAction = error.slice(markerIndex + marker.length);
  const genericAction = rawAction.match(
    /^Fix the reported (\w+) input or dependency, then resume the pipeline\.$/,
  );
  const actionStage = genericAction && isStageKey(genericAction[1]) ? genericAction[1] : null;
  const action = actionStage
    ? t("grow.fixStageAction", { stage: t(`stage.${actionStage}`) })
    : rawAction;
  return (
    <li className="error-item">
      <span>{detail}</span>
      <span className="error-action">
        <b>{t("grow.actionLabel")}</b>
        {action}
      </span>
    </li>
  );
}

function StageRowView({ row, t }: { row: StageRow; t: ReturnType<typeof useT> }) {
  const key = row.key;
  const stageKey = isStageKey(key) ? key : null;
  const label = stageKey ? t(`stage.${stageKey}`) : row.label;
  const tip = stageKey ? t(`stage.${stageKey}.tip`) : "";
  const badgeKey = ["done", "running", "failed", "partial", "wait"].includes(row.badge)
    ? `badge.${row.badge}`
    : "";
  return (
    <div className={`stage-row stage-row-${row.badge}`} role="listitem">
      <div className="stage-name">
        <span className="stage-name-cell" title={tip}>
          {stageKey && (
            <span className={`stage-glyph glyph-${row.badge}`}>
              <StageGlyph stage={stageKey} size={17} />
            </span>
          )}
          {label}
        </span>
      </div>
      <div className="stage-bar">
        <div className="track">
          <div className={`fill badge-${row.badge}`} style={{ width: `${row.pct}%` }} />
        </div>
      </div>
      <div className="stage-pct">{row.pct}%</div>
      <div className="stage-count">
        {row.done}/{row.total}
      </div>
      <div className="stage-badge">
        <span className={`badge badge-${row.badge}`}>
          {badgeKey ? t(badgeKey) : row.badge}
        </span>
      </div>
    </div>
  );
}
