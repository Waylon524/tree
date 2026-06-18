"""Unit tests for prebuilt llama-server resolution (no network)."""

from __future__ import annotations

import pytest

from tree.rag import llama_server


@pytest.mark.parametrize(
    "system,machine,expected",
    [
        ("win32", "AMD64", "win-cpu-x64"),
        ("win32", "ARM64", "win-cpu-arm64"),
        ("darwin", "arm64", "macos-arm64"),
        ("darwin", "x86_64", "macos-x64"),
        ("linux", "x86_64", "ubuntu-x64"),
        ("linux", "aarch64", "ubuntu-arm64"),
    ],
)
def test_platform_token(system, machine, expected):
    assert llama_server.platform_token(system, machine) == expected


def test_platform_token_rejects_unknown_arch():
    with pytest.raises(llama_server.LlamaServerError):
        llama_server.platform_token("linux", "mips")


def test_platform_token_rejects_unknown_os():
    with pytest.raises(llama_server.LlamaServerError):
        llama_server.platform_token("sunos", "x86_64")


def test_asset_name_and_url_match_real_release_naming():
    # Matches the actual ggml-org/llama.cpp asset naming verified against the API.
    assert llama_server.asset_name("b9670", system="win32", machine="AMD64") == (
        "llama-b9670-bin-win-cpu-x64.zip"
    )
    assert llama_server.asset_name("b9670", system="darwin", machine="arm64") == (
        "llama-b9670-bin-macos-arm64.tar.gz"
    )
    assert llama_server.asset_name("b9670", system="linux", machine="x86_64") == (
        "llama-b9670-bin-ubuntu-x64.tar.gz"
    )
    url = llama_server.download_url("b9670", system="linux", machine="x86_64")
    assert url == (
        "https://github.com/ggml-org/llama.cpp/releases/download/"
        "b9670/llama-b9670-bin-ubuntu-x64.tar.gz"
    )


def test_download_url_honors_override(monkeypatch):
    monkeypatch.setenv("LLAMA_SERVER_DOWNLOAD_URL", "https://example.com/custom.tar.gz")
    assert llama_server.download_url("b9670", system="linux", machine="x86_64") == (
        "https://example.com/custom.tar.gz"
    )


def test_build_argv_contains_required_flags(tmp_path, monkeypatch):
    monkeypatch.setattr(llama_server.sys, "platform", "linux")
    binary = tmp_path / "llama-server"
    gguf = tmp_path / "model.gguf"
    argv = llama_server.build_argv(binary, gguf, host="127.0.0.1", port=8788)
    assert argv[0] == str(binary)
    assert argv[1:3] == ["-m", str(gguf)]
    assert "--embeddings" in argv
    assert argv[argv.index("--host") + 1] == "127.0.0.1"
    assert argv[argv.index("--port") + 1] == "8788"
    assert "--pooling" not in argv  # default: rely on model metadata


def test_build_argv_defaults_to_cpu_only_on_macos(tmp_path, monkeypatch):
    monkeypatch.setattr(llama_server.sys, "platform", "darwin")
    argv = llama_server.build_argv(tmp_path / "b", tmp_path / "m.gguf", host="h", port=1)
    assert argv[argv.index("-ngl") + 1] == "0"


def test_build_argv_honors_gpu_layers_override(tmp_path, monkeypatch):
    monkeypatch.setattr(llama_server.sys, "platform", "darwin")
    monkeypatch.setenv("LLAMA_SERVER_N_GPU_LAYERS", "12")
    argv = llama_server.build_argv(tmp_path / "b", tmp_path / "m.gguf", host="h", port=1)
    assert argv[argv.index("-ngl") + 1] == "12"


def test_build_argv_allows_gpu_layers_override_to_disable_flag(tmp_path, monkeypatch):
    monkeypatch.setattr(llama_server.sys, "platform", "darwin")
    monkeypatch.setenv("LLAMA_SERVER_N_GPU_LAYERS", "")
    argv = llama_server.build_argv(tmp_path / "b", tmp_path / "m.gguf", host="h", port=1)
    assert "-ngl" not in argv


def test_build_argv_adds_pooling_override(tmp_path, monkeypatch):
    monkeypatch.setattr(llama_server.sys, "platform", "linux")
    monkeypatch.setenv("LLAMA_SERVER_POOLING", "last")
    argv = llama_server.build_argv(tmp_path / "b", tmp_path / "m.gguf", host="h", port=1)
    assert argv[argv.index("--pooling") + 1] == "last"


def test_resolve_prefers_explicit_bin_env(tmp_path, monkeypatch):
    binary = tmp_path / llama_server.binary_name()
    binary.write_text("x", encoding="utf-8")
    monkeypatch.setenv("LLAMA_SERVER_BIN", str(binary))
    assert llama_server.resolve_llama_server() == binary


def test_resolve_finds_binary_in_cache(tmp_path, monkeypatch):
    monkeypatch.delenv("LLAMA_SERVER_BIN", raising=False)
    monkeypatch.setenv("LLAMA_SERVER_CACHE_DIR", str(tmp_path / "cache"))
    nested = tmp_path / "cache" / "llama-cpp-b9670" / "build" / "bin"
    nested.mkdir(parents=True)
    binary = nested / llama_server.binary_name()
    binary.write_text("x", encoding="utf-8")
    # Ensure PATH lookup cannot interfere.
    monkeypatch.setattr(llama_server.shutil, "which", lambda name: None)
    assert llama_server.resolve_llama_server() == binary


def test_ensure_raises_when_missing_and_download_disabled(tmp_path, monkeypatch):
    monkeypatch.delenv("LLAMA_SERVER_BIN", raising=False)
    monkeypatch.setenv("LLAMA_SERVER_CACHE_DIR", str(tmp_path / "empty-cache"))
    monkeypatch.setenv("LLAMA_SERVER_AUTO_DOWNLOAD", "false")
    monkeypatch.setattr(llama_server.shutil, "which", lambda name: None)
    with pytest.raises(llama_server.LlamaServerError, match="AUTO_DOWNLOAD"):
        llama_server.ensure_llama_server()


def test_ensure_downloads_and_locates_binary(tmp_path, monkeypatch):
    monkeypatch.delenv("LLAMA_SERVER_BIN", raising=False)
    monkeypatch.setenv("LLAMA_SERVER_CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.setattr(llama_server.shutil, "which", lambda name: None)

    def _fake_download(tag, dest):
        nested = dest / "build" / "bin"
        nested.mkdir(parents=True)
        (nested / llama_server.binary_name()).write_text("#!/bin/sh\n", encoding="utf-8")

    monkeypatch.setattr(llama_server, "_download_and_extract", _fake_download)

    binary = llama_server.ensure_llama_server()
    assert binary.name == llama_server.binary_name()
    assert binary.is_file()
