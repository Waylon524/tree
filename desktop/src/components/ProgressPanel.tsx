import type { Status } from "../types";

export function ProgressPanel({ status }: { status: Status | null }) {
  if (!status) return <p className="muted">Waiting for status…</p>;
  return (
    <div>
      <div className="status">
        <span className="kv">
          materials <b>{status.materials}</b>
        </span>
        <span className="kv">
          nodes <b>{status.nodes}</b>
        </span>
        <span className="kv">
          edges <b>{status.edges}</b>
        </span>
        <span className="kv">
          active <b>{status.active}</b>
        </span>
        <span className="kv">
          embed <b>{status.embedding_server}</b> · {status.embedding_backend}
        </span>
      </div>
      {status.message && <p className="muted">{status.message}</p>}
      <table className="stages">
        <tbody>
          {status.rows.map((row) => (
            <tr key={row.label}>
              <td className="stage-name">{row.label}</td>
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
          ))}
        </tbody>
      </table>
      {status.errors.length > 0 && (
        <div className="errors">
          <b>Errors</b>
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
