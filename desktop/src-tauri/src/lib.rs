//! TREE desktop shell: spawns the Python engine sidecar (`tre serve`) and hands
//! the React frontend its loopback API base + token via the `api_config` command.
//!
//! Sidecar resolution order:
//!   1. `TREE_SIDECAR_BIN` (explicit path),
//!   2. bundled resource `tre-engine/tre-engine[.exe]` (production installer),
//!   3. the dev PyInstaller build under `packaging/dist`.
//! For dev against a manually-run server, set `TREE_API_BASE` + `TREE_API_TOKEN`.

use std::net::TcpListener;
use std::path::PathBuf;
use std::process::{Child, Command, Stdio};
use std::sync::Mutex;

use tauri::{Manager, RunEvent, State};

struct ApiConfig {
    base: String,
    token: String,
}

struct Sidecar(Mutex<Option<Child>>);

#[tauri::command]
fn api_config(config: State<'_, ApiConfig>) -> serde_json::Value {
    serde_json::json!({ "base": config.base, "token": config.token })
}

fn free_port() -> u16 {
    TcpListener::bind("127.0.0.1:0")
        .and_then(|listener| listener.local_addr())
        .map(|addr| addr.port())
        .unwrap_or(8799)
}

fn default_workspace() -> PathBuf {
    let home = std::env::var("HOME")
        .or_else(|_| std::env::var("USERPROFILE"))
        .unwrap_or_else(|_| ".".to_string());
    PathBuf::from(home).join("TREE")
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

fn spawn_sidecar(app: &tauri::AppHandle, port: u16, token: &str) -> Option<Child> {
    let binary = sidecar_binary(app)?;
    let root = default_workspace();
    let _ = std::fs::create_dir_all(&root);
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
            &root.to_string_lossy(),
        ])
        .stdout(Stdio::null())
        .stderr(Stdio::null())
        .spawn()
        .ok()
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        .invoke_handler(tauri::generate_handler![api_config])
        .setup(|app| {
            if cfg!(debug_assertions) {
                app.handle().plugin(
                    tauri_plugin_log::Builder::default()
                        .level(log::LevelFilter::Info)
                        .build(),
                )?;
            }
            let (base, token, child) =
                match (std::env::var("TREE_API_BASE"), std::env::var("TREE_API_TOKEN")) {
                    (Ok(base), Ok(token)) => (base, token, None),
                    _ => {
                        let port = free_port();
                        let token = uuid::Uuid::new_v4().as_simple().to_string();
                        let child = spawn_sidecar(app.handle(), port, &token);
                        (format!("http://127.0.0.1:{port}"), token, child)
                    }
                };
            app.manage(ApiConfig { base, token });
            app.manage(Sidecar(Mutex::new(child)));
            Ok(())
        })
        .build(tauri::generate_context!())
        .expect("error while building tauri application")
        .run(|app_handle, event| {
            if let RunEvent::Exit = event {
                if let Some(sidecar) = app_handle.try_state::<Sidecar>() {
                    if let Ok(mut guard) = sidecar.0.lock() {
                        if let Some(mut child) = guard.take() {
                            let _ = child.kill();
                        }
                    }
                }
            }
        });
}
