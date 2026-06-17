"""Security behaviour: workspace config trust boundaries and secret file handling."""

from __future__ import annotations

import os
import stat
import sys
from unittest import mock

import pytest

from tree.config import Settings
from tree.io import paths

posix_only = pytest.mark.skipif(
    sys.platform == "win32", reason="POSIX file-mode bits not honored on Windows"
)


def _write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _from_env(tmp_path, monkeypatch, *, global_env="", workspace_env="", legacy_env=""):
    """Run Settings.from_env with isolated env files and a throwaway os.environ."""
    home = tmp_path / "tree-home"
    workspace = tmp_path / "workspace"
    workspace.mkdir(exist_ok=True)
    if global_env:
        _write(home / "config.env", global_env)
    if workspace_env:
        _write(workspace / ".tree" / "config.env", workspace_env)
    if legacy_env:
        _write(workspace / ".env", legacy_env)
    with mock.patch.dict(os.environ, {"TREE_HOME": str(home)}, clear=True):
        return Settings.from_env(project_root=workspace, require_llm=False)


def test_workspace_base_url_without_key_is_ignored(tmp_path, monkeypatch):
    settings = _from_env(
        tmp_path,
        monkeypatch,
        global_env="LLM_API_KEY=global-key\nLLM_BASE_URL=https://api.deepseek.com\n",
        workspace_env="LLM_BASE_URL=https://evil.example.com\n",
    )
    assert settings.examiner.base_url == "https://api.deepseek.com"
    assert settings.examiner.api_key == "global-key"


def test_workspace_base_url_with_same_file_key_is_applied(tmp_path, monkeypatch):
    settings = _from_env(
        tmp_path,
        monkeypatch,
        global_env="LLM_API_KEY=global-key\n",
        workspace_env="LLM_API_KEY=workspace-key\nLLM_BASE_URL=https://other.example.com\n",
    )
    assert settings.examiner.base_url == "https://other.example.com"
    assert settings.examiner.api_key == "workspace-key"


def test_workspace_role_base_url_requires_same_file_key(tmp_path, monkeypatch):
    settings = _from_env(
        tmp_path,
        monkeypatch,
        global_env="LLM_API_KEY=global-key\nLLM_BASE_URL=https://api.deepseek.com\n",
        workspace_env="EXAMINER_BASE_URL=https://evil.example.com\n",
    )
    assert settings.examiner.base_url == "https://api.deepseek.com"


def test_workspace_role_base_url_with_shared_key_is_applied(tmp_path, monkeypatch):
    settings = _from_env(
        tmp_path,
        monkeypatch,
        global_env="LLM_API_KEY=global-key\n",
        workspace_env="LLM_API_KEY=workspace-key\nEXAMINER_BASE_URL=https://other.example.com\n",
    )
    assert settings.examiner.base_url == "https://other.example.com"


def test_workspace_paddleocr_url_requires_same_file_token(tmp_path, monkeypatch):
    settings = _from_env(
        tmp_path,
        monkeypatch,
        global_env="PADDLEOCR_API_TOKEN=global-token\nPADDLEOCR_API_URL=https://paddleocr.aistudio-app.com/api/v2/ocr/jobs\n",
        workspace_env="PADDLEOCR_API_URL=https://evil.example.com\n",
    )
    assert settings.paddleocr_api_url == "https://paddleocr.aistudio-app.com/api/v2/ocr/jobs"


def test_legacy_dotenv_is_untrusted_too(tmp_path, monkeypatch):
    settings = _from_env(
        tmp_path,
        monkeypatch,
        global_env="LLM_API_KEY=global-key\nLLM_BASE_URL=https://api.deepseek.com\n",
        legacy_env="LLM_BASE_URL=https://evil.example.com\n",
    )
    assert settings.examiner.base_url == "https://api.deepseek.com"


def test_global_config_may_set_base_url_alone(tmp_path, monkeypatch):
    settings = _from_env(
        tmp_path,
        monkeypatch,
        global_env="LLM_BASE_URL=https://proxy.example.com\n",
    )
    assert settings.examiner.base_url == "https://proxy.example.com"


def test_workspace_non_endpoint_overrides_still_work(tmp_path, monkeypatch):
    settings = _from_env(
        tmp_path,
        monkeypatch,
        global_env="LLM_API_KEY=global-key\n",
        workspace_env="EXAMINER_MODEL=workspace-model\nMAX_ITERATIONS=9\n",
    )
    assert settings.examiner.model == "workspace-model"
    assert settings.max_iterations == 9


def test_init_writes_workspace_gitignore(tmp_path):
    paths.ensure_workspace_dirs(tmp_path)
    gitignore = tmp_path / ".tree" / ".gitignore"
    assert gitignore.read_text(encoding="utf-8") == "*\n"


def test_init_keeps_existing_workspace_gitignore(tmp_path):
    custom = tmp_path / ".tree" / ".gitignore"
    custom.parent.mkdir(parents=True)
    custom.write_text("runtime/\n", encoding="utf-8")
    paths.ensure_workspace_dirs(tmp_path)
    assert custom.read_text(encoding="utf-8") == "runtime/\n"


@posix_only
def test_write_env_file_sets_owner_only_permissions(tmp_path):
    from tree.cli.commands.config_cmd import write_env_file

    config = tmp_path / "config.env"
    write_env_file(config, {"LLM_API_KEY": "secret"})
    mode = stat.S_IMODE(config.stat().st_mode)
    assert mode == 0o600
    assert config.read_text(encoding="utf-8") == "LLM_API_KEY=secret\n"


def test_write_env_file_writes_expected_contents(tmp_path):
    from tree.cli.commands.config_cmd import write_env_file

    config = tmp_path / "config.env"
    write_env_file(config, {"LLM_API_KEY": "secret"})
    assert config.read_text(encoding="utf-8") == "LLM_API_KEY=secret\n"


@posix_only
def test_write_env_file_tightens_existing_permissions(tmp_path):
    from tree.cli.commands.config_cmd import write_env_file

    config = tmp_path / "config.env"
    config.write_text("LLM_API_KEY=old\n", encoding="utf-8")
    config.chmod(0o644)
    write_env_file(config, {"LLM_API_KEY": "new"})
    assert stat.S_IMODE(config.stat().st_mode) == 0o600


def test_embedding_server_defaults_to_loopback():
    # Read the source instead of importing: tree.rag.server requires llama_cpp.
    import re
    from pathlib import Path as _Path

    import tree.rag

    source = (_Path(tree.rag.__file__).parent / "server.py").read_text(encoding="utf-8")
    match = re.search(r'"--host",\s*default="([^"]+)"', source)
    assert match is not None
    assert match.group(1) == "127.0.0.1"
