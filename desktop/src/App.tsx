import { useState } from "react";
import type { ChangeEvent } from "react";
import { dagUrl, getToken, runPipeline, stopPipeline } from "./api";
import { useProgress } from "./useProgress";
import { ProgressPanel } from "./components/ProgressPanel";
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
      <button onClick={() => value.trim() && onToken(value.trim())}>Connect</button>
    </div>
  );
}

function Dashboard({ token }: { token: string }) {
  const { status, connected } = useProgress(token);
  const [busy, setBusy] = useState<boolean>(false);

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
          </div>
          <ProgressPanel status={status} />
        </section>

        <section className="card">
          <h2>Knowledge DAG</h2>
          <object data={dagUrl()} type="image/svg+xml" className="dag">
            <p className="muted">DAG not generated yet.</p>
          </object>
        </section>

        <section className="grid">
          <Outputs />
          <SetupForm />
        </section>
      </main>
    </div>
  );
}
