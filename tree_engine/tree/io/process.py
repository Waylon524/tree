"""Cross-platform process helpers for TREE-managed background services.

POSIX uses ``start_new_session`` + signals; Windows uses detached process
creation flags + ``TerminateProcess`` via ``os.kill``. Keeping the platform
branches here lets ``rag.service`` and ``cli.lifecycle`` stay platform-neutral.

The ``sys.platform == "win32"`` checks are written inline (not via a helper
constant) so that static type checkers narrow the Windows-only stdlib APIs.
"""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import IO, Sequence


def spawn_detached(
    args: Sequence[str],
    *,
    cwd: str | Path | None = None,
    stdout: IO[bytes] | int | None = None,
    stderr: IO[bytes] | int | None = None,
) -> subprocess.Popen[bytes]:
    """Start a background process detached from the current console/session.

    The child survives the launching terminal closing on both POSIX and Windows.
    """
    if sys.platform == "win32":
        # Detach from the console and start a new process group so the child is
        # not killed when the launching terminal window closes.
        creationflags = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS
        return subprocess.Popen(
            args, cwd=cwd, stdout=stdout, stderr=stderr, creationflags=creationflags
        )
    return subprocess.Popen(args, cwd=cwd, stdout=stdout, stderr=stderr, start_new_session=True)


def pid_alive(pid: int) -> bool:
    """Return True if a process with ``pid`` is currently running."""
    if sys.platform == "win32":
        return _windows_pid_alive(pid)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        # Process exists but is owned by someone else.
        return True
    return True


def terminate_pid(pid: int, *, force: bool = False) -> None:
    """Terminate ``pid`` if it exists. ``force`` escalates to SIGKILL on POSIX.

    On Windows ``os.kill`` maps any non-CTRL signal to ``TerminateProcess``,
    which is already a hard kill, so ``force`` is a no-op there.
    """
    if sys.platform == "win32":
        try:
            os.kill(pid, signal.SIGTERM)  # -> TerminateProcess on Windows
        except (ProcessLookupError, OSError):
            return
        return

    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    if force:
        time.sleep(0.1)
        if pid_alive(pid):
            try:
                os.kill(pid, signal.SIGKILL)
            except ProcessLookupError:
                return


def _windows_pid_alive(pid: int) -> bool:
    if sys.platform != "win32":  # pragma: no cover - guards type narrowing only
        raise RuntimeError("_windows_pid_alive called on a non-Windows platform")

    import ctypes
    from ctypes import wintypes

    SYNCHRONIZE = 0x00100000
    WAIT_TIMEOUT = 0x00000102

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
    kernel32.WaitForSingleObject.restype = wintypes.DWORD
    kernel32.WaitForSingleObject.argtypes = (wintypes.HANDLE, wintypes.DWORD)
    kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)

    handle = kernel32.OpenProcess(SYNCHRONIZE, False, pid)
    if not handle:
        return False
    try:
        return bool(kernel32.WaitForSingleObject(handle, 0) == WAIT_TIMEOUT)
    finally:
        kernel32.CloseHandle(handle)
