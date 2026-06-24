import { useEffect, useMemo, useState } from "react";
import type { FormEvent } from "react";
import type { AppBootstrap, ProjectSelection, ProjectSummary } from "../api";
import {
  chooseParentTreeArchive,
  chooseProjectArchiveDestination,
  createProject,
  deleteProject,
  exportProjectArchive,
  importParentTreeArchive,
  renameProject,
  selectProject,
  transplantProject,
} from "../api";
import { useT } from "../i18n";
import { FruitTreeMark, OrchardScene } from "./illustrations";

interface ProjectLibraryProps {
  bootstrap: AppBootstrap;
  onProjectReady: (selection: ProjectSelection) => void;
  onBootstrapChange: (bootstrap: AppBootstrap) => void;
  onBack?: () => void;
}

export function ProjectLibrary({
  bootstrap,
  onProjectReady,
  onBootstrapChange,
  onBack,
}: ProjectLibraryProps) {
  const t = useT();
  const [projects, setProjects] = useState<ProjectSummary[]>(bootstrap.projects);
  const [currentProject, setCurrentProject] = useState<ProjectSummary | null>(
    bootstrap.current_project,
  );
  const [name, setName] = useState<string>("");
  const [editingId, setEditingId] = useState<string>("");
  const [uprootId, setUprootId] = useState<string>("");
  const [transplantId, setTransplantId] = useState<string>("");
  const [editName, setEditName] = useState<string>("");
  const [editDescription, setEditDescription] = useState<string>("");
  const [deleteConfirm, setDeleteConfirm] = useState<string>("");
  const [transplantConfirm, setTransplantConfirm] = useState<string>("");
  const [busyId, setBusyId] = useState<string>("");
  const [error, setError] = useState<string>(bootstrap.error ?? "");
  const [message, setMessage] = useState<string>("");

  useEffect(() => {
    setProjects(bootstrap.projects);
    setCurrentProject(bootstrap.current_project);
  }, [bootstrap.projects, bootstrap.current_project]);

  const sortedProjects = useMemo(
    () =>
      [...projects].sort((a, b) => {
        const bTime = b.last_opened_at || b.updated_at || b.created_at;
        const aTime = a.last_opened_at || a.updated_at || a.created_at;
        return bTime - aTime;
      }),
    [projects],
  );

  const applySelection = (selection: ProjectSelection): void => {
    setProjects(selection.projects);
    setCurrentProject(selection.current_project);
    onProjectReady(selection);
  };

  const applyBootstrap = (next: AppBootstrap): void => {
    setProjects(next.projects);
    setCurrentProject(next.current_project);
    onBootstrapChange(next);
  };

  const create = async (event: FormEvent<HTMLFormElement>): Promise<void> => {
    event.preventDefault();
    const trimmed = name.trim();
    if (!trimmed) {
      setError(t("orchard.nameRequired"));
      return;
    }
    setBusyId("create");
    setError("");
    setMessage("");
    try {
      const selection = await createProject(trimmed);
      setName("");
      applySelection(selection);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusyId("");
    }
  };

  const plantParentTree = async (): Promise<void> => {
    setBusyId("import");
    setError("");
    setMessage("");
    try {
      const archivePath = await chooseParentTreeArchive();
      if (!archivePath) return;
      const trimmed = name.trim();
      const selection = await importParentTreeArchive(archivePath, trimmed || undefined);
      setName("");
      applySelection(selection);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusyId("");
    }
  };

  const propagate = async (project: ProjectSummary): Promise<void> => {
    setBusyId(`propagate:${project.id}`);
    setError("");
    setMessage("");
    try {
      const destination = await chooseProjectArchiveDestination(project.name);
      if (!destination) return;
      const result = await exportProjectArchive(project.id, destination);
      setMessage(t("orchard.propagated", { size: formatBytes(result.bytes) }));
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusyId("");
    }
  };

  const observe = async (project: ProjectSummary): Promise<void> => {
    setBusyId(`open:${project.id}`);
    setError("");
    setMessage("");
    try {
      applySelection(await selectProject(project.id));
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusyId("");
    }
  };

  const startRename = (project: ProjectSummary): void => {
    setUprootId("");
    setTransplantId("");
    setEditingId(project.id);
    setEditName(project.name);
    setEditDescription(project.description ?? "");
    setError("");
    setMessage("");
  };

  const saveRename = async (event: FormEvent<HTMLFormElement>, id: string): Promise<void> => {
    event.preventDefault();
    setBusyId(`save:${id}`);
    setError("");
    setMessage("");
    try {
      const next = await renameProject(id, editName, editDescription);
      applyBootstrap(next);
      setEditingId("");
      setMessage(t("orchard.renamed"));
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusyId("");
    }
  };

  const startUproot = (project: ProjectSummary): void => {
    setEditingId("");
    setTransplantId("");
    setUprootId(project.id);
    setDeleteConfirm("");
    setError("");
    setMessage("");
  };

  const startTransplant = (project: ProjectSummary): void => {
    setEditingId("");
    setUprootId("");
    setTransplantId(project.id);
    setTransplantConfirm("");
    setError("");
    setMessage("");
  };

  const uproot = async (project: ProjectSummary): Promise<void> => {
    setBusyId(`delete:${project.id}`);
    setError("");
    setMessage("");
    try {
      const next = await deleteProject(project.id, deleteConfirm);
      applyBootstrap(next);
      setUprootId("");
      setMessage(t("orchard.uprooted"));
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusyId("");
    }
  };

  const transplant = async (project: ProjectSummary): Promise<void> => {
    setBusyId(`transplant:${project.id}`);
    setError("");
    setMessage("");
    try {
      const destination = await chooseProjectArchiveDestination(project.name);
      if (!destination) return;
      const next = await transplantProject(project.id, destination, transplantConfirm);
      applyBootstrap(next);
      setTransplantId("");
      setMessage(t("orchard.transplanted"));
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusyId("");
    }
  };

  return (
    <div className="project-library">
      <header className="project-library-bar">
        <span className="brand">T.R.E.E.</span>
        {onBack && (
          <button className="ghost" type="button" onClick={onBack}>
            {t("orchard.back")}
          </button>
        )}
      </header>
      <main className="project-library-main">
        <section className="orchard-banner">
          <OrchardScene />
          <div className="orchard-banner-text">
            <h1>{t("orchard.title")}</h1>
            <p className="muted">{t("orchard.subtitle")}</p>
          </div>
        </section>

        <form className="project-form project-plant-form" onSubmit={(event) => void create(event)}>
          <input
            value={name}
            maxLength={80}
            onChange={(event) => setName(event.target.value)}
            placeholder={t("orchard.newName")}
            aria-label={t("orchard.newName")}
          />
          <button type="submit" disabled={busyId === "create"}>
            {busyId === "create" ? t("orchard.planting") : t("orchard.fromSeeds")}
          </button>
          <button
            className="ghost"
            type="button"
            onClick={() => void plantParentTree()}
            disabled={busyId === "import"}
          >
            {busyId === "import" ? t("orchard.planting") : t("orchard.fromParentTree")}
          </button>
        </form>

        {error && <div className="errors project-error">{error}</div>}
        {message && <div className="success project-error">{message}</div>}

        {sortedProjects.length === 0 ? (
          <section className="project-empty">
            <h2>{t("orchard.emptyTitle")}</h2>
            <p className="muted">{t("orchard.emptyHint")}</p>
          </section>
        ) : (
          <section className="project-grid" aria-label="Project list">
            {sortedProjects.map((project) => {
              const isCurrent = currentProject?.id === project.id;
              return (
                <article
                  className={`tree-card ${isCurrent ? "selected" : ""}`}
                  key={project.id}
                >
                  <div className="tree-card-head">
                    <FruitTreeMark fruits={project.output_count} size={52} />
                    <div className="tree-card-title">
                      <strong>{project.name}</strong>
                      {isCurrent && <small>{t("orchard.currentTree")}</small>}
                      <span className="muted">{formatProjectDate(project.last_opened_at, t)}</span>
                    </div>
                  </div>

                  <dl className="project-stats">
                    <div>
                      <dt>{t("orchard.imported")}</dt>
                      <dd>{project.source_count}</dd>
                    </div>
                    <div>
                      <dt>{t("orchard.generated")}</dt>
                      <dd>{project.output_count}</dd>
                    </div>
                    <div>
                      <dt>{t("orchard.storage")}</dt>
                      <dd className="stat-small">{formatBytes(project.storage_bytes)}</dd>
                    </div>
                  </dl>

                  {editingId === project.id ? (
                    <form
                      className="tree-edit"
                      onSubmit={(event) => void saveRename(event, project.id)}
                    >
                      <label>
                        {t("orchard.name")}
                        <input
                          value={editName}
                          maxLength={80}
                          onChange={(event) => setEditName(event.target.value)}
                        />
                      </label>
                      <label>
                        {t("orchard.description")}
                        <textarea
                          value={editDescription}
                          maxLength={500}
                          rows={3}
                          onChange={(event) => setEditDescription(event.target.value)}
                        />
                      </label>
                      <div className="tree-actions">
                        <button type="submit" disabled={busyId === `save:${project.id}`}>
                          {busyId === `save:${project.id}` ? t("common.saving") : t("common.save")}
                        </button>
                        <button className="ghost" type="button" onClick={() => setEditingId("")}>
                          {t("common.back")}
                        </button>
                      </div>
                    </form>
                  ) : transplantId === project.id ? (
                    <div className="tree-uproot">
                      <h3>{t("orchard.transplantTitle")}</h3>
                      <p className="muted">{t("orchard.transplantHint")}</p>
                      <input
                        value={transplantConfirm}
                        onChange={(event) => setTransplantConfirm(event.target.value)}
                        placeholder={t("orchard.uprootConfirm", { name: project.name })}
                        aria-label="confirm transplant"
                      />
                      <div className="tree-actions">
                        <button
                          type="button"
                          onClick={() => void transplant(project)}
                          disabled={
                            transplantConfirm !== project.name ||
                            busyId === `transplant:${project.id}`
                          }
                        >
                          {busyId === `transplant:${project.id}`
                            ? t("orchard.transplanting")
                            : t("orchard.transplant")}
                        </button>
                        <button className="ghost" type="button" onClick={() => setTransplantId("")}>
                          {t("common.back")}
                        </button>
                      </div>
                    </div>
                  ) : uprootId === project.id ? (
                    <div className="tree-uproot">
                      <h3>{t("orchard.uprootTitle")}</h3>
                      <p className="muted">{t("orchard.uprootHint")}</p>
                      <input
                        value={deleteConfirm}
                        onChange={(event) => setDeleteConfirm(event.target.value)}
                        placeholder={t("orchard.uprootConfirm", { name: project.name })}
                        aria-label="confirm uproot"
                      />
                      <div className="tree-actions">
                        <button
                          className="danger"
                          type="button"
                          onClick={() => void uproot(project)}
                          disabled={
                            deleteConfirm !== project.name || busyId === `delete:${project.id}`
                          }
                        >
                          {busyId === `delete:${project.id}`
                            ? t("orchard.uprooting")
                            : t("orchard.uproot")}
                        </button>
                        <button className="ghost" type="button" onClick={() => setUprootId("")}>
                          {t("common.back")}
                        </button>
                      </div>
                    </div>
                  ) : (
                    <div className="tree-actions">
                      <button
                        type="button"
                        onClick={() => void observe(project)}
                        disabled={busyId === `open:${project.id}`}
                      >
                        {busyId === `open:${project.id}`
                          ? t("orchard.observing")
                          : t("orchard.observe")}
                      </button>
                      <button className="ghost" type="button" onClick={() => startRename(project)}>
                        {t("orchard.rename")}
                      </button>
                      <button
                        className="ghost"
                        type="button"
                        onClick={() => void propagate(project)}
                        disabled={busyId === `propagate:${project.id}`}
                      >
                        {busyId === `propagate:${project.id}`
                          ? t("orchard.propagating")
                          : t("orchard.propagate")}
                      </button>
                      <button className="ghost" type="button" onClick={() => startTransplant(project)}>
                        {t("orchard.transplant")}
                      </button>
                      <button className="ghost" type="button" onClick={() => startUproot(project)}>
                        {t("orchard.uproot")}
                      </button>
                    </div>
                  )}
                </article>
              );
            })}
          </section>
        )}
      </main>
    </div>
  );
}

function formatProjectDate(seconds: number, t: ReturnType<typeof useT>): string {
  if (!seconds) return t("orchard.neverOpened");
  return `${t("orchard.lastOpened")} ${new Date(seconds * 1000).toLocaleString()}`;
}

function formatBytes(bytes: number): string {
  if (!bytes) return "0 B";
  const units = ["B", "KB", "MB", "GB", "TB"];
  let value = bytes;
  let unit = 0;
  while (value >= 1024 && unit < units.length - 1) {
    value /= 1024;
    unit += 1;
  }
  return `${value >= 10 || unit === 0 ? value.toFixed(0) : value.toFixed(1)} ${units[unit]}`;
}
