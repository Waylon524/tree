"""PyInstaller entry point: a self-contained ``tre`` executable.

Bundled and shipped as the desktop app's sidecar. The Tauri shell launches it
headless, e.g. ``tre-engine serve --host 127.0.0.1 --port 8799 --token <t>``.
"""

from tree.cli.app import app

if __name__ == "__main__":
    app()
