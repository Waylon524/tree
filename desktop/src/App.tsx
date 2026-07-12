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
import { Button } from "./components/ui/Button";
import { Message } from "./components/ui/Message";
import type { ReaderTarget } from "./components/Reader";
import { getGrowBlockReason } from "./lib/grow";

type Page = "grow" | "fruits" | "harvest" | "reader" | "tend";

const loadDagWorkbench = () =>
  import("./components/DagWorkbench").then((module) => ({
    default: module.DagWorkbench,
  }));

const LazyDagWorkbench = lazy(loadDagWorkbench);

const loadReader = () =>
  import("./components/Reader").then((module) => ({
    default: module.Reader,
  }));

const LazyReader = lazy(loadReader);

const NAV: Array<{ key: Page; labelKey: string }> = [
  { key: "grow", labelKey: "nav.grow" },
  { key: "fruits", labelKey: "nav.fruits" },
  { key: "harvest", labelKey: "nav.harvest" },
  { key: "tend", labelKey: "nav.tend" },
];

const ENGINE_STATES = ["running", "stopped", "starting"];
const PHASE_STATES = ["idle", "running", "blocked", "complete", "failed"];
const EXTENSION_PHASE_KEYS: Record<string, string> = {
  checking: "gate.soil.phase.checking",
  missing: "gate.soil.phase.missing",
  preparing: "gate.soil.phase.preparing",
  "downloading-runtime": "gate.soil.phase.runtime",
  "downloading-model": "gate.soil.phase.model",
  failed: "gate.soil.phase.failed",
};

interface ExtensionGateState {
  extension: ExtensionState | null;
  openSetup: () => void;
}

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
      {({ extension, openSetup }) => (
        <Dashboard
          token={token}
          project={bootstrap?.current_project ?? null}
          extension={extension}
          onPrepareExtension={openSetup}
          onSwitchProject={isDesktop ? () => setShowProjects(true) : undefined}
        />
      )}
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
      <Button
        onClick={() => {
          const trimmed = value.trim();
          if (trimmed) {
            setToken(trimmed);
            onToken(trimmed);
          }
        }}
      >
        {t("gate.token.connect")}
      </Button>
    </div>
  );
}

function ExtensionGate({
  token,
  children,
}: {
  token: string;
  children: (state: ExtensionGateState) => JSX.Element;
}) {
  const t = useT();
  const [extension, setExtension] = useState<ExtensionState | null>(null);
  const [busy, setBusy] = useState<boolean>(false);
  const [error, setError] = useState<string>("");
  const [browsing, setBrowsing] = useState<boolean>(false);
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

  if (extension?.installed || browsing) {
    return children({ extension, openSetup: () => setBrowsing(false) });
  }

  const progress = extension?.progress ?? 0;
  const phase = extension?.phase ?? "checking";
  const phaseKey = EXTENSION_PHASE_KEYS[phase] ?? "gate.soil.phase.checking";
  const message = error || t(phaseKey);

  return (
    <div className="gate extension-gate">
      <h1 className="brand">T.R.E.E.</h1>
      <section className="card soil-card">
        <Seedling />
        <div>
          <h2>{t("gate.soil.title")}</h2>
          <p className="muted">{t("gate.soil.desc")}</p>
          <p className="hint">{t("gate.soil.downloadNote")}</p>
          <div className="extension-status">
            <span className={`pill phase-${phase}`}>{t(phaseKey)}</span>
            <span className={error ? "errors" : "hint"}>{message}</span>
          </div>
          <div className="extension-requirements" aria-label={t("gate.soil.requirements")}>
            <span>
              {t("gate.soil.model")}: {extension?.model === "cached" ? t("common.ready") : t("common.required")}
            </span>
            <span>
              {t("gate.soil.runtime")}: {extension?.runtime && extension.runtime !== "missing" ? t("common.ready") : t("common.required")}
            </span>
          </div>
          {(extension?.status === "installing" || busy) && (
            <div className="progress-row">
              <div className="progress-track">
                <div className="progress-fill" style={{ width: `${Math.max(5, progress)}%` }} />
              </div>
              <span className="stage-pct">{progress}%</span>
            </div>
          )}
          <div className="gate-actions">
            <Button onClick={() => void install()} disabled={busy || extension?.status === "installing"}>
              {extension?.status === "installing" || busy
                ? t("gate.soil.installing")
                : extension?.status === "failed"
                  ? t("common.retry")
                  : t("gate.soil.install")}
            </Button>
            <Button variant="ghost" onClick={() => setBrowsing(true)} disabled={busy}>
              {t("gate.soil.browse")}
            </Button>
          </div>
        </div>
      </section>
    </div>
  );
}

function Dashboard({
  token,
  project,
  extension,
  onPrepareExtension,
  onSwitchProject,
}: {
  token: string;
  project: ProjectSummary | null;
  extension: ExtensionState | null;
  onPrepareExtension: () => void;
  onSwitchProject?: () => void;
}) {
  const t = useT();
  const { status, connected } = useProgress(token);
  const [page, setPage] = useState<Page>("grow");
  const [readerTarget, setReaderTarget] = useState<ReaderTarget | null>(null);
  const [selectedDagNodeId, setSelectedDagNodeId] = useState<string>("");
  const [busy, setBusy] = useState<boolean>(false);
  const [actionError, setActionError] = useState<string>("");
  const growBlockReason = getGrowBlockReason(status, extension);
  const engineActive = status?.engine === "running" || status?.engine === "starting";

  const runAction = async (action: () => Promise<void>): Promise<void> => {
    setBusy(true);
    setActionError("");
    try {
      await action();
    } catch (err) {
      setActionError(err instanceof Error ? err.message : String(err));
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
        <nav className="tabs" aria-label={t("nav.primary")}>
          {NAV.map((item) => (
            <button
              key={item.key}
              className={`tab ${page === item.key ? "active" : ""}`}
              onClick={() => setPage(item.key)}
              onFocus={item.key === "harvest" ? () => void loadDagWorkbench() : undefined}
              onMouseEnter={item.key === "harvest" ? () => void loadDagWorkbench() : undefined}
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
              <Button
                onClick={() => void runAction(runPipeline)}
                disabled={busy || Boolean(growBlockReason) || engineActive}
              >
                {t("grow.grow")}
              </Button>
              <Button
                variant="ghost"
                onClick={() => void runAction(stopPipeline)}
                disabled={busy || !engineActive}
              >
                {t("grow.rest")}
              </Button>
            </div>
            {growBlockReason === "extension" && (
              <div className="grow-readiness">
                <Message kind="hint">{t("grow.needsExtension")}</Message>
                <Button variant="ghost" onClick={onPrepareExtension}>
                  {t("gate.soil.install")}
                </Button>
              </div>
            )}
            {growBlockReason === "materials" && (
              <div className="grow-readiness">
                <Message kind="hint">{t("grow.needsMaterials")}</Message>
                <Button variant="ghost" onClick={() => setPage("tend")}>
                  {t("grow.goTend")}
                </Button>
              </div>
            )}
            {actionError && <Message kind="error">{actionError}</Message>}
            <ProgressPanel status={status} />
          </section>
        )}
        {page === "fruits" && (
          <Outputs
            onReadOutput={(name) => openReader({ name, from: "outputs" })}
            onPrepare={() => setPage("tend")}
          />
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
    <section className="dag-loading" aria-label={t("harvest.loading")}>
      <span className="pill">{t("common.loading")}</span>
    </section>
  );
}

function ReaderLoading() {
  const t = useT();
  return (
    <section className="card" aria-label={t("reader.loading")}>
      <span className="pill">{t("common.loading")}</span>
    </section>
  );
}
