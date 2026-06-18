//! TREE desktop shell: spawns the Python engine sidecar (`tre serve`) and hands
//! the React frontend its loopback API base + token via the `api_config` command.
//!
//! Sidecar resolution: `TREE_SIDECAR_BIN` (path to the bundled/standalone tre
//! binary) if set; otherwise the dev PyInstaller build under packaging/dist.
//! If no binary is found (dev with a manually-run `tre serve`), set
//! `TREE_API_BASE` + `TREE_API_TOKEN` and the shell will use those instead.

use std::net::TcpListener;
use std::path::PathBuf;
use std::process::{Child, Command, Stdio};
use std::sync::Mutex;

use tauri::{RunEvent, State};

struct ApiConfig {
    base: String,
    token: String,
}

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

/// Resolve the sidecar binary: explicit env, then the dev PyInstaller build.
fn sidecar_binary() -> Option<PathBuf> {
    if let Ok(explicit) = std::env::var("TREE_SIDECAR_BIN") {
        let path = PathBuf::from(explicit);
        if path.is_file() {
            return Some(path);
        }
    }
    let exe_name = if cfg!(windows) { "tre-engine.exe" } else { "tre-engine" };
    let dev = PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .join("../../packaging/dist/tre-engine")
        .join(exe_name);
    dev.is_file().then_some(dev)
}

/// Spawn the headless engine server. None when no sidecar binary is available
/// (dev: run `tre serve --port <p> --token <t>` yourself and use TREE_API_*).
fn spawn_sidecar(port: u16, token: &str) -> Option<Child> {
    let binary = sidecar_binary()?;
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
    // Prefer an externally-managed server (dev) when TREE_API_* is set.
    let (base, token, child) =
        match (std::env::var("TREE_API_BASE"), std::env::var("TREE_API_TOKEN")) {
            (Ok(base), Ok(token)) => (base, token, None),
            _ => {
                let port = free_port();
                let token = uuid::Uuid::new_v4().as_simple().to_string();
                let child = spawn_sidecar(port, &token);
                (format!("http://127.0.0.1:{port}"), token, child)
            }
        };
    let sidecar: Mutex<Option<Child>> = Mutex::new(child);

    tauri::Builder::default()
        .manage(ApiConfig { base, token })
        .invoke_handler(tauri::generate_handler![api_config])
        .setup(|app| {
            if cfg!(debug_assertions) {
                app.handle().plugin(
                    tauri_plugin_log::Builder::default()
                        .level(log::LevelFilter::Info)
                        .build(),
                )?;
            }
            Ok(())
        })
        .build(tauri::generate_context!())
        .expect("error while building tauri application")
        .run(move |_app_handle, event| {
            if let RunEvent::Exit = event {
                if let Ok(mut guard) = sidecar.lock() {
                    if let Some(mut process) = guard.take() {
                        let _ = process.kill();
                    }
                }
            }
        });
}
