"""Resolve / download the prebuilt llama.cpp ``llama-server`` binary.

``llama-server`` exposes an OpenAI-compatible ``/v1/embeddings`` endpoint and is
published as per-platform prebuilt archives on the llama.cpp GitHub releases.
Using it lets TREE host a local embedding server without the ``llama-cpp-python``
toolchain (the [local-embed] extra), which is the hard part on native Windows.

Resolution order:
  1. ``LLAMA_SERVER_BIN``         explicit binary path
  2. TREE-managed cache           ``~/.tree/bin/llama-cpp-<tag>/.../llama-server``
  3. ``PATH``                     a system-installed ``llama-server``
  4. auto-download                the pinned release asset for this platform
"""

from __future__ import annotations

import logging
import os
import platform
import shutil
import sys
import tarfile
import tempfile
import urllib.request
import zipfile
from pathlib import Path

from tree.config import DEFAULT_LLAMA_SERVER_CTX
from tree.io import paths

logger = logging.getLogger(__name__)

# Pinned default release. Bump together with the asset-name format below; override
# at runtime with LLAMA_SERVER_VERSION or LLAMA_SERVER_DOWNLOAD_URL.
DEFAULT_VERSION = "b9670"
_RELEASE_BASE = "https://github.com/ggml-org/llama.cpp/releases/download"


class LlamaServerError(RuntimeError):
    """Raised when the llama-server binary cannot be resolved or downloaded."""


def binary_name() -> str:
    return "llama-server.exe" if sys.platform == "win32" else "llama-server"


def resolve_llama_server() -> Path | None:
    """Return a usable llama-server binary path without downloading, or None."""
    env = os.environ.get("LLAMA_SERVER_BIN", "").strip()
    if env:
        candidate = Path(env).expanduser()
        if candidate.is_file():
            return candidate

    cached = _find_binary(paths.llama_server_cache_root())
    if cached is not None:
        return cached

    on_path = shutil.which(binary_name())
    return Path(on_path) if on_path else None


def ensure_llama_server() -> Path:
    """Resolve a llama-server binary, downloading the pinned release if needed."""
    found = resolve_llama_server()
    if found is not None:
        return found

    if not _env_bool("LLAMA_SERVER_AUTO_DOWNLOAD", True):
        raise LlamaServerError(
            "llama-server binary not found and LLAMA_SERVER_AUTO_DOWNLOAD=false. "
            "Set LLAMA_SERVER_BIN to a binary, or enable automatic download."
        )

    tag = os.environ.get("LLAMA_SERVER_VERSION", "").strip() or DEFAULT_VERSION
    dest = paths.llama_server_cache_root() / f"llama-cpp-{tag}"
    try:
        _download_and_extract(tag, dest)
    except LlamaServerError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise LlamaServerError(
            f"Failed to download llama-server ({tag}) for this platform. Check network "
            "access, or set LLAMA_SERVER_BIN to a manually installed binary."
        ) from exc

    binary = _find_binary(dest)
    if binary is None:
        raise LlamaServerError(
            f"Downloaded llama-server archive did not contain {binary_name()} (under {dest})."
        )
    if sys.platform != "win32":
        binary.chmod(0o755)
    return binary


def build_argv(binary: Path, gguf: Path, *, host: str, port: int) -> list[str]:
    """Build the launch argv for the embedding server."""
    argv = [
        str(binary),
        "-m",
        str(gguf),
        "--embeddings",
        "--host",
        host,
        "--port",
        str(port),
        "-c",
        os.environ.get("LLAMA_SERVER_CTX", "").strip() or str(DEFAULT_LLAMA_SERVER_CTX),
    ]
    gpu_layers = _gpu_layers_override()
    if gpu_layers:
        argv += ["-ngl", gpu_layers]
    pooling = os.environ.get("LLAMA_SERVER_POOLING", "").strip()
    if pooling:
        argv += ["--pooling", pooling]
    return argv


def _gpu_layers_override() -> str:
    configured = os.environ.get("LLAMA_SERVER_N_GPU_LAYERS")
    if configured is not None:
        return configured.strip()
    if sys.platform == "darwin":
        # llama.cpp b9670's Metal embedding path can terminate on longer Chinese MTUs.
        return "0"
    return ""


# --- platform / asset mapping (pure, unit-tested) ----------------------------

def platform_token(system: str | None = None, machine: str | None = None) -> str:
    """Map a platform to the llama.cpp release asset platform token (e.g. ``win-cpu-x64``)."""
    system = (system or sys.platform).lower()
    machine = (machine or platform.machine()).lower()
    if machine in {"arm64", "aarch64"}:
        arch = "arm64"
    elif machine in {"x86_64", "amd64", "x64"}:
        arch = "x64"
    else:
        raise LlamaServerError(f"Unsupported CPU architecture for llama-server: {machine!r}")

    if system == "win32":
        return f"win-cpu-{arch}"
    if system == "darwin":
        return f"macos-{arch}"
    if system.startswith("linux"):
        return f"ubuntu-{arch}"
    raise LlamaServerError(f"Unsupported platform for llama-server: {system!r}")


def asset_name(tag: str, *, system: str | None = None, machine: str | None = None) -> str:
    token = platform_token(system, machine)
    ext = "zip" if (system or sys.platform).lower() == "win32" else "tar.gz"
    return f"llama-{tag}-bin-{token}.{ext}"


def download_url(tag: str, *, system: str | None = None, machine: str | None = None) -> str:
    override = os.environ.get("LLAMA_SERVER_DOWNLOAD_URL", "").strip()
    if override:
        return override
    return f"{_RELEASE_BASE}/{tag}/{asset_name(tag, system=system, machine=machine)}"


# --- internals ---------------------------------------------------------------

def _download_and_extract(tag: str, dest: Path) -> None:
    url = download_url(tag)
    dest.mkdir(parents=True, exist_ok=True)
    logger.info("Downloading llama-server %s from %s", tag, url)
    suffix = ".zip" if url.endswith(".zip") else ".tar.gz"
    request = urllib.request.Request(url, headers={"User-Agent": "tree-engine"})
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
        tmp_path = Path(tmp.name)
        with urllib.request.urlopen(request) as resp:  # noqa: S310 - pinned https GitHub URL
            shutil.copyfileobj(resp, tmp)
    try:
        _extract_archive(tmp_path, dest)
    finally:
        tmp_path.unlink(missing_ok=True)


def _extract_archive(archive: Path, dest: Path) -> None:
    if archive.name.endswith(".zip"):
        with zipfile.ZipFile(archive) as zf:
            zf.extractall(dest)
        return
    with tarfile.open(archive, "r:gz") as tf:
        # filter="data" (py>=3.12) blocks path traversal / unsafe members.
        tf.extractall(dest, filter="data")


def _find_binary(root: Path) -> Path | None:
    if not root.exists():
        return None
    name = binary_name()
    for candidate in sorted(root.rglob(name)):
        if candidate.is_file():
            return candidate
    return None


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() not in {"0", "false", "no", "off"}
