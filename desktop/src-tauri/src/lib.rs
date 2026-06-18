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
use std::io::{Read, Write};
use std::net::{TcpListener, TcpStream};
use std::path::{Path, PathBuf};
use std::process::{Child, Command, Stdio};
use std::sync::Mutex;
use std::time::{Duration, SystemTime, UNIX_EPOCH};

use serde::{Deserialize, Serialize};
use tauri::{Manager, RunEvent, State};

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

    let project_path = PathBuf::from(&project.path);
    if !is_managed_project_path(&project_path) {
        return Err("Only managed TREE projects can be deleted from the project library".to_string());
    }

    let was_current = index.current_project_id.as_deref() == Some(project.id.as_str());
    if was_current {
        stop_managed_sidecar(&state, true);
    }

    if project_path.exists() {
        fs::remove_dir_all(&project_path)
            .map_err(|error| format!("Failed to delete project files: {error}"))?;
    }

    index.projects.remove(position);
    if was_current {
        index.current_project_id = None;
    }
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
    let binary = sidecar_binary(app)
        .ok_or_else(|| "TREE sidecar binary was not found. Rebuild the desktop package.".to_string())?;
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
    let child = state
        .sidecar
        .lock()
        .ok()
        .and_then(|mut guard| guard.take());
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

fn ensure_project_switching_allowed() -> Result<(), String> {
    if external_api_config().is_some() {
        Err("Project switching is disabled while TREE_API_BASE/TREE_API_TOKEN are set".to_string())
    } else {
        Ok(())
    }
}

fn external_api_config() -> Option<ApiConfig> {
    match (std::env::var("TREE_API_BASE"), std::env::var("TREE_API_TOKEN")) {
        (Ok(base), Ok(token)) => Some(ApiConfig { base, token }),
        _ => None,
    }
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
    let bytes = fs::read(&path).map_err(|error| format!("Failed to read project index: {error}"))?;
    serde_json::from_slice(&bytes).map_err(|error| format!("Failed to parse project index: {error}"))
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
    let tmp = parent.join(format!(
        ".{}.tmp",
        uuid::Uuid::new_v4().as_simple()
    ));
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
    entries
        .flatten()
        .map(|entry| dir_size(entry.path()))
        .sum()
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
        return Err("Choose a TREE workspace containing materials, outputs, or .tree/runtime".to_string());
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
            return Err(format!("Cannot import workspace because {name} is not a directory"));
        }
        copy_dir_contents(&source_child, &target.join(name))?;
    }
    Ok(())
}

fn copy_dir_contents(source: &Path, target: &Path) -> Result<(), String> {
    fs::create_dir_all(target)
        .map_err(|error| format!("Failed to create import directory: {error}"))?;
    let entries = fs::read_dir(source)
        .map_err(|error| format!("Failed to read import source: {error}"))?;
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

fn spawn_sidecar(
    binary: PathBuf,
    root: &Path,
    port: u16,
    token: &str,
) -> Result<Child, String> {
    let root_arg = path_to_string(root);
    Command::new(binary)
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
            import_existing_project
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
        assert_eq!(validate_project_name("  Final Review  ").unwrap(), "Final Review");
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

        assert!(!has_project_name_except(&index, "biology notes", "proj_one"));
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
        fs::write(source.join("materials").join("default").join("lecture.md"), "source").unwrap();
        fs::write(source.join("outputs").join("001.md"), "output").unwrap();
        fs::write(source.join(".tree").join("runtime").join("state.json"), "{}").unwrap();

        copy_workspace_roots(&source, &target).unwrap();

        assert_eq!(
            fs::read_to_string(target.join("materials").join("default").join("lecture.md"))
                .unwrap(),
            "source"
        );
        assert_eq!(fs::read_to_string(target.join("outputs").join("001.md")).unwrap(), "output");
        assert_eq!(
            fs::read_to_string(target.join(".tree").join("runtime").join("state.json"))
                .unwrap(),
            "{}"
        );

        let _ = fs::remove_dir_all(source);
        let _ = fs::remove_dir_all(target);
    }

    fn temp_test_dir(label: &str) -> PathBuf {
        let path = std::env::temp_dir().join(format!(
            "tree-{label}-{}",
            uuid::Uuid::new_v4().as_simple()
        ));
        fs::create_dir_all(&path).unwrap();
        path
    }
}
