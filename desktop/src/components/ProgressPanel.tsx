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
        <span className="kv">
          {t("grow.embed")} <b>{status.embedding_server}</b> · {status.embedding_backend}
        </span>
      </div>
      {status.message && <p className="muted">{status.message}</p>}
      <table className="stages">
        <tbody>
          {status.rows.map((row) => (
            <StageRowView key={row.key ?? row.label} row={row} t={t} />
          ))}
        </tbody>
      </table>
      {status.errors.length > 0 && (
        <div className="errors">
          <b>{t("grow.errors")}</b>
          <ul>
            {status.errors.map((err, i) => (
              <li key={i}>{err}</li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}

function StageRowView({ row, t }: { row: StageRow; t: ReturnType<typeof useT> }) {
  const key = row.key;
  const stageKey = isStageKey(key) ? key : null;
  const label = stageKey ? t(`stage.${stageKey}`) : row.label;
  const tip = stageKey ? t(`stage.${stageKey}.tip`) : "";
  return (
    <tr>
      <td className="stage-name">
        <span className="stage-name-cell" title={tip}>
          {stageKey && (
            <span className={`stage-glyph glyph-${row.badge}`}>
              <StageGlyph stage={stageKey} size={17} />
            </span>
          )}
          {label}
        </span>
      </td>
      <td className="stage-bar">
        <div className="track">
          <div className={`fill badge-${row.badge}`} style={{ width: `${row.pct}%` }} />
        </div>
      </td>
      <td className="stage-pct">{row.pct}%</td>
      <td className="stage-count">
        {row.done}/{row.total}
      </td>
      <td>
        <span className={`badge badge-${row.badge}`}>{row.badge}</span>
      </td>
      <td className="stage-current" title={row.current}>
        {row.current}
      </td>
    </tr>
  );
}
