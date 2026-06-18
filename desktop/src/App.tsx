import { useEffect, useState } from "react";
import type { ChangeEvent } from "react";
import {
  getToken,
  openDag,
  runPipeline,
  setToken,
  startEmbedding,
  stopEmbedding,
  stopPipeline,
} from "./api";
import { useProgress } from "./useProgress";
import { ProgressPanel } from "./components/ProgressPanel";
import { Materials } from "./components/Materials";
import { Outputs } from "./components/Outputs";
import { SetupForm } from "./components/SetupForm";

export function App() {
  const [token, setToken] = useState<string>(getToken());
  if (!token) return <TokenGate onToken={setToken} />;
  return <Dashboard token={token} />;
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

function Dashboard({ token }: { token: string }) {
  const { status, connected } = useProgress(token);
  const [busy, setBusy] = useState<boolean>(false);
  const [dagMsg, setDagMsg] = useState<string>("");

  // Auto-start the embedding model when the app opens (first run downloads it).
  useEffect(() => {
    void startEmbedding().catch(() => undefined);
  }, []);

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
        {status && (
          <span className={`pill engine-${status.engine}`}>engine: {status.engine}</span>
        )}
        <span className={`conn ${connected ? "on" : "off"}`}>
          {connected ? "live" : "connecting…"}
        </span>
      </header>
      <main>
        <section className="card">
          <div className="controls">
            <button onClick={() => void guard(runPipeline)()} disabled={busy}>
              Run
            </button>
            <button className="ghost" onClick={() => void guard(stopPipeline)()} disabled={busy}>
              Stop
            </button>
            {status && <span className={`pill phase-${status.phase}`}>{status.phase}</span>}
            {status && (
              <span className="kv">
                embed <b>{status.embedding_server}</b>
              </span>
            )}
            <button
              className="ghost"
              onClick={() => void startEmbedding().catch(() => undefined)}
            >
              Start embedding
            </button>
            <button className="ghost" onClick={() => void stopEmbedding().catch(() => undefined)}>
              Stop embedding
            </button>
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

        <section className="grid">
          <Materials />
          <Outputs />
        </section>

        <SetupForm />
      </main>
    </div>
  );
}
