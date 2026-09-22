"""Make the pip-shipped CUDA libraries findable on Windows.

`nvidia-cublas-cu12` and `nvidia-cudnn-cu12` install their DLLs under
`site-packages/nvidia/<component>/bin/`. On Linux the consuming extension finds
them through its RPATH; on Windows nothing points there, so CTranslate2 fails
at model construction with "Library cublas64_12.dll is not found or cannot be
loaded" even though the file is sitting in the environment.

The fix is to register those directories with the OS loader before the runtime
is imported. This module must therefore be used *before* `import ctranslate2`,
which is why the engine and the capability probe both go through it.
"""

from __future__ import annotations

import ctypes
import os
import sys
from functools import cache
from pathlib import Path

#: Loaded explicitly to prove the environment works rather than assuming it.
#: cuBLAS is what CTranslate2 fails on first; cuDNN is what it fails on next.
REQUIRED_DLLS = ("cublas64_12.dll", "cublasLt64_12.dll", "cudnn64_9.dll")


def nvidia_dll_dirs() -> list[Path]:
    """Every `nvidia/*/bin` directory present in this interpreter's environment."""
    found: list[Path] = []
    seen: set[Path] = set()
    for entry in sys.path:
        if not entry:
            continue
        root = Path(entry) / "nvidia"
        if not root.is_dir():
            continue
        for component in sorted(root.iterdir()):
            bin_dir = component / "bin"
            if bin_dir.is_dir() and bin_dir not in seen:
                seen.add(bin_dir)
                found.append(bin_dir)
    return found


@cache
def prepare_cuda_dll_path() -> tuple[Path, ...]:
    """Register the CUDA DLL directories with the loader. Idempotent.

    Cached because `os.add_dll_directory` returns a handle that must stay
    alive for the registration to hold; keeping them in the cache keeps them
    alive for the life of the process.
    """
    if os.name != "nt":
        return ()

    handles = []
    registered: list[Path] = []
    for directory in nvidia_dll_dirs():
        try:
            handles.append(os.add_dll_directory(str(directory)))
            registered.append(directory)
        except OSError:
            continue
    # Also extend PATH: some loaders consult it, and a child process started
    # by the engine inherits it.
    if registered:
        existing = os.environ.get("PATH", "")
        additions = os.pathsep.join(str(p) for p in registered)
        if additions not in existing:
            os.environ["PATH"] = additions + os.pathsep + existing

    prepare_cuda_dll_path._handles = handles  # type: ignore[attr-defined]
    return tuple(registered)


def load_required_dlls() -> tuple[bool, str]:
    """Actually load the CUDA libraries, and say which one failed.

    Counting CUDA devices is not this test: that asks the *driver*, which is
    installed system-wide and answers happily while cuBLAS is nowhere the
    loader can see it. The failure this catches is exactly the one that
    otherwise surfaces minutes later, after a multi-gigabyte model download.
    """
    if os.name != "nt":
        return True, ""

    prepare_cuda_dll_path()
    for name in REQUIRED_DLLS:
        try:
            ctypes.WinDLL(name)
        except OSError as exc:
            return False, (
                f"a biblioteca '{name}' nao carregou ({exc}). "
                f"Diretorios registrados: "
                f"{', '.join(str(p) for p in nvidia_dll_dirs()) or 'nenhum'}"
            )
    return True, ""
