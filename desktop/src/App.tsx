import { useEffect, useState } from "react";
import type { ChangeEvent } from "react";
import {
  getToken,
  fetchExtension,
  installExtension,
  openDag,
  runPipeline,
  setToken,
  stopPipeline,
} from "./api";
import type { ExtensionState } from "./api";
import { useProgress } from "./useProgress";
import { ProgressPanel } from "./components/ProgressPanel";
import { Materials } from "./components/Materials";
import { Outputs } from "./components/Outputs";
import { Settings } from "./components/Settings";

type Page = "overview" | "materials" | "outputs" | "settings";

const PAGES: Array<{ key: Page; label: string }> = [
  { key: "overview", label: "Overview" },
  { key: "materials", label: "Materials" },
  { key: "outputs", label: "Outputs" },
  { key: "settings", label: "Settings" },
];

export function App() {
  const [token, setToken] = useState<string>(getToken());
  if (!token) return <TokenGate onToken={setToken} />;
  return (
    <ExtensionGate token={token}>
      <Dashboard token={token} />
    </ExtensionGate>
  );
}

function TokenGate({ onToken }: { onToken: (value: string) => void }) {
  const [value, setValue] = useState<string>("");
  return (
    <div className="gate">
      <h1 className="brand">T.R.E.E.</h1>
      <p className="muted">
        Paste the token from the <code>tre gui</code> URL (the part after <code>?token=</code>).
      </p>
      <input
        value={value}
        onChange={(event: ChangeEvent<HTMLInputElement>) => setValue(event.target.value)}
        placeholder="token"
      />
      <button
        onClick={() => {
          const trimmed = value.trim();
          if (trimmed) {
            setToken(trimmed);
            onToken(trimmed);
          }
        }}
      >
        Connect
      </button>
    </div>
  );
}

function ExtensionGate({ token, children }: { token: string; children: JSX.Element }) {
  const [extension, setExtension] = useState<ExtensionState | null>(null);
  const [busy, setBusy] = useState<boolean>(false);
  const [error, setError] = useState<string>("");

  useEffect(() => {
    let active = true;
    const load = (): void => {
      fetchExtension()
        .then((data) => {
          if (active) setExtension(data);
        })
        .catch((err: unknown) => {
          if (active) setError(String(err));
        });
    };
    load();
    const timer = window.setInterval(() => {
      if (!active) return;
      if (extension?.status === "installing") load();
    }, 1000);
    return () => {
      active = false;
      window.clearInterval(timer);
    };
  }, [token, extension?.status]);

  const install = async (): Promise<void> => {
    setBusy(true);
    setError("");
    try {
      setExtension(await installExtension());
    } catch (err) {
      setError(String(err));
    } finally {
      setBusy(false);
    }
  };

  if (extension?.installed) return children;

  const progress = extension?.progress ?? 0;
  const phase = extension?.phase ?? "checking";
  const message = error || extension?.message || "Checking embedding extension...";

  return (
    <div className="gate extension-gate">
      <h1 className="brand">T.R.E.E.</h1>
      <section className="card">
        <h2>Embedding Extension</h2>
        <p className="muted">
          TREE needs the local embedding extension before the workspace can run.
        </p>
        <div className="extension-status">
          <span className={`pill phase-${phase}`}>{phase}</span>
          <span className={error ? "errors" : "hint"}>{message}</span>
        </div>
        {(extension?.status === "installing" || busy) && (
          <div className="progress-row">
            <div className="progress-track">
              <div className="progress-fill" style={{ width: `${Math.max(5, progress)}%` }} />
            </div>
            <span className="stage-pct">{progress}%</span>
          </div>
        )}
        <button
          onClick={() => void install()}
          disabled={busy || extension?.status === "installing"}
        >
          {extension?.status === "installing" || busy ? "Installing..." : "Install extension"}
        </button>
      </section>
    </div>
  );
}

function Dashboard({ token }: { token: string }) {
  const { status, connected } = useProgress(token);
  const [page, setPage] = useState<Page>("overview");
  const [busy, setBusy] = useState<boolean>(false);
  const [dagMsg, setDagMsg] = useState<string>("");

  const guard = (action: () => Promise<void>) => async (): Promise<void> => {
    setBusy(true);
    try {
      await action();
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="app">
      <header className="bar">
        <span className="brand">T.R.E.E.</span>
        <nav className="tabs" aria-label="Primary">
          {PAGES.map((item) => (
            <button
              key={item.key}
              className={`tab ${page === item.key ? "active" : ""}`}
              onClick={() => setPage(item.key)}
              type="button"
            >
              {item.label}
            </button>
          ))}
        </nav>
        <div className="bar-status">
          {status && (
            <span className={`pill engine-${status.engine}`}>engine: {status.engine}</span>
          )}
          <span className={`conn ${connected ? "on" : "off"}`}>
            {connected ? "live" : "connecting…"}
          </span>
        </div>
      </header>
      <main>
        {page === "overview" && (
          <>
            <section className="card">
              <div className="controls">
                <button onClick={() => void guard(runPipeline)()} disabled={busy}>
                  Run
                </button>
                <button className="ghost" onClick={() => void guard(stopPipeline)()} disabled={busy}>
                  Stop
                </button>
                {status && <span className={`pill phase-${status.phase}`}>{status.phase}</span>}
              </div>
              <ProgressPanel status={status} />
            </section>

            <section className="card">
              <h2>Knowledge DAG</h2>
              <div className="controls">
                <button
                  onClick={() =>
                    void openDag()
                      .then(setDagMsg)
                      .catch((err: unknown) => setDagMsg(String(err)))
                  }
                >
                  Open DAG in viewer
                </button>
                {dagMsg ? (
                  <span className="hint" dangerouslySetInnerHTML={{ __html: dagMsg }} />
                ) : (
                  <span className="hint">Opens knowledge-dag.svg in your system's default app.</span>
                )}
              </div>
            </section>
          </>
        )}
        {page === "materials" && <Materials />}
        {page === "outputs" && <Outputs />}
        {page === "settings" && <Settings />}
      </main>
    </div>
  );
}
