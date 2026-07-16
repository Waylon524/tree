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
import { formatBytes } from "../lib/format";
import { FruitTreeMark, OrchardScene } from "./illustrations";
import { Button } from "./ui/Button";
import { ConfirmByName } from "./ui/ConfirmByName";
import { Menu } from "./ui/Menu";
import { Message } from "./ui/Message";

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
    setError("");
    setMessage("");
  };

  const startTransplant = (project: ProjectSummary): void => {
    setEditingId("");
    setUprootId("");
    setTransplantId(project.id);
    setError("");
    setMessage("");
  };

  const uproot = async (project: ProjectSummary): Promise<void> => {
    setBusyId(`delete:${project.id}`);
    setError("");
    setMessage("");
    try {
      const next = await deleteProject(project.id, project.name);
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
      const next = await transplantProject(project.id, destination, project.name);
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
          <Button variant="ghost" onClick={onBack}>
            {t("orchard.back")}
          </Button>
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
          <Button type="submit" disabled={busyId === "create"}>
            {busyId === "create" ? t("orchard.planting") : t("orchard.fromSeeds")}
          </Button>
          <Button
            variant="ghost"
            onClick={() => void plantParentTree()}
            disabled={busyId === "import"}
          >
            {busyId === "import" ? t("orchard.planting") : t("orchard.fromParentTree")}
          </Button>
        </form>

        {error && (
          <Message kind="error" className="project-error">
            {error}
          </Message>
        )}
        {message && (
          <Message kind="success" className="project-error">
            {message}
          </Message>
        )}

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
                        <Button type="submit" disabled={busyId === `save:${project.id}`}>
                          {busyId === `save:${project.id}` ? t("common.saving") : t("common.save")}
                        </Button>
                        <Button variant="ghost" onClick={() => setEditingId("")}>
                          {t("common.back")}
                        </Button>
                      </div>
                    </form>
                  ) : transplantId === project.id ? (
                    <ConfirmByName
                      title={t("orchard.transplantTitle")}
                      hint={t("orchard.transplantHint")}
                      expectedName={project.name}
                      placeholder={t("orchard.uprootConfirm", { name: project.name })}
                      confirmLabel={t("orchard.transplant")}
                      busyLabel={t("orchard.transplanting")}
                      cancelLabel={t("common.back")}
                      busy={busyId === `transplant:${project.id}`}
                      onConfirm={() => void transplant(project)}
                      onCancel={() => setTransplantId("")}
                    />
                  ) : uprootId === project.id ? (
                    <ConfirmByName
                      title={t("orchard.uprootTitle")}
                      hint={t("orchard.uprootHint")}
                      expectedName={project.name}
                      placeholder={t("orchard.uprootConfirm", { name: project.name })}
                      confirmLabel={t("orchard.uproot")}
                      busyLabel={t("orchard.uprooting")}
                      cancelLabel={t("common.back")}
                      busy={busyId === `delete:${project.id}`}
                      danger
                      onConfirm={() => void uproot(project)}
                      onCancel={() => setUprootId("")}
                    />
                  ) : (
                    <div className="tree-actions tree-card-actions">
                      <Button
                        onClick={() => void observe(project)}
                        disabled={busyId === `open:${project.id}`}
                      >
                        {busyId === `open:${project.id}`
                          ? t("orchard.observing")
                          : t("orchard.observe")}
                      </Button>
                      <Menu
                        label={t("orchard.more")}
                        items={[
                          {
                            key: "rename",
                            label: t("orchard.rename"),
                            onClick: () => startRename(project),
                          },
                          {
                            key: "propagate",
                            label:
                              busyId === `propagate:${project.id}`
                                ? t("orchard.propagating")
                                : t("orchard.propagate"),
                            disabled: busyId === `propagate:${project.id}`,
                            onClick: () => void propagate(project),
                          },
                          {
                            key: "transplant",
                            label: t("orchard.transplant"),
                            onClick: () => startTransplant(project),
                          },
                          {
                            key: "uproot",
                            label: t("orchard.uproot"),
                            danger: true,
                            onClick: () => startUproot(project),
                          },
                        ]}
                      />
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
