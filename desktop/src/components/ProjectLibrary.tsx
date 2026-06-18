import { useEffect, useMemo, useState } from "react";
import type { FormEvent } from "react";
import type { AppBootstrap, ProjectSelection, ProjectSummary } from "../api";
import {
  chooseWorkspaceDirectory,
  createProject,
  deleteProject,
  importExistingProject,
  renameProject,
  selectProject,
} from "../api";

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
  const [projects, setProjects] = useState<ProjectSummary[]>(bootstrap.projects);
  const [currentProject, setCurrentProject] = useState<ProjectSummary | null>(
    bootstrap.current_project,
  );
  const [selectedId, setSelectedId] = useState<string>(
    bootstrap.current_project?.id ?? bootstrap.projects[0]?.id ?? "",
  );
  const [name, setName] = useState<string>("");
  const [editName, setEditName] = useState<string>("");
  const [editDescription, setEditDescription] = useState<string>("");
  const [deleteConfirm, setDeleteConfirm] = useState<string>("");
  const [busyId, setBusyId] = useState<string>("");
  const [error, setError] = useState<string>(bootstrap.error ?? "");
  const [message, setMessage] = useState<string>("");

  useEffect(() => {
    setProjects(bootstrap.projects);
    setCurrentProject(bootstrap.current_project);
    setSelectedId((previous) => {
      if (previous && bootstrap.projects.some((project) => project.id === previous)) {
        return previous;
      }
      return bootstrap.current_project?.id ?? bootstrap.projects[0]?.id ?? "";
    });
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

  const selectedProject =
    sortedProjects.find((project) => project.id === selectedId) ?? sortedProjects[0] ?? null;

  useEffect(() => {
    setEditName(selectedProject?.name ?? "");
    setEditDescription(selectedProject?.description ?? "");
    setDeleteConfirm("");
  }, [selectedProject?.id, selectedProject?.name, selectedProject?.description]);

  const applySelection = (selection: ProjectSelection): void => {
    setProjects(selection.projects);
    setCurrentProject(selection.current_project);
    setSelectedId(selection.current_project.id);
    onProjectReady(selection);
  };

  const applyBootstrap = (next: AppBootstrap): void => {
    setProjects(next.projects);
    setCurrentProject(next.current_project);
    setSelectedId((previous) => {
      if (previous && next.projects.some((project) => project.id === previous)) return previous;
      return next.current_project?.id ?? next.projects[0]?.id ?? "";
    });
    onBootstrapChange(next);
  };

  const create = async (event: FormEvent<HTMLFormElement>): Promise<void> => {
    event.preventDefault();
    const trimmed = name.trim();
    if (!trimmed) {
      setError("Project name is required.");
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

  const importExisting = async (): Promise<void> => {
    setBusyId("import");
    setError("");
    setMessage("");
    try {
      const sourcePath = await chooseWorkspaceDirectory();
      if (!sourcePath) return;
      const trimmed = name.trim();
      const selection = await importExistingProject(sourcePath, trimmed || undefined);
      setName("");
      applySelection(selection);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusyId("");
    }
  };

  const open = async (project: ProjectSummary): Promise<void> => {
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

  const saveDetails = async (event: FormEvent<HTMLFormElement>): Promise<void> => {
    event.preventDefault();
    if (!selectedProject) return;
    setBusyId(`save:${selectedProject.id}`);
    setError("");
    setMessage("");
    try {
      const next = await renameProject(selectedProject.id, editName, editDescription);
      applyBootstrap(next);
      setMessage("Project details saved.");
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusyId("");
    }
  };

  const removeProject = async (): Promise<void> => {
    if (!selectedProject) return;
    setBusyId(`delete:${selectedProject.id}`);
    setError("");
    setMessage("");
    try {
      const next = await deleteProject(selectedProject.id, deleteConfirm);
      applyBootstrap(next);
      setMessage("Project deleted.");
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
            Back to project
          </button>
        )}
      </header>
      <main className="project-library-main">
        <section className="project-create">
          <div>
            <h1>Projects</h1>
            <p className="muted">Imported files, generated files, DAG state, and runtime data stay separate.</p>
          </div>
          <form className="project-form" onSubmit={(event) => void create(event)}>
            <input
              value={name}
              maxLength={80}
              onChange={(event) => setName(event.target.value)}
              placeholder="New project name"
              aria-label="New project name"
            />
            <button type="submit" disabled={busyId === "create"}>
              {busyId === "create" ? "Creating..." : "Create"}
            </button>
            <button
              className="ghost"
              type="button"
              onClick={() => void importExisting()}
              disabled={busyId === "import"}
            >
              {busyId === "import" ? "Importing..." : "Import Existing"}
            </button>
          </form>
        </section>

        {error && <div className="errors project-error">{error}</div>}
        {message && <div className="success project-error">{message}</div>}

        {sortedProjects.length === 0 ? (
          <section className="project-empty">
            <h2>No projects yet</h2>
            <p className="muted">Create one or import an existing TREE workspace.</p>
          </section>
        ) : (
          <section className="project-workspace" aria-label="Project library">
            <div className="project-grid" aria-label="Project list">
              {sortedProjects.map((project) => (
                <article
                  className={`project-card ${project.id === selectedProject?.id ? "selected" : ""}`}
                  key={project.id}
                >
                  <button
                    className="project-card-main"
                    type="button"
                    onClick={() => setSelectedId(project.id)}
                    aria-pressed={project.id === selectedProject?.id}
                  >
                    <span>
                      <strong>{project.name}</strong>
                      {currentProject?.id === project.id && <small>Current project</small>}
                    </span>
                    <span className="muted">{formatProjectDate(project.last_opened_at)}</span>
                  </button>
                  <dl className="project-stats">
                    <div>
                      <dt>Imported</dt>
                      <dd>{project.source_count}</dd>
                    </div>
                    <div>
                      <dt>Generated</dt>
                      <dd>{project.output_count}</dd>
                    </div>
                  </dl>
                  <button
                    type="button"
                    onClick={() => void open(project)}
                    disabled={busyId === `open:${project.id}`}
                  >
                    {busyId === `open:${project.id}` ? "Opening..." : "Open"}
                  </button>
                </article>
              ))}
            </div>

            {selectedProject && (
              <aside className="project-detail" aria-label="Project details">
                <div className="project-detail-head">
                  <div>
                    <h2>{selectedProject.name}</h2>
                    <p className="muted">{selectedProject.description || "No description"}</p>
                  </div>
                  <span className="pill">{currentProject?.id === selectedProject.id ? "Current" : "Library"}</span>
                </div>

                <dl className="project-meta-grid">
                  <div>
                    <dt>Imported</dt>
                    <dd>{selectedProject.source_count}</dd>
                  </div>
                  <div>
                    <dt>Generated</dt>
                    <dd>{selectedProject.output_count}</dd>
                  </div>
                  <div>
                    <dt>Storage</dt>
                    <dd>{formatBytes(selectedProject.storage_bytes)}</dd>
                  </div>
                  <div>
                    <dt>Created</dt>
                    <dd>{formatDate(selectedProject.created_at)}</dd>
                  </div>
                  <div>
                    <dt>Updated</dt>
                    <dd>{formatDate(selectedProject.updated_at)}</dd>
                  </div>
                  <div>
                    <dt>Last Opened</dt>
                    <dd>{formatDate(selectedProject.last_opened_at)}</dd>
                  </div>
                </dl>

                <form className="project-settings-form" onSubmit={(event) => void saveDetails(event)}>
                  <label>
                    Name
                    <input
                      value={editName}
                      maxLength={80}
                      onChange={(event) => setEditName(event.target.value)}
                    />
                  </label>
                  <label>
                    Description
                    <textarea
                      value={editDescription}
                      maxLength={500}
                      rows={4}
                      onChange={(event) => setEditDescription(event.target.value)}
                    />
                  </label>
                  <button type="submit" disabled={busyId === `save:${selectedProject.id}`}>
                    {busyId === `save:${selectedProject.id}` ? "Saving..." : "Save details"}
                  </button>
                </form>

                <section className="project-danger" aria-label="Delete project">
                  <h3>Delete Project</h3>
                  <p className="muted">This removes the managed project copy and cannot be undone.</p>
                  <input
                    value={deleteConfirm}
                    onChange={(event) => setDeleteConfirm(event.target.value)}
                    placeholder={`Type ${selectedProject.name}`}
                    aria-label="Project delete confirmation"
                  />
                  <button
                    className="danger"
                    type="button"
                    onClick={() => void removeProject()}
                    disabled={
                      deleteConfirm !== selectedProject.name ||
                      busyId === `delete:${selectedProject.id}`
                    }
                  >
                    {busyId === `delete:${selectedProject.id}` ? "Deleting..." : "Delete project"}
                  </button>
                </section>
              </aside>
            )}
          </section>
        )}
      </main>
    </div>
  );
}

function formatProjectDate(seconds: number): string {
  if (!seconds) return "Never opened";
  return `Last opened ${new Date(seconds * 1000).toLocaleString()}`;
}

function formatDate(seconds: number): string {
  if (!seconds) return "Not yet";
  return new Date(seconds * 1000).toLocaleString();
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
