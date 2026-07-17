//! TREE desktop shell: manages the desktop project registry, starts the Python
//! engine sidecar (`tre serve`) for the selected project root, and hands the
//! React frontend its loopback API base + token.
//!
//! Sidecar resolution order:
//!   1. `TREE_SIDECAR_BIN` (explicit path),
//!   2. bundled resource `tre-engine/tre-engine[.exe]` (production installer),
//!   3. the dev PyInstaller build under `packaging/dist`.
//! For dev against a manually-run server, set `TREE_API_BASE` + `TREE_API_TOKEN`.

use std::fs;
use std::fs::File;
use std::io::{Read, Seek, Write};
use std::net::{TcpListener, TcpStream};
#[cfg(windows)]
use std::os::windows::process::CommandExt;
use std::path::{Path, PathBuf};
use std::process::{Child, Command, Stdio};
use std::sync::Mutex;
use std::time::{Duration, SystemTime, UNIX_EPOCH};

use serde::{Deserialize, Serialize};
use tauri::{Manager, RunEvent, State};

const ARCHIVE_MANIFEST: &str = "tree-parent-tree.json";
const ARCHIVE_SCHEMA: &str = "tree.parent-tree";
#[cfg(windows)]
const CREATE_NO_WINDOW: u32 = 0x0800_0000;

#[derive(Clone, Debug, Serialize, Deserialize)]
struct ApiConfig {
    base: String,
    token: String,
}

#[derive(Clone, Debug, Serialize, Deserialize)]
struct ProjectSummary {
    id: String,
    name: String,
    #[serde(default)]
    description: String,
    path: String,
    created_at: u64,
    updated_at: u64,
    last_opened_at: u64,
    source_count: u64,
    output_count: u64,
    #[serde(default)]
    storage_bytes: u64,
}

#[derive(Clone, Debug, Serialize, Deserialize)]
struct ProjectFile {
    schema: String,
    version: u32,
    id: String,
    name: String,
    #[serde(default)]
    description: String,
    created_at: u64,
    updated_at: u64,
}

#[derive(Clone, Debug, Serialize, Deserialize)]
struct ProjectIndex {
    schema: String,
    version: u32,
    current_project_id: Option<String>,
    projects: Vec<ProjectSummary>,
}

impl Default for ProjectIndex {
    fn default() -> Self {
        Self {
            schema: "tree.project-index".to_string(),
            version: 1,
            current_project_id: None,
            projects: Vec::new(),
        }
    }
}

#[derive(Clone, Debug, Serialize)]
struct AppBootstrap {
    tauri: bool,
    projects: Vec<ProjectSummary>,
    current_project: Option<ProjectSummary>,
    api: Option<ApiConfig>,
    error: Option<String>,
}

#[derive(Clone, Debug, Serialize)]
struct ProjectSelection {
    projects: Vec<ProjectSummary>,
    current_project: ProjectSummary,
    api: ApiConfig,
}

#[derive(Clone, Debug, Serialize)]
struct ProjectArchiveResult {
    path: String,
    bytes: u64,
}

#[derive(Clone, Debug, Serialize, Deserialize)]
struct ProjectArchiveManifest {
    schema: String,
    version: u32,
    project_id: String,
    name: String,
    #[serde(default)]
    description: String,
    created_at: u64,
    updated_at: u64,
    exported_at: u64,
    workspace_root: String,
}

struct AppState {
    api: Mutex<Option<ApiConfig>>,
    sidecar: Mutex<Option<Child>>,
}

impl AppState {
    fn new() -> Self {
        Self {
            api: Mutex::new(None),
            sidecar: Mutex::new(None),
        }
    }
}

#[tauri::command]
fn api_config(
    app: tauri::AppHandle,
    state: State<'_, AppState>,
) -> Result<serde_json::Value, String> {
    if let Some(api) = external_api_config() {
        set_active_api(&state, Some(api.clone()))?;
        return Ok(serde_json::json!({ "base": api.base, "token": api.token }));
    }
    let api = match active_api(&state)? {
        Some(api) => api,
        None => {
            let index = load_index()?;
            let current_id = index
                .current_project_id
                .clone()
                .ok_or_else(|| "No active project API".to_string())?;
            activate_project(&app, &state, &current_id)?.api
        }
    };
    Ok(serde_json::json!({ "base": api.base, "token": api.token }))
}

#[tauri::command]
fn app_bootstrap(
    app: tauri::AppHandle,
    state: State<'_, AppState>,
) -> Result<AppBootstrap, String> {
    let index = load_index()?;
    let projects = refresh_projects(&index);
    let current_project = current_project_from(&index, &projects);

    if let Some(api) = external_api_config() {
        set_active_api(&state, Some(api.clone()))?;
        return Ok(AppBootstrap {
            tauri: true,
            projects,
            current_project,
            api: Some(api),
            error: None,
        });
    }

    if let Some(api) = active_api(&state)? {
        return Ok(AppBootstrap {
            tauri: true,
            projects,
            current_project,
            api: Some(api),
            error: None,
        });
    }

    let Some(current_id) = index.current_project_id.clone() else {
        return Ok(AppBootstrap {
            tauri: true,
            projects,
            current_project: None,
            api: None,
            error: None,
        });
    };

    match activate_project(&app, &state, &current_id) {
        Ok(selection) => Ok(AppBootstrap {
            tauri: true,
            projects: selection.projects,
            current_project: Some(selection.current_project),
            api: Some(selection.api),
            error: None,
        }),
        Err(error) => Ok(AppBootstrap {
            tauri: true,
            projects,
            current_project,
            api: None,
            error: Some(error),
        }),
    }
}

#[tauri::command]
fn list_projects() -> Result<Vec<ProjectSummary>, String> {
    let index = load_index()?;
    Ok(refresh_projects(&index))
}

#[tauri::command]
fn create_project(
    app: tauri::AppHandle,
    state: State<'_, AppState>,
    name: String,
) -> Result<ProjectSelection, String> {
    ensure_project_switching_allowed()?;

    let name = validate_project_name(&name)?;
    let mut index = load_index()?;
    if has_project_name(&index, &name) {
        return Err("A project with this name already exists".to_string());
    }

    let now = now_secs();
    let id = format!("proj_{}", uuid::Uuid::new_v4().as_simple());
    let path = projects_root().join(&id);
    let summary = ProjectSummary {
        id: id.clone(),
        name: name.clone(),
        description: String::new(),
        path: path_to_string(&path),
        created_at: now,
        updated_at: now,
        last_opened_at: 0,
        source_count: 0,
        output_count: 0,
        storage_bytes: 0,
    };

    ensure_project_dirs(&summary)?;
    write_project_file(&summary)?;
    index.projects.push(summary);
    save_index(&index)?;

    activate_project(&app, &state, &id)
}

#[tauri::command]
fn select_project(
    app: tauri::AppHandle,
    state: State<'_, AppState>,
    id: String,
) -> Result<ProjectSelection, String> {
    ensure_project_switching_allowed()?;
    activate_project(&app, &state, &id)
}

#[tauri::command]
fn rename_project(
    state: State<'_, AppState>,
    id: String,
    name: String,
    description: Option<String>,
) -> Result<AppBootstrap, String> {
    ensure_project_switching_allowed()?;

    let name = validate_project_name(&name)?;
    let description = validate_project_description(description.as_deref().unwrap_or(""))?;
    let mut index = load_index()?;
    if has_project_name_except(&index, &name, &id) {
        return Err("A project with this name already exists".to_string());
    }

    let Some(position) = index.projects.iter().position(|project| project.id == id) else {
        return Err("Project not found".to_string());
    };

    let now = now_secs();
    let mut project = index.projects[position].clone();
    project.name = name;
    project.description = description;
    project.updated_at = now;
    project.storage_bytes = dir_size(&project.path);
    index.projects[position] = project.clone();

    ensure_project_dirs(&project)?;
    write_project_file(&project)?;
    save_index(&index)?;
    bootstrap_from_state(&state)
}

#[tauri::command]
fn delete_project(
    state: State<'_, AppState>,
    id: String,
    confirmation: String,
) -> Result<AppBootstrap, String> {
    ensure_project_switching_allowed()?;

    let mut index = load_index()?;
    let Some(position) = index.projects.iter().position(|project| project.id == id) else {
        return Err("Project not found".to_string());
    };
    let project = index.projects[position].clone();
    if confirmation.trim() != project.name {
        return Err("Type the project name to delete it".to_string());
    }
    remove_project_from_index_and_disk(&mut index, &state, &project)?;
    save_index(&index)?;
    bootstrap_from_state(&state)
}

#[tauri::command]
fn import_existing_project(
    app: tauri::AppHandle,
    state: State<'_, AppState>,
    source_path: String,
    name: Option<String>,
) -> Result<ProjectSelection, String> {
    ensure_project_switching_allowed()?;
    let source = PathBuf::from(source_path);
    let project = register_existing_workspace(&source, name.as_deref())?;
    activate_project(&app, &state, &project.id)
}

#[tauri::command]
fn export_project_archive(
    state: State<'_, AppState>,
    id: String,
    destination_path: String,
) -> Result<ProjectArchiveResult, String> {
    ensure_project_switching_allowed()?;
    let index = load_index()?;
    let project = find_project(&index, &id)?;
    ensure_project_not_running(&state, &index, &project)?;
    let destination = normalize_archive_destination(&destination_path);
    write_project_archive(&project, &destination)
}

#[tauri::command]
fn transplant_project(
    state: State<'_, AppState>,
    id: String,
    destination_path: String,
    confirmation: String,
) -> Result<AppBootstrap, String> {
    ensure_project_switching_allowed()?;
    let mut index = load_index()?;
    let project = find_project(&index, &id)?;
    if confirmation.trim() != project.name {
        return Err("Type the project name to transplant it".to_string());
    }
    ensure_project_not_running(&state, &index, &project)?;
    let destination = normalize_archive_destination(&destination_path);
    write_project_archive(&project, &destination)?;
    remove_project_from_index_and_disk(&mut index, &state, &project)?;
    save_index(&index)?;
    bootstrap_from_state(&state)
}

#[tauri::command]
fn import_parent_tree_archive(
    app: tauri::AppHandle,
    state: State<'_, AppState>,
    archive_path: String,
    name: Option<String>,
) -> Result<ProjectSelection, String> {
    ensure_project_switching_allowed()?;
    let archive = PathBuf::from(archive_path);
    let project = restore_parent_tree_archive(&archive, name.as_deref())?;
    activate_project(&app, &state, &project.id)
}

fn activate_project(
    app: &tauri::AppHandle,
    state: &AppState,
    id: &str,
) -> Result<ProjectSelection, String> {
    let mut index = load_index()?;
    let Some(position) = index.projects.iter().position(|project| project.id == id) else {
        return Err("Project not found".to_string());
    };

    let mut project = index.projects[position].clone();
    ensure_project_dirs(&project)?;
    let api = start_project_sidecar(app, state, &project)?;

    let now = now_secs();
    project.updated_at = now;
    project.last_opened_at = now;
    project.source_count = count_regular_files(Path::new(&project.path).join("materials"));
    project.output_count = count_regular_files(Path::new(&project.path).join("outputs"));
    project.storage_bytes = dir_size(&project.path);
    index.projects[position] = project.clone();
    index.current_project_id = Some(project.id.clone());
    save_index(&index)?;

    let projects = refresh_projects(&index);
    let current_project = projects
        .iter()
        .find(|candidate| candidate.id == project.id)
        .cloned()
        .unwrap_or(project);

    Ok(ProjectSelection {
        projects,
        current_project,
        api,
    })
}

fn start_project_sidecar(
    app: &tauri::AppHandle,
    state: &AppState,
    project: &ProjectSummary,
) -> Result<ApiConfig, String> {
    let binary = sidecar_binary(app).ok_or_else(|| {
        "TREE sidecar binary was not found. Rebuild the desktop package.".to_string()
    })?;
    let port = free_port();
    let token = uuid::Uuid::new_v4().as_simple().to_string();
    let root = PathBuf::from(&project.path);
    fs::create_dir_all(&root).map_err(|error| format!("Failed to create project root: {error}"))?;

    stop_managed_sidecar(state, true);

    let child = spawn_sidecar(binary, &root, port, &token)?;
    let api = ApiConfig {
        base: format!("http://127.0.0.1:{port}"),
        token,
    };

    {
        let mut guard = state
            .sidecar
            .lock()
            .map_err(|_| "Sidecar state lock poisoned".to_string())?;
        *guard = Some(child);
    }
    set_active_api(state, Some(api.clone()))?;
    Ok(api)
}

fn stop_managed_sidecar(state: &AppState, notify: bool) {
    let child = state.sidecar.lock().ok().and_then(|mut guard| guard.take());
    let api = if child.is_some() {
        state.api.lock().ok().and_then(|guard| guard.clone())
    } else {
        None
    };

    if notify {
        if let Some(api) = api {
            notify_quit(&api.base, &api.token);
        }
    }

    if let Some(mut child) = child {
        let _ = child.kill();
        let _ = child.wait();
    }
    let _ = set_active_api(state, None);
}

fn active_api(state: &AppState) -> Result<Option<ApiConfig>, String> {
    state
        .api
        .lock()
        .map(|guard| guard.clone())
        .map_err(|_| "API state lock poisoned".to_string())
}

fn set_active_api(state: &AppState, api: Option<ApiConfig>) -> Result<(), String> {
    let mut guard = state
        .api
        .lock()
        .map_err(|_| "API state lock poisoned".to_string())?;
    *guard = api;
    Ok(())
}

fn bootstrap_from_state(state: &AppState) -> Result<AppBootstrap, String> {
    let index = load_index()?;
    let projects = refresh_projects(&index);
    let current_project = current_project_from(&index, &projects);
    Ok(AppBootstrap {
        tauri: true,
        projects,
        current_project,
        api: active_api(state)?,
        error: None,
    })
}

fn find_project(index: &ProjectIndex, id: &str) -> Result<ProjectSummary, String> {
    index
        .projects
        .iter()
        .find(|project| project.id == id)
        .cloned()
        .ok_or_else(|| "Project not found".to_string())
}

fn remove_project_from_index_and_disk(
    index: &mut ProjectIndex,
    state: &AppState,
    project: &ProjectSummary,
) -> Result<(), String> {
    let Some(position) = index
        .projects
        .iter()
        .position(|candidate| candidate.id == project.id)
    else {
        return Err("Project not found".to_string());
    };
    let project_path = PathBuf::from(&project.path);
    if !is_managed_project_path(&project_path) {
        return Err(
            "Only managed TREE projects can be deleted from the project library".to_string(),
        );
    }

    let was_current = index.current_project_id.as_deref() == Some(project.id.as_str());
    if was_current {
        stop_managed_sidecar(state, true);
    }

    if project_path.exists() {
        fs::remove_dir_all(&project_path)
            .map_err(|error| format!("Failed to delete project files: {error}"))?;
    }

    index.projects.remove(position);
    if was_current {
        index.current_project_id = None;
    }
    Ok(())
}

fn ensure_project_switching_allowed() -> Result<(), String> {
    if external_api_config().is_some() {
        Err("Project switching is disabled while TREE_API_BASE/TREE_API_TOKEN are set".to_string())
    } else {
        Ok(())
    }
}

fn external_api_config() -> Option<ApiConfig> {
    match (
        std::env::var("TREE_API_BASE"),
        std::env::var("TREE_API_TOKEN"),
    ) {
        (Ok(base), Ok(token)) => Some(ApiConfig { base, token }),
        _ => None,
    }
}

fn ensure_project_not_running(
    state: &AppState,
    index: &ProjectIndex,
    project: &ProjectSummary,
) -> Result<(), String> {
    if index.current_project_id.as_deref() != Some(project.id.as_str()) {
        return Ok(());
    }
    let Some(api) = active_api(state)? else {
        return Ok(());
    };
    if engine_is_running(&api)? {
        return Err("Stop Grow before archiving this tree.".to_string());
    }
    Ok(())
}

fn engine_is_running(api: &ApiConfig) -> Result<bool, String> {
    let Some(rest) = api.base.strip_prefix("http://") else {
        return Ok(false);
    };
    let host_port = rest.trim_end_matches('/');
    let mut stream = TcpStream::connect(host_port)
        .map_err(|error| format!("Could not verify engine status: {error}"))?;
    let _ = stream.set_read_timeout(Some(Duration::from_millis(1500)));
    let _ = stream.set_write_timeout(Some(Duration::from_millis(1500)));
    let request = format!(
        "GET /api/status?token={} HTTP/1.1\r\nHost: {}\r\nConnection: close\r\n\r\n",
        api.token, host_port
    );
    stream
        .write_all(request.as_bytes())
        .map_err(|error| format!("Could not request engine status: {error}"))?;
    let mut response = String::new();
    stream
        .read_to_string(&mut response)
        .map_err(|error| format!("Could not read engine status: {error}"))?;
    let body = response
        .split_once("\r\n\r\n")
        .map(|(_, body)| body)
        .unwrap_or(response.as_str());
    let parsed: serde_json::Value = serde_json::from_str(body)
        .map_err(|error| format!("Could not parse engine status: {error}"))?;
    Ok(parsed.get("engine").and_then(|value| value.as_str()) == Some("running"))
}

fn free_port() -> u16 {
    TcpListener::bind("127.0.0.1:0")
        .and_then(|listener| listener.local_addr())
        .map(|addr| addr.port())
        .unwrap_or(8799)
}

fn tree_home() -> PathBuf {
    if let Ok(home) = std::env::var("TREE_HOME") {
        return PathBuf::from(home);
    }
    let home = std::env::var("HOME")
        .or_else(|_| std::env::var("USERPROFILE"))
        .unwrap_or_else(|_| ".".to_string());
    PathBuf::from(home).join(".tree")
}

fn projects_root() -> PathBuf {
    tree_home().join("projects")
}

fn index_path() -> PathBuf {
    projects_root().join("index.json")
}

fn load_index() -> Result<ProjectIndex, String> {
    let path = index_path();
    if !path.exists() {
        return Ok(ProjectIndex::default());
    }
    let bytes =
        fs::read(&path).map_err(|error| format!("Failed to read project index: {error}"))?;
    serde_json::from_slice(&bytes)
        .map_err(|error| format!("Failed to parse project index: {error}"))
}

fn save_index(index: &ProjectIndex) -> Result<(), String> {
    write_json_atomic(&index_path(), index)
}

fn write_project_file(project: &ProjectSummary) -> Result<(), String> {
    let file = ProjectFile {
        schema: "tree.project".to_string(),
        version: 1,
        id: project.id.clone(),
        name: project.name.clone(),
        description: project.description.clone(),
        created_at: project.created_at,
        updated_at: project.updated_at,
    };
    write_json_atomic(&Path::new(&project.path).join("project.json"), &file)
}

fn write_json_atomic<T: Serialize>(path: &Path, value: &T) -> Result<(), String> {
    let parent = path
        .parent()
        .ok_or_else(|| format!("Invalid path: {}", path.display()))?;
    fs::create_dir_all(parent).map_err(|error| format!("Failed to create directory: {error}"))?;
    let tmp = parent.join(format!(".{}.tmp", uuid::Uuid::new_v4().as_simple()));
    let bytes = serde_json::to_vec_pretty(value)
        .map_err(|error| format!("Failed to serialize JSON: {error}"))?;
    fs::write(&tmp, bytes).map_err(|error| format!("Failed to write JSON: {error}"))?;
    fs::rename(&tmp, path)
        .or_else(|_| {
            let _ = fs::remove_file(path);
            fs::rename(&tmp, path)
        })
        .map_err(|error| format!("Failed to replace JSON file: {error}"))
}

fn validate_project_name(name: &str) -> Result<String, String> {
    let trimmed = name.trim();
    if trimmed.is_empty() {
        return Err("Project name is required".to_string());
    }
    if trimmed.chars().count() > 80 {
        return Err("Project name must be 80 characters or fewer".to_string());
    }
    Ok(trimmed.to_string())
}

fn validate_project_description(description: &str) -> Result<String, String> {
    let trimmed = description.trim();
    if trimmed.chars().count() > 500 {
        return Err("Project description must be 500 characters or fewer".to_string());
    }
    Ok(trimmed.to_string())
}

fn has_project_name(index: &ProjectIndex, name: &str) -> bool {
    index
        .projects
        .iter()
        .any(|project| project.name.eq_ignore_ascii_case(name))
}

fn has_project_name_except(index: &ProjectIndex, name: &str, except_id: &str) -> bool {
    index
        .projects
        .iter()
        .any(|project| project.id != except_id && project.name.eq_ignore_ascii_case(name))
}

fn ensure_project_dirs(project: &ProjectSummary) -> Result<(), String> {
    let root = PathBuf::from(&project.path);
    fs::create_dir_all(root.join("materials"))
        .map_err(|error| format!("Failed to create materials directory: {error}"))?;
    fs::create_dir_all(root.join("outputs"))
        .map_err(|error| format!("Failed to create outputs directory: {error}"))?;
    fs::create_dir_all(root.join(".tree").join("runtime"))
        .map_err(|error| format!("Failed to create runtime directory: {error}"))?;
    Ok(())
}

fn refresh_projects(index: &ProjectIndex) -> Vec<ProjectSummary> {
    index
        .projects
        .iter()
        .map(|project| {
            let root = PathBuf::from(&project.path);
            let mut refreshed = project.clone();
            refreshed.source_count = count_regular_files(root.join("materials"));
            refreshed.output_count = count_regular_files(root.join("outputs"));
            refreshed.storage_bytes = dir_size(root);
            refreshed
        })
        .collect()
}

fn current_project_from(
    index: &ProjectIndex,
    projects: &[ProjectSummary],
) -> Option<ProjectSummary> {
    let current_id = index.current_project_id.as_ref()?;
    projects
        .iter()
        .find(|project| &project.id == current_id)
        .cloned()
}

fn count_regular_files(path: impl AsRef<Path>) -> u64 {
    let path = path.as_ref();
    let Ok(entries) = fs::read_dir(path) else {
        return 0;
    };
    entries
        .flatten()
        .map(|entry| {
            let path = entry.path();
            if path.is_dir() {
                count_regular_files(path)
            } else if path.is_file() {
                1
            } else {
                0
            }
        })
        .sum()
}

fn dir_size(path: impl AsRef<Path>) -> u64 {
    let path = path.as_ref();
    let Ok(metadata) = fs::metadata(path) else {
        return 0;
    };
    if metadata.is_file() {
        return metadata.len();
    }
    if !metadata.is_dir() {
        return 0;
    }
    let Ok(entries) = fs::read_dir(path) else {
        return 0;
    };
    entries.flatten().map(|entry| dir_size(entry.path())).sum()
}

fn is_managed_project_path(path: &Path) -> bool {
    let root = projects_root();
    match (path.canonicalize(), root.canonicalize()) {
        (Ok(project), Ok(root)) => project.starts_with(root),
        _ => path.starts_with(root),
    }
}

fn register_existing_workspace(
    source: &Path,
    proposed_name: Option<&str>,
) -> Result<ProjectSummary, String> {
    let source = source
        .canonicalize()
        .map_err(|error| format!("Failed to read selected workspace: {error}"))?;
    validate_workspace_root(&source)?;

    let default_name = source
        .file_name()
        .and_then(|value| value.to_str())
        .unwrap_or("Imported TREE Project");
    let name = validate_project_name(proposed_name.unwrap_or(default_name))?;

    let mut index = load_index()?;
    if has_project_name(&index, &name) {
        return Err("A project with this name already exists".to_string());
    }

    let now = now_secs();
    let id = format!("proj_{}", uuid::Uuid::new_v4().as_simple());
    let path = projects_root().join(&id);
    let mut summary = ProjectSummary {
        id: id.clone(),
        name,
        description: "Imported from an existing TREE workspace.".to_string(),
        path: path_to_string(&path),
        created_at: now,
        updated_at: now,
        last_opened_at: 0,
        source_count: 0,
        output_count: 0,
        storage_bytes: 0,
    };

    if let Err(error) = copy_workspace_roots(&source, &path) {
        let _ = fs::remove_dir_all(&path);
        return Err(error);
    }
    ensure_project_dirs(&summary)?;
    summary.source_count = count_regular_files(path.join("materials"));
    summary.output_count = count_regular_files(path.join("outputs"));
    summary.storage_bytes = dir_size(&path);
    write_project_file(&summary)?;

    index.projects.push(summary.clone());
    save_index(&index)?;
    Ok(summary)
}

fn validate_workspace_root(source: &Path) -> Result<(), String> {
    if !source.is_dir() {
        return Err("Choose a TREE workspace folder".to_string());
    }
    let looks_like_workspace = source.join("materials").is_dir()
        || source.join("outputs").is_dir()
        || source.join(".tree").join("runtime").is_dir();
    if !looks_like_workspace {
        return Err(
            "Choose a TREE workspace containing materials, outputs, or .tree/runtime".to_string(),
        );
    }
    Ok(())
}

fn copy_workspace_roots(source: &Path, target: &Path) -> Result<(), String> {
    fs::create_dir_all(target)
        .map_err(|error| format!("Failed to create imported project: {error}"))?;
    for name in ["materials", "outputs", ".tree"] {
        let source_child = source.join(name);
        if !source_child.exists() {
            continue;
        }
        if !source_child.is_dir() {
            return Err(format!(
                "Cannot import workspace because {name} is not a directory"
            ));
        }
        copy_dir_contents(&source_child, &target.join(name))?;
    }
    Ok(())
}

fn normalize_archive_destination(path: &str) -> PathBuf {
    let mut destination = PathBuf::from(path);
    if destination.extension().and_then(|value| value.to_str()) != Some("zip") {
        destination.set_extension("zip");
    }
    destination
}

fn write_project_archive(
    project: &ProjectSummary,
    destination: &Path,
) -> Result<ProjectArchiveResult, String> {
    let root = PathBuf::from(&project.path);
    validate_workspace_root(&root)?;
    if let Some(parent) = destination.parent() {
        if !parent.as_os_str().is_empty() {
            fs::create_dir_all(parent)
                .map_err(|error| format!("Failed to create archive destination: {error}"))?;
        }
    }

    let manifest = ProjectArchiveManifest {
        schema: ARCHIVE_SCHEMA.to_string(),
        version: 1,
        project_id: project.id.clone(),
        name: project.name.clone(),
        description: project.description.clone(),
        created_at: project.created_at,
        updated_at: project.updated_at,
        exported_at: now_secs(),
        workspace_root: path_to_string(&root),
    };
    let manifest_bytes = serde_json::to_vec_pretty(&manifest)
        .map_err(|error| format!("Failed to serialize archive manifest: {error}"))?;

    let files = collect_archive_files(&root, Some(destination))?;
    let mut writer = StoredZipWriter::create(destination)?;
    writer.add_bytes(ARCHIVE_MANIFEST, &manifest_bytes)?;
    for (name, path) in files {
        writer.add_file(&name, &path)?;
    }
    writer.finish()?;

    let bytes = fs::metadata(destination)
        .map_err(|error| format!("Failed to read archive metadata: {error}"))?
        .len();
    Ok(ProjectArchiveResult {
        path: path_to_string(destination),
        bytes,
    })
}

fn collect_archive_files(
    root: &Path,
    exclude_path: Option<&Path>,
) -> Result<Vec<(String, PathBuf)>, String> {
    let mut files = Vec::new();
    for name in ["project.json", "materials", "outputs", ".tree"] {
        let path = root.join(name);
        if !path.exists() {
            continue;
        }
        collect_archive_files_inner(root, &path, exclude_path, &mut files)?;
    }
    files.sort_by(|left, right| left.0.cmp(&right.0));
    Ok(files)
}

fn collect_archive_files_inner(
    root: &Path,
    path: &Path,
    exclude_path: Option<&Path>,
    files: &mut Vec<(String, PathBuf)>,
) -> Result<(), String> {
    if path.is_dir() {
        let mut entries = fs::read_dir(path)
            .map_err(|error| format!("Failed to read archive source: {error}"))?
            .collect::<Result<Vec<_>, _>>()
            .map_err(|error| format!("Failed to read archive entry: {error}"))?;
        entries.sort_by_key(|entry| entry.file_name());
        for entry in entries {
            collect_archive_files_inner(root, &entry.path(), exclude_path, files)?;
        }
        return Ok(());
    }
    let metadata = fs::symlink_metadata(path)
        .map_err(|error| format!("Failed to inspect archive source: {error}"))?;
    if metadata.file_type().is_symlink() || !metadata.is_file() {
        return Ok(());
    }
    if exclude_path.is_some_and(|target| archive_paths_match(path, target)) {
        return Ok(());
    }
    let rel = archive_relative_path(root, path)?;
    if should_include_archive_path(&rel) {
        files.push((rel, path.to_path_buf()));
    }
    Ok(())
}

fn archive_paths_match(path: &Path, target: &Path) -> bool {
    let Ok(path) = fs::canonicalize(path) else {
        return false;
    };
    let Some(parent) = target.parent() else {
        return false;
    };
    let Some(file_name) = target.file_name() else {
        return false;
    };
    let Ok(parent) = fs::canonicalize(parent) else {
        return false;
    };
    path == parent.join(file_name)
}

fn archive_relative_path(root: &Path, path: &Path) -> Result<String, String> {
    let rel = path
        .strip_prefix(root)
        .map_err(|_| format!("Archive path is outside project root: {}", path.display()))?;
    let parts = rel
        .components()
        .map(|component| component.as_os_str().to_string_lossy().into_owned())
        .collect::<Vec<_>>();
    Ok(parts.join("/"))
}

fn should_include_archive_path(rel: &str) -> bool {
    let normalized = rel.replace('\\', "/");
    if normalized == ".env" || normalized == ".tree/config.env" {
        return false;
    }
    if normalized.starts_with(".tree/runtime/services/") {
        return false;
    }
    let filename = normalized.rsplit('/').next().unwrap_or("");
    if filename.ends_with(".pid") || filename.ends_with(".stop") || filename.ends_with(".log") {
        return false;
    }
    true
}

fn restore_parent_tree_archive(
    archive: &Path,
    proposed_name: Option<&str>,
) -> Result<ProjectSummary, String> {
    if !archive.is_file() {
        return Err("Choose a TREE parent tree zip archive".to_string());
    }
    let manifest = read_archive_manifest(archive)?;
    if manifest.schema != ARCHIVE_SCHEMA {
        return Err("Archive is not a TREE parent tree archive".to_string());
    }

    let name = validate_project_name(proposed_name.unwrap_or(&manifest.name))?;
    let mut index = load_index()?;
    if has_project_name(&index, &name) {
        return Err("A project with this name already exists".to_string());
    }

    let now = now_secs();
    let id = format!("proj_{}", uuid::Uuid::new_v4().as_simple());
    let path = projects_root().join(&id);
    let mut summary = ProjectSummary {
        id: id.clone(),
        name,
        description: manifest.description.clone(),
        path: path_to_string(&path),
        created_at: now,
        updated_at: now,
        last_opened_at: 0,
        source_count: 0,
        output_count: 0,
        storage_bytes: 0,
    };

    let restore_result = (|| -> Result<(), String> {
        extract_archive_to_project(archive, &path)?;
        ensure_project_dirs(&summary)?;
        rewrite_runtime_json_paths(&path, &manifest.workspace_root, &path_to_string(&path))?;
        write_project_file(&summary)?;
        summary.source_count = count_regular_files(path.join("materials"));
        summary.output_count = count_regular_files(path.join("outputs"));
        summary.storage_bytes = dir_size(&path);
        write_project_file(&summary)?;
        Ok(())
    })();
    if let Err(error) = restore_result {
        let _ = fs::remove_dir_all(&path);
        return Err(error);
    }

    index.projects.push(summary.clone());
    save_index(&index)?;
    Ok(summary)
}

fn read_archive_manifest(archive: &Path) -> Result<ProjectArchiveManifest, String> {
    for entry in read_stored_zip_entries(archive)? {
        if entry.name == ARCHIVE_MANIFEST {
            return serde_json::from_slice(&entry.data)
                .map_err(|error| format!("Failed to parse archive manifest: {error}"));
        }
    }
    Err("Archive manifest missing".to_string())
}

fn extract_archive_to_project(archive: &Path, target: &Path) -> Result<(), String> {
    fs::create_dir_all(target)
        .map_err(|error| format!("Failed to create restored project: {error}"))?;
    let mut saw_manifest = false;
    for entry in read_stored_zip_entries(archive)? {
        if entry.name == ARCHIVE_MANIFEST {
            saw_manifest = true;
            continue;
        }
        if !should_include_archive_path(&entry.name) {
            continue;
        }
        let relative = safe_archive_path(&entry.name)?;
        let destination = target.join(relative);
        if let Some(parent) = destination.parent() {
            fs::create_dir_all(parent)
                .map_err(|error| format!("Failed to create restore directory: {error}"))?;
        }
        fs::write(&destination, entry.data)
            .map_err(|error| format!("Failed to restore archive file: {error}"))?;
    }
    if !saw_manifest {
        return Err("Archive manifest missing".to_string());
    }
    Ok(())
}

fn safe_archive_path(name: &str) -> Result<PathBuf, String> {
    if name.is_empty() || name.ends_with('/') || name.contains('\\') {
        return Err("Archive contains an unsafe path".to_string());
    }
    let path = PathBuf::from(name);
    if path.is_absolute() || name.contains(':') {
        return Err("Archive contains an unsafe path".to_string());
    }
    for component in path.components() {
        match component {
            std::path::Component::Normal(_) => {}
            _ => return Err("Archive contains an unsafe path".to_string()),
        }
    }
    Ok(path)
}

fn rewrite_runtime_json_paths(root: &Path, old_root: &str, new_root: &str) -> Result<(), String> {
    if old_root.is_empty() || old_root == new_root {
        return Ok(());
    }
    let runtime = root.join(".tree").join("runtime");
    if !runtime.is_dir() {
        return Ok(());
    }
    rewrite_runtime_json_paths_inner(&runtime, old_root, new_root)
}

fn rewrite_runtime_json_paths_inner(
    path: &Path,
    old_root: &str,
    new_root: &str,
) -> Result<(), String> {
    if path.is_dir() {
        for entry in fs::read_dir(path)
            .map_err(|error| format!("Failed to read runtime JSON directory: {error}"))?
        {
            let entry =
                entry.map_err(|error| format!("Failed to read runtime JSON entry: {error}"))?;
            rewrite_runtime_json_paths_inner(&entry.path(), old_root, new_root)?;
        }
        return Ok(());
    }
    if path.extension().and_then(|value| value.to_str()) != Some("json") || !path.is_file() {
        return Ok(());
    }
    let Ok(text) = fs::read_to_string(path) else {
        return Ok(());
    };
    let Ok(mut value) = serde_json::from_str::<serde_json::Value>(&text) else {
        return Ok(());
    };
    if rewrite_json_value_paths(&mut value, old_root, new_root) {
        write_json_atomic(path, &value)?;
    }
    Ok(())
}

fn rewrite_json_value_paths(value: &mut serde_json::Value, old_root: &str, new_root: &str) -> bool {
    match value {
        serde_json::Value::String(text) => {
            if text.starts_with(old_root) {
                *text = format!("{}{}", new_root, &text[old_root.len()..]);
                true
            } else {
                false
            }
        }
        serde_json::Value::Array(items) => {
            let mut changed = false;
            for item in items {
                changed |= rewrite_json_value_paths(item, old_root, new_root);
            }
            changed
        }
        serde_json::Value::Object(map) => {
            let mut changed = false;
            for item in map.values_mut() {
                changed |= rewrite_json_value_paths(item, old_root, new_root);
            }
            changed
        }
        _ => false,
    }
}

fn copy_dir_contents(source: &Path, target: &Path) -> Result<(), String> {
    fs::create_dir_all(target)
        .map_err(|error| format!("Failed to create import directory: {error}"))?;
    let entries =
        fs::read_dir(source).map_err(|error| format!("Failed to read import source: {error}"))?;
    for entry in entries {
        let entry = entry.map_err(|error| format!("Failed to read import entry: {error}"))?;
        let source_path = entry.path();
        let target_path = target.join(entry.file_name());
        if source_path.is_dir() {
            copy_dir_contents(&source_path, &target_path)?;
        } else if source_path.is_file() {
            fs::copy(&source_path, &target_path)
                .map_err(|error| format!("Failed to copy imported file: {error}"))?;
        }
    }
    Ok(())
}

struct StoredZipWriter {
    file: File,
    entries: Vec<StoredZipCentralEntry>,
}

struct StoredZipCentralEntry {
    name: String,
    crc32: u32,
    size: u32,
    local_offset: u32,
}

struct StoredZipEntry {
    name: String,
    data: Vec<u8>,
}

impl StoredZipWriter {
    fn create(path: &Path) -> Result<Self, String> {
        let file =
            File::create(path).map_err(|error| format!("Failed to create archive: {error}"))?;
        Ok(Self {
            file,
            entries: Vec::new(),
        })
    }

    fn add_file(&mut self, name: &str, path: &Path) -> Result<(), String> {
        let data = fs::read(path)
            .map_err(|error| format!("Failed to read archive file {}: {error}", path.display()))?;
        self.add_bytes(name, &data)
    }

    fn add_bytes(&mut self, name: &str, data: &[u8]) -> Result<(), String> {
        let name_bytes = name.as_bytes();
        let name_len = u16::try_from(name_bytes.len())
            .map_err(|_| format!("Archive path too long: {name}"))?;
        let size =
            u32::try_from(data.len()).map_err(|_| format!("Archive entry too large: {name}"))?;
        let offset = u32::try_from(zip_position(&mut self.file)?)
            .map_err(|_| "Archive exceeds ZIP32 size limit".to_string())?;
        let crc32 = crc32fast::hash(data);

        write_u32(&mut self.file, 0x0403_4b50)?;
        write_u16(&mut self.file, 20)?;
        write_u16(&mut self.file, 0)?;
        write_u16(&mut self.file, 0)?;
        write_u16(&mut self.file, 0)?;
        write_u16(&mut self.file, 0)?;
        write_u32(&mut self.file, crc32)?;
        write_u32(&mut self.file, size)?;
        write_u32(&mut self.file, size)?;
        write_u16(&mut self.file, name_len)?;
        write_u16(&mut self.file, 0)?;
        zip_write_all(&mut self.file, name_bytes)?;
        zip_write_all(&mut self.file, data)?;
        self.entries.push(StoredZipCentralEntry {
            name: name.to_string(),
            crc32,
            size,
            local_offset: offset,
        });
        Ok(())
    }

    fn finish(mut self) -> Result<(), String> {
        let central_offset = u32::try_from(zip_position(&mut self.file)?)
            .map_err(|_| "Archive exceeds ZIP32 size limit".to_string())?;
        for entry in &self.entries {
            let name_bytes = entry.name.as_bytes();
            let name_len = u16::try_from(name_bytes.len())
                .map_err(|_| format!("Archive path too long: {}", entry.name))?;
            write_u32(&mut self.file, 0x0201_4b50)?;
            write_u16(&mut self.file, 20)?;
            write_u16(&mut self.file, 20)?;
            write_u16(&mut self.file, 0)?;
            write_u16(&mut self.file, 0)?;
            write_u16(&mut self.file, 0)?;
            write_u16(&mut self.file, 0)?;
            write_u32(&mut self.file, entry.crc32)?;
            write_u32(&mut self.file, entry.size)?;
            write_u32(&mut self.file, entry.size)?;
            write_u16(&mut self.file, name_len)?;
            write_u16(&mut self.file, 0)?;
            write_u16(&mut self.file, 0)?;
            write_u16(&mut self.file, 0)?;
            write_u16(&mut self.file, 0)?;
            write_u32(&mut self.file, 0)?;
            write_u32(&mut self.file, entry.local_offset)?;
            zip_write_all(&mut self.file, name_bytes)?;
        }
        let central_size = u32::try_from(zip_position(&mut self.file)? - u64::from(central_offset))
            .map_err(|_| "Archive central directory exceeds ZIP32 size limit".to_string())?;
        let count = u16::try_from(self.entries.len())
            .map_err(|_| "Archive has too many entries".to_string())?;
        write_u32(&mut self.file, 0x0605_4b50)?;
        write_u16(&mut self.file, 0)?;
        write_u16(&mut self.file, 0)?;
        write_u16(&mut self.file, count)?;
        write_u16(&mut self.file, count)?;
        write_u32(&mut self.file, central_size)?;
        write_u32(&mut self.file, central_offset)?;
        write_u16(&mut self.file, 0)?;
        self.file
            .flush()
            .map_err(|error| format!("Failed to flush archive: {error}"))?;
        Ok(())
    }
}

fn read_stored_zip_entries(path: &Path) -> Result<Vec<StoredZipEntry>, String> {
    let bytes = fs::read(path).map_err(|error| format!("Failed to read archive: {error}"))?;
    let mut pos = 0usize;
    let mut entries = Vec::new();
    while pos + 4 <= bytes.len() {
        let signature = read_u32_from(&bytes, pos)?;
        if signature == 0x0201_4b50 || signature == 0x0605_4b50 {
            break;
        }
        if signature != 0x0403_4b50 {
            return Err("Archive contains an unsupported ZIP record".to_string());
        }
        if pos + 30 > bytes.len() {
            return Err("Archive local header is truncated".to_string());
        }
        let flags = read_u16_from(&bytes, pos + 6)?;
        let method = read_u16_from(&bytes, pos + 8)?;
        let crc32 = read_u32_from(&bytes, pos + 14)?;
        let compressed_size = read_u32_from(&bytes, pos + 18)? as usize;
        let uncompressed_size = read_u32_from(&bytes, pos + 22)? as usize;
        let name_len = read_u16_from(&bytes, pos + 26)? as usize;
        let extra_len = read_u16_from(&bytes, pos + 28)? as usize;
        if flags & 0x0008 != 0 {
            return Err("Archive uses unsupported ZIP data descriptors".to_string());
        }
        if method != 0 {
            return Err("Archive uses unsupported ZIP compression".to_string());
        }
        if compressed_size != uncompressed_size {
            return Err("Archive entry has invalid stored sizes".to_string());
        }
        let name_start = pos + 30;
        let data_start = name_start
            .checked_add(name_len)
            .and_then(|value| value.checked_add(extra_len))
            .ok_or_else(|| "Archive entry is too large".to_string())?;
        let data_end = data_start
            .checked_add(compressed_size)
            .ok_or_else(|| "Archive entry is too large".to_string())?;
        if data_end > bytes.len() {
            return Err("Archive entry data is truncated".to_string());
        }
        let name = std::str::from_utf8(&bytes[name_start..name_start + name_len])
            .map_err(|_| "Archive entry path is not UTF-8".to_string())?
            .to_string();
        if name.ends_with('/') {
            pos = data_end;
            continue;
        }
        safe_archive_path(&name)?;
        let data = bytes[data_start..data_end].to_vec();
        if crc32fast::hash(&data) != crc32 {
            return Err(format!("Archive entry failed checksum: {name}"));
        }
        entries.push(StoredZipEntry { name, data });
        pos = data_end;
    }
    Ok(entries)
}

fn write_u16(file: &mut File, value: u16) -> Result<(), String> {
    zip_write_all(file, &value.to_le_bytes())
}

fn write_u32(file: &mut File, value: u32) -> Result<(), String> {
    zip_write_all(file, &value.to_le_bytes())
}

fn zip_write_all(file: &mut File, bytes: &[u8]) -> Result<(), String> {
    file.write_all(bytes)
        .map_err(|error| format!("Failed to write archive: {error}"))
}

fn zip_position(file: &mut File) -> Result<u64, String> {
    file.stream_position()
        .map_err(|error| format!("Failed to write archive: {error}"))
}

fn read_u16_from(bytes: &[u8], pos: usize) -> Result<u16, String> {
    let data = bytes
        .get(pos..pos + 2)
        .ok_or_else(|| "Archive is truncated".to_string())?;
    Ok(u16::from_le_bytes([data[0], data[1]]))
}

fn read_u32_from(bytes: &[u8], pos: usize) -> Result<u32, String> {
    let data = bytes
        .get(pos..pos + 4)
        .ok_or_else(|| "Archive is truncated".to_string())?;
    Ok(u32::from_le_bytes([data[0], data[1], data[2], data[3]]))
}

fn path_to_string(path: &Path) -> String {
    path.to_string_lossy().into_owned()
}

fn now_secs() -> u64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|duration| duration.as_secs())
        .unwrap_or(0)
}

fn sidecar_exe_name() -> &'static str {
    if cfg!(windows) {
        "tre-engine.exe"
    } else {
        "tre-engine"
    }
}

fn sidecar_binary(app: &tauri::AppHandle) -> Option<PathBuf> {
    if let Ok(explicit) = std::env::var("TREE_SIDECAR_BIN") {
        let path = PathBuf::from(explicit);
        if path.is_file() {
            return Some(path);
        }
    }
    if let Ok(resources) = app.path().resource_dir() {
        let bundled = resources.join("tre-engine").join(sidecar_exe_name());
        if bundled.is_file() {
            return Some(bundled);
        }
    }
    let dev = PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .join("../../packaging/dist/tre-engine")
        .join(sidecar_exe_name());
    dev.is_file().then_some(dev)
}

fn spawn_sidecar(binary: PathBuf, root: &Path, port: u16, token: &str) -> Result<Child, String> {
    let root_arg = path_to_string(root);
    let mut command = Command::new(binary);
    #[cfg(windows)]
    command.creation_flags(CREATE_NO_WINDOW);

    command
        .args([
            "serve",
            "--host",
            "127.0.0.1",
            "--port",
            &port.to_string(),
            "--token",
            token,
            "--root",
            &root_arg,
        ])
        .stdout(Stdio::null())
        .stderr(Stdio::null())
        .spawn()
        .map_err(|error| format!("Failed to start TREE sidecar: {error}"))
}

fn notify_quit(base: &str, token: &str) {
    let Some(rest) = base.strip_prefix("http://") else {
        return;
    };
    let host_port = rest.trim_end_matches('/');
    let Ok(mut stream) = TcpStream::connect(host_port) else {
        return;
    };
    let _ = stream.set_read_timeout(Some(Duration::from_millis(750)));
    let _ = stream.set_write_timeout(Some(Duration::from_millis(750)));
    let request = format!(
        "POST /api/quit?token={} HTTP/1.1\r\nHost: {}\r\nContent-Length: 0\r\nConnection: close\r\n\r\n",
        token, host_port
    );
    let _ = stream.write_all(request.as_bytes());
    let mut buf = [0_u8; 128];
    let _ = stream.read(&mut buf);
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        .plugin(tauri_plugin_dialog::init())
        .invoke_handler(tauri::generate_handler![
            api_config,
            app_bootstrap,
            list_projects,
            create_project,
            select_project,
            rename_project,
            delete_project,
            import_existing_project,
            export_project_archive,
            transplant_project,
            import_parent_tree_archive
        ])
        .setup(|app| {
            if cfg!(debug_assertions) {
                app.handle().plugin(
                    tauri_plugin_log::Builder::default()
                        .level(log::LevelFilter::Info)
                        .build(),
                )?;
            }
            let state = AppState::new();
            if let Some(api) = external_api_config() {
                let _ = set_active_api(&state, Some(api));
            }
            app.manage(state);
            Ok(())
        })
        .build(tauri::generate_context!())
        .expect("error while building tauri application")
        .run(|app_handle, event| {
            if let RunEvent::Exit = event {
                if let Some(state) = app_handle.try_state::<AppState>() {
                    stop_managed_sidecar(&state, true);
                }
            }
        });
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn project_name_validation_trims_and_rejects_empty() {
        assert_eq!(
            validate_project_name("  Final Review  ").unwrap(),
            "Final Review"
        );
        assert!(validate_project_name("   ").is_err());
    }

    #[test]
    fn project_name_validation_enforces_length() {
        let too_long = "a".repeat(81);
        assert!(validate_project_name(&too_long).is_err());
    }

    #[test]
    fn duplicate_project_names_are_case_insensitive() {
        let mut index = ProjectIndex::default();
        index.projects.push(ProjectSummary {
            id: "proj_existing".to_string(),
            name: "Biology Notes".to_string(),
            description: "Course notes".to_string(),
            path: "/tmp/proj_existing".to_string(),
            created_at: 1,
            updated_at: 1,
            last_opened_at: 0,
            source_count: 0,
            output_count: 0,
            storage_bytes: 0,
        });
        assert!(has_project_name(&index, "biology notes"));
        assert!(has_project_name(&index, "BIOLOGY NOTES"));
        assert!(!has_project_name(&index, "Chemistry Notes"));
    }

    #[test]
    fn duplicate_project_names_can_ignore_current_project() {
        let mut index = ProjectIndex::default();
        index.projects.push(ProjectSummary {
            id: "proj_one".to_string(),
            name: "Biology Notes".to_string(),
            description: String::new(),
            path: "/tmp/proj_one".to_string(),
            created_at: 1,
            updated_at: 1,
            last_opened_at: 0,
            source_count: 0,
            output_count: 0,
            storage_bytes: 0,
        });
        index.projects.push(ProjectSummary {
            id: "proj_two".to_string(),
            name: "Chemistry Notes".to_string(),
            description: String::new(),
            path: "/tmp/proj_two".to_string(),
            created_at: 1,
            updated_at: 1,
            last_opened_at: 0,
            source_count: 0,
            output_count: 0,
            storage_bytes: 0,
        });

        assert!(!has_project_name_except(
            &index,
            "biology notes",
            "proj_one"
        ));
        assert!(has_project_name_except(&index, "biology notes", "proj_two"));
    }

    #[test]
    fn project_description_validation_trims_and_limits_length() {
        assert_eq!(
            validate_project_description("  imported course notes  ").unwrap(),
            "imported course notes"
        );
        assert!(validate_project_description(&"a".repeat(501)).is_err());
    }

    #[test]
    fn workspace_validation_requires_tree_shape() {
        let root = temp_test_dir("workspace-validation");
        assert!(validate_workspace_root(&root).is_err());

        fs::create_dir_all(root.join(".tree").join("runtime")).unwrap();
        assert!(validate_workspace_root(&root).is_ok());

        let _ = fs::remove_dir_all(root);
    }

    #[test]
    fn workspace_copy_preserves_known_project_roots() {
        let source = temp_test_dir("workspace-source");
        let target = temp_test_dir("workspace-target");
        fs::create_dir_all(source.join("materials").join("default")).unwrap();
        fs::create_dir_all(source.join("outputs")).unwrap();
        fs::create_dir_all(source.join(".tree").join("runtime")).unwrap();
        fs::write(
            source.join("materials").join("default").join("lecture.md"),
            "source",
        )
        .unwrap();
        fs::write(source.join("outputs").join("001.md"), "output").unwrap();
        fs::write(
            source.join(".tree").join("runtime").join("state.json"),
            "{}",
        )
        .unwrap();

        copy_workspace_roots(&source, &target).unwrap();

        assert_eq!(
            fs::read_to_string(target.join("materials").join("default").join("lecture.md"))
                .unwrap(),
            "source"
        );
        assert_eq!(
            fs::read_to_string(target.join("outputs").join("001.md")).unwrap(),
            "output"
        );
        assert_eq!(
            fs::read_to_string(target.join(".tree").join("runtime").join("state.json")).unwrap(),
            "{}"
        );

        let _ = fs::remove_dir_all(source);
        let _ = fs::remove_dir_all(target);
    }

    #[test]
    fn parent_tree_archive_includes_progress_and_excludes_secrets() {
        let root = temp_test_dir("archive-source");
        fs::create_dir_all(root.join("materials").join("default")).unwrap();
        fs::create_dir_all(root.join("outputs")).unwrap();
        fs::create_dir_all(root.join(".tree").join("prompts")).unwrap();
        fs::create_dir_all(root.join(".tree").join("runtime").join("services")).unwrap();
        fs::write(root.join("project.json"), "{}").unwrap();
        fs::write(
            root.join("materials").join("default").join("lecture.md"),
            "seed",
        )
        .unwrap();
        fs::write(root.join("outputs").join("001.Root.md"), "fruit").unwrap();
        fs::write(root.join(".tree").join(".gitignore"), "*\n").unwrap();
        fs::write(
            root.join(".tree").join("prompts").join("overrides.json"),
            "{}",
        )
        .unwrap();
        fs::write(
            root.join(".tree")
                .join("runtime")
                .join("learning-state.json"),
            "{}",
        )
        .unwrap();
        fs::write(root.join(".env"), "LLM_API_KEY=secret").unwrap();
        fs::write(
            root.join(".tree").join("config.env"),
            "PADDLEOCR_API_TOKEN=secret",
        )
        .unwrap();
        fs::write(
            root.join(".tree")
                .join("runtime")
                .join("services")
                .join("engine.log"),
            "log",
        )
        .unwrap();
        let project = ProjectSummary {
            id: "proj_archive".to_string(),
            name: "Archive Tree".to_string(),
            description: "desc".to_string(),
            path: path_to_string(&root),
            created_at: 1,
            updated_at: 2,
            last_opened_at: 0,
            source_count: 0,
            output_count: 0,
            storage_bytes: 0,
        };
        let archive = root.with_extension("zip");

        write_project_archive(&project, &archive).unwrap();

        let names = read_stored_zip_entries(&archive)
            .unwrap()
            .into_iter()
            .map(|entry| entry.name)
            .collect::<Vec<_>>();
        assert!(names.contains(&ARCHIVE_MANIFEST.to_string()));
        assert!(names.contains(&"materials/default/lecture.md".to_string()));
        assert!(names.contains(&"outputs/001.Root.md".to_string()));
        assert!(names.contains(&".tree/prompts/overrides.json".to_string()));
        assert!(names.contains(&".tree/runtime/learning-state.json".to_string()));
        assert!(names.contains(&".tree/.gitignore".to_string()));
        assert!(!names.contains(&".env".to_string()));
        assert!(!names.contains(&".tree/config.env".to_string()));
        assert!(!names.contains(&".tree/runtime/services/engine.log".to_string()));

        let _ = fs::remove_dir_all(root);
        let _ = fs::remove_file(archive);
    }

    #[test]
    fn parent_tree_archive_restore_rewrites_runtime_paths() {
        let old_home = std::env::var_os("TREE_HOME");
        let home = temp_test_dir("archive-home");
        std::env::set_var("TREE_HOME", &home);
        let source = temp_test_dir("archive-restore-source");
        fs::create_dir_all(source.join("materials")).unwrap();
        fs::create_dir_all(source.join("outputs")).unwrap();
        fs::create_dir_all(source.join(".tree").join("runtime")).unwrap();
        fs::write(source.join("materials").join("lecture.md"), "seed").unwrap();
        fs::write(source.join("outputs").join("001.Root.md"), "fruit").unwrap();
        fs::write(
            source
                .join(".tree")
                .join("runtime")
                .join("pipeline-state.json"),
            serde_json::json!({
                "node_runs": [{"draft_path": source.join(".tree/runtime/drafts/n1.md")}],
                "other": [source.join("outputs/001.Root.md")]
            })
            .to_string(),
        )
        .unwrap();
        let project = ProjectSummary {
            id: "proj_source".to_string(),
            name: "Source Tree".to_string(),
            description: "desc".to_string(),
            path: path_to_string(&source),
            created_at: 1,
            updated_at: 2,
            last_opened_at: 0,
            source_count: 0,
            output_count: 0,
            storage_bytes: 0,
        };
        let archive = source.with_extension("zip");
        write_project_archive(&project, &archive).unwrap();

        let restored = restore_parent_tree_archive(&archive, Some("Restored Tree")).unwrap();

        assert_eq!(restored.name, "Restored Tree");
        assert_eq!(restored.source_count, 1);
        assert_eq!(restored.output_count, 1);
        let restored_root = PathBuf::from(&restored.path);
        assert!(restored_root.join("materials").join("lecture.md").is_file());
        assert!(restored_root.join("outputs").join("001.Root.md").is_file());
        let state_text = fs::read_to_string(
            restored_root
                .join(".tree")
                .join("runtime")
                .join("pipeline-state.json"),
        )
        .unwrap();
        assert!(state_text.contains(&path_to_string(&restored_root)));
        assert!(!state_text.contains(&path_to_string(&source)));

        if let Some(value) = old_home {
            std::env::set_var("TREE_HOME", value);
        } else {
            std::env::remove_var("TREE_HOME");
        }
        let _ = fs::remove_dir_all(home);
        let _ = fs::remove_dir_all(source);
        let _ = fs::remove_file(archive);
    }

    #[test]
    fn parent_tree_archive_rejects_zip_slip_paths() {
        let root = temp_test_dir("archive-slip");
        let archive = root.join("bad.zip");
        let mut writer = StoredZipWriter::create(&archive).unwrap();
        writer.add_bytes(ARCHIVE_MANIFEST, b"{}").unwrap();
        writer.add_bytes("../evil.txt", b"nope").unwrap();
        writer.finish().unwrap();

        assert!(read_stored_zip_entries(&archive).is_err());

        let _ = fs::remove_dir_all(root);
    }

    fn temp_test_dir(label: &str) -> PathBuf {
        let path =
            std::env::temp_dir().join(format!("tree-{label}-{}", uuid::Uuid::new_v4().as_simple()));
        fs::create_dir_all(&path).unwrap();
        path
    }
}
