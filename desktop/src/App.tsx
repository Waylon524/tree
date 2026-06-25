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
import { useT } from "./i18n";
import { useProgress } from "./useProgress";
import { ProgressPanel } from "./components/ProgressPanel";
import { Outputs } from "./components/Outputs";
import { Settings } from "./components/Settings";
import { ProjectLibrary } from "./components/ProjectLibrary";
import { Seedling } from "./components/illustrations";
import type { ReaderTarget } from "./components/Reader";

type Page = "grow" | "fruits" | "harvest" | "reader" | "tend";

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

const NAV: Array<{ key: Page; labelKey: string }> = [
  { key: "grow", labelKey: "nav.grow" },
  { key: "fruits", labelKey: "nav.fruits" },
  { key: "harvest", labelKey: "nav.harvest" },
  { key: "tend", labelKey: "nav.tend" },
];

const ENGINE_STATES = ["running", "stopped", "starting"];
const PHASE_STATES = ["idle", "running", "blocked", "complete", "failed"];

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
  const t = useT();
  const [value, setValue] = useState<string>("");
  return (
    <div className="gate">
      <h1 className="brand">T.R.E.E.</h1>
      <p className="muted">{t("gate.token.desc")}</p>
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
        {t("gate.token.connect")}
      </button>
    </div>
  );
}

function ExtensionGate({ token, children }: { token: string; children: JSX.Element }) {
  const t = useT();
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
            setError(t("gate.soil.starting"));
          } else {
            setError(`${t("gate.soil.failed")} ${detail}`);
          }
        });
    };
    load();
    const timer = shouldPoll ? window.setInterval(load, 1000) : undefined;
    return () => {
      active = false;
      if (timer) window.clearInterval(timer);
    };
  }, [token, shouldPoll, t]);

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
  const message = error || extension?.message || t("gate.soil.checking");

  return (
    <div className="gate extension-gate">
      <h1 className="brand">T.R.E.E.</h1>
      <section className="card soil-card">
        <Seedling />
        <div>
          <h2>{t("gate.soil.title")}</h2>
          <p className="muted">{t("gate.soil.desc")}</p>
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
          <button onClick={() => void install()} disabled={busy || extension?.status === "installing"}>
            {extension?.status === "installing" || busy
              ? t("gate.soil.installing")
              : t("gate.soil.install")}
          </button>
        </div>
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
  const t = useT();
  const { status, connected } = useProgress(token);
  const [page, setPage] = useState<Page>("grow");
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
          {NAV.map((item) => (
            <button
              key={item.key}
              className={`tab ${page === item.key ? "active" : ""}`}
              onClick={() => setPage(item.key)}
              type="button"
            >
              {t(item.labelKey)}
            </button>
          ))}
        </nav>
        {project && onSwitchProject && (
          <button className="project-chip" type="button" onClick={onSwitchProject}>
            <span>{project.name}</span>
            <small>{t("common.switch")}</small>
          </button>
        )}
        <div className="bar-status">
          {status && (
            <span className={`pill engine-${status.engine}`}>
              {t("engine.label")}:{" "}
              {ENGINE_STATES.includes(status.engine) ? t(`engine.${status.engine}`) : status.engine}
            </span>
          )}
          <span className={`conn ${connected ? "on" : "off"}`}>
            {connected ? t("conn.live") : t("conn.connecting")}
          </span>
        </div>
      </header>
      <main className={page === "harvest" ? "main-dag" : page === "reader" ? "main-reader" : undefined}>
        {page === "grow" && (
          <section className="card grow-card">
            <div className="grow-head">
              <div>
                <h2 className="grow-title">{t("grow.heading")}</h2>
                <p className="muted">{t("grow.subtitle")}</p>
              </div>
              {status && (
                <span className={`pill phase-${status.phase}`}>
                  {PHASE_STATES.includes(status.phase) ? t(`phase.${status.phase}`) : status.phase}
                </span>
              )}
            </div>
            <div className="controls">
              <button onClick={() => void guard(runPipeline)()} disabled={busy}>
                {t("grow.grow")}
              </button>
              <button className="ghost" onClick={() => void guard(stopPipeline)()} disabled={busy}>
                {t("grow.rest")}
              </button>
            </div>
            <ProgressPanel status={status} />
          </section>
        )}
        {page === "fruits" && (
          <Outputs onReadOutput={(name) => openReader({ name, from: "outputs" })} />
        )}
        {page === "harvest" && (
          <Suspense fallback={<DagLoading />}>
            <LazyDagWorkbench
              status={status}
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
              onBackToDag={() => setPage("harvest")}
              onBackToOutputs={() => setPage("fruits")}
            />
          </Suspense>
        )}
        {page === "tend" && <Settings />}
      </main>
    </div>
  );
}

function DagLoading() {
  const t = useT();
  return (
    <section className="dag-loading" aria-label="Loading knowledge graph">
      <span className="pill">{t("common.loading")}</span>
    </section>
  );
}

function ReaderLoading() {
  const t = useT();
  return (
    <section className="card" aria-label="Loading reader">
      <span className="pill">{t("common.loading")}</span>
    </section>
  );
}
