import { lazy, Suspense, useEffect, useRef, useState } from "react";
import type { ChangeEvent } from "react";
import {
  type AppBootstrap,
  getToken,
  fetchExtension,
  installExtension,
  type ProjectSelection,
  type ProjectSummary,
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
import { ProjectLibrary } from "./components/ProjectLibrary";
import type { ReaderTarget } from "./components/Reader";

type Page = "overview" | "materials" | "outputs" | "dag" | "reader" | "settings";

const LazyDagWorkbench = lazy(() =>
  import("./components/DagWorkbench").then((module) => ({
    default: module.DagWorkbench,
  })),
);

const LazyReader = lazy(() =>
  import("./components/Reader").then((module) => ({
    default: module.Reader,
  })),
);

const PAGES: Array<{ key: Page; label: string }> = [
  { key: "overview", label: "Overview" },
  { key: "materials", label: "Imported Files" },
  { key: "outputs", label: "Generated Files" },
  { key: "dag", label: "知识图谱" },
  { key: "settings", label: "Settings" },
];

export function App({ initialBootstrap }: { initialBootstrap: AppBootstrap | null }) {
  const [bootstrap, setBootstrap] = useState<AppBootstrap | null>(initialBootstrap);
  const [showProjects, setShowProjects] = useState<boolean>(
    Boolean(initialBootstrap?.tauri && !initialBootstrap.api),
  );
  const [token, setTokenValue] = useState<string>(getToken());
  const isDesktop = Boolean(bootstrap?.tauri);

  const handleProjectReady = (selection: ProjectSelection): void => {
    setBootstrap({
      tauri: true,
      projects: selection.projects,
      current_project: selection.current_project,
      api: selection.api,
      error: null,
    });
    setTokenValue(selection.api.token);
    setShowProjects(false);
  };

  const handleBootstrapChange = (next: AppBootstrap): void => {
    setBootstrap(next);
    setTokenValue(next.api?.token ?? "");
  };

  if (isDesktop && (showProjects || !bootstrap?.api)) {
    return (
      <ProjectLibrary
        bootstrap={
          bootstrap ?? {
            tauri: true,
            projects: [],
            current_project: null,
            api: null,
            error: null,
          }
        }
        onProjectReady={handleProjectReady}
        onBootstrapChange={handleBootstrapChange}
        onBack={bootstrap?.api ? () => setShowProjects(false) : undefined}
      />
    );
  }

  if (!token) return <TokenGate onToken={setTokenValue} />;
  return (
    <ExtensionGate token={token}>
      <Dashboard
        token={token}
        project={bootstrap?.current_project ?? null}
        onSwitchProject={isDesktop ? () => setShowProjects(true) : undefined}
      />
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
  const failures = useRef<number>(0);
  const shouldPoll = !extension || extension.status === "installing" || Boolean(error);

  useEffect(() => {
    let active = true;
    const load = (): void => {
      fetchExtension()
        .then((data) => {
          if (!active) return;
          failures.current = 0;
          setError("");
          setExtension(data);
        })
        .catch((err: unknown) => {
          if (!active) return;
          failures.current += 1;
          const detail = err instanceof Error ? err.message : String(err);
          const transient =
            detail.includes("Load failed") ||
            detail.includes("Failed to fetch") ||
            detail.includes("NetworkError");
          if (transient && failures.current < 10) {
            setError("Starting local TREE service. Retrying...");
          } else {
            setError(`Could not connect to local TREE service. ${detail}`);
          }
        });
    };
    load();
    const timer = shouldPoll ? window.setInterval(load, 1000) : undefined;
    return () => {
      active = false;
      if (timer) window.clearInterval(timer);
    };
  }, [token, shouldPoll]);

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

function Dashboard({
  token,
  project,
  onSwitchProject,
}: {
  token: string;
  project: ProjectSummary | null;
  onSwitchProject?: () => void;
}) {
  const { status, connected } = useProgress(token);
  const [page, setPage] = useState<Page>("overview");
  const [readerTarget, setReaderTarget] = useState<ReaderTarget | null>(null);
  const [selectedDagNodeId, setSelectedDagNodeId] = useState<string>("");
  const [busy, setBusy] = useState<boolean>(false);

  const guard = (action: () => Promise<void>) => async (): Promise<void> => {
    setBusy(true);
    try {
      await action();
    } finally {
      setBusy(false);
    }
  };

  const openReader = (target: ReaderTarget): void => {
    setReaderTarget(target);
    if (target.nodeId) setSelectedDagNodeId(target.nodeId);
    setPage("reader");
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
        {project && onSwitchProject && (
          <button className="project-chip" type="button" onClick={onSwitchProject}>
            <span>{project.name}</span>
            <small>Switch</small>
          </button>
        )}
        <div className="bar-status">
          {status && (
            <span className={`pill engine-${status.engine}`}>engine: {status.engine}</span>
          )}
          <span className={`conn ${connected ? "on" : "off"}`}>
            {connected ? "live" : "connecting…"}
          </span>
        </div>
      </header>
      <main className={page === "dag" ? "main-dag" : page === "reader" ? "main-reader" : undefined}>
        {page === "overview" && (
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
        )}
        {page === "materials" && <Materials />}
        {page === "outputs" && (
          <Outputs onReadOutput={(name) => openReader({ name, from: "outputs" })} />
        )}
        {page === "dag" && (
          <Suspense fallback={<DagLoading />}>
            <LazyDagWorkbench
              selectedNodeId={selectedDagNodeId}
              onSelectedNodeChange={setSelectedDagNodeId}
              onReadOutput={(name, nodeId) => openReader({ name, from: "dag", nodeId })}
            />
          </Suspense>
        )}
        {page === "reader" && readerTarget && (
          <Suspense fallback={<ReaderLoading />}>
            <LazyReader
              target={readerTarget}
              onBackToDag={() => setPage("dag")}
              onBackToOutputs={() => setPage("outputs")}
            />
          </Suspense>
        )}
        {page === "settings" && <Settings />}
      </main>
    </div>
  );
}

function DagLoading() {
  return (
    <section className="dag-loading" aria-label="Loading knowledge graph">
      <span className="pill">Loading Graph</span>
    </section>
  );
}

function ReaderLoading() {
  return (
    <section className="card" aria-label="Loading reader">
      <span className="pill">Loading Reader</span>
    </section>
  );
}
