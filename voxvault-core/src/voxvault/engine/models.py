"""Transcription models on disk: where they come from, and the one download.

The engine loads a model only from the data directory (``local_files_only``)
and every process of the core runs with the Hugging Face client offline, so a
transcription never opens a connection. Getting a model there is this module's
job, and it happens only when somebody asks: the first-use preparation, a model
change in the settings, or ``voxvault models download``.

The download is this module's own, and resumes. The Hugging Face client used
to continue from its ``.incomplete`` files; since 1.x it writes each download
to a name unique to the process and deletes it on failure, so an interrupted
model -- 1.6 GB on a home connection -- started again from zero, and a killed
process left its partial behind for good. Here every file goes to one fixed
partial per file, continued with an HTTP ``Range`` request, checked against the
repository's own hash, and only then put where the engine looks: the snapshot
of the client's cache layout, with the branch reference beside it.

Progress is the bytes actually received, counted as they arrive and reported
four times a second together with what the partials already held.
"""

from __future__ import annotations

import fnmatch
import hashlib
import os
import threading
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from .base import MissingPrerequisiteError

#: The faster-whisper conversions, by the names the configuration uses. The
#: same mapping faster-whisper itself resolves, kept here so that the command
#: line can answer without importing the inference runtime.
REPOSITORIES: Final[dict[str, str]] = {
    "tiny": "Systran/faster-whisper-tiny",
    "base": "Systran/faster-whisper-base",
    "small": "Systran/faster-whisper-small",
    "medium": "Systran/faster-whisper-medium",
    "large-v1": "Systran/faster-whisper-large-v1",
    "large-v2": "Systran/faster-whisper-large-v2",
    "large-v3": "Systran/faster-whisper-large-v3",
    "large-v3-turbo": "mobiuslabsgmbh/faster-whisper-large-v3-turbo",
    "distil-large-v3": "Systran/faster-distil-whisper-large-v3",
}

#: The files a model is made of -- the same list faster-whisper downloads.
FILES: Final = [
    "config.json",
    "preprocessor_config.json",
    "model.bin",
    "tokenizer.json",
    "vocabulary.*",
]

#: What a complete model has, whatever its vocabulary file is called.
REQUIRED: Final = ("config.json", "model.bin", "tokenizer.json")

#: How often progress is reported while downloading.
PROGRESS_INTERVAL_S: Final = 0.25

#: Where the partial files wait, inside the repository's cache folder.
PARTIAL_DIR: Final = ".voxvault-parcial"

CHUNK: Final = 1 << 20
TIMEOUT_S: Final = 60

Progress = Callable[[int, int], None]


class DownloadCorrupted(RuntimeError):
    """A file arrived whole and different from what the repository says."""


@dataclass(frozen=True, slots=True)
class RemoteFile:
    name: str
    size: int
    #: SHA-256 of the content for files kept in LFS, None for the small ones.
    sha256: str | None
    #: Git's blob hash, which every file has.
    blob_id: str | None


@dataclass(frozen=True, slots=True)
class RemoteModel:
    commit: str
    files: tuple[RemoteFile, ...]

    @property
    def total(self) -> int:
        return sum(f.size for f in self.files)


def repository(model: str) -> str:
    try:
        return REPOSITORIES[model]
    except KeyError:
        known = ", ".join(sorted(REPOSITORIES))
        raise ValueError(f"Modelo desconhecido: '{model}'. Conhecidos: {known}.") from None


def cache_folder(models_dir: Path, model: str) -> Path:
    """The folder the Hugging Face cache layout gives this model's repository."""
    return Path(models_dir) / ("models--" + repository(model).replace("/", "--"))


def missing_model(model: str, models_dir: Path) -> MissingPrerequisiteError:
    return MissingPrerequisiteError(
        f"modelo {model}",
        f"o modelo nao esta em {models_dir}. Baixe-o nas Configuracoes do "
        f"aplicativo ou com: voxvault models download --model {model}",
    )


def _remote_model(repo: str) -> RemoteModel:
    """The commit the default branch points at, and the model's files there."""
    from huggingface_hub import HfApi

    info = HfApi().model_info(repo, files_metadata=True)
    files = tuple(
        RemoteFile(
            name=sibling.rfilename,
            size=int(sibling.size or 0),
            sha256=getattr(sibling.lfs, "sha256", None) if sibling.lfs else None,
            blob_id=sibling.blob_id,
        )
        for sibling in info.siblings or []
        if any(fnmatch.fnmatch(sibling.rfilename, pattern) for pattern in FILES)
    )
    return RemoteModel(commit=info.sha or "", files=files)


def _file_url(repo: str, commit: str, name: str) -> str:
    from huggingface_hub import hf_hub_url

    return hf_hub_url(repo, name, revision=commit)


def _open(url: str, start: int):
    """The response for ``url`` from byte ``start``; redirects are followed."""
    headers = {"User-Agent": "voxvault"}
    if start:
        headers["Range"] = f"bytes={start}-"
    return urllib.request.urlopen(urllib.request.Request(url, headers=headers),
                                  timeout=TIMEOUT_S)


def _matches(path: Path, remote: RemoteFile) -> bool:
    """The file is the repository's: its SHA-256, or its git blob hash."""
    if remote.sha256:
        digest = hashlib.sha256()
        prefix = b""
    elif remote.blob_id:
        digest = hashlib.sha1()  # git's own content address, not a security hash
        prefix = f"blob {remote.size}\0".encode()
    else:
        return path.stat().st_size == remote.size
    digest.update(prefix)
    with path.open("rb") as source:
        for block in iter(lambda: source.read(CHUNK), b""):
            digest.update(block)
    return digest.hexdigest() == (remote.sha256 or remote.blob_id)


class _Counter:
    def __init__(self, start: int) -> None:
        self._value = start
        self._lock = threading.Lock()

    def add(self, amount: int) -> None:
        with self._lock:
            self._value += amount

    @property
    def value(self) -> int:
        with self._lock:
            return self._value


def _fetch(url: str, partial: Path, remote: RemoteFile, counter: _Counter) -> None:
    """Bring ``partial`` to the whole file, continuing from what it has."""
    start = partial.stat().st_size if partial.exists() else 0
    if start > remote.size:
        counter.add(-start)
        partial.unlink()
        start = 0
    if start == remote.size:
        return
    with _open(url, start) as response:
        if start and getattr(response, "status", 200) != 206:
            # The server sent the whole file instead of the rest of it.
            counter.add(-start)
            start = 0
        with partial.open("ab" if start else "wb") as out:
            for block in iter(lambda: response.read(CHUNK), b""):
                out.write(block)
                counter.add(len(block))


def download(models_dir: Path, model: str, *, on_progress: Progress | None = None) -> Path:
    """Fetch a model into the data directory, resuming whatever is there.

    ``on_progress(downloaded, total)`` is called every quarter of a second and
    once more at the end. Returns the folder the engine will load it from.
    """
    # The one process of the core that goes online, and only to this host.
    os.environ.pop("HF_HUB_OFFLINE", None)
    os.environ["HF_HUB_DISABLE_TELEMETRY"] = "1"

    repo = repository(model)
    folder = cache_folder(models_dir, model)
    remote = _remote_model(repo)
    snapshot = folder / "snapshots" / remote.commit
    partials = folder / PARTIAL_DIR
    snapshot.mkdir(parents=True, exist_ok=True)
    partials.mkdir(parents=True, exist_ok=True)

    def already(remote_file: RemoteFile) -> int:
        final = snapshot / remote_file.name
        if final.is_file() and final.stat().st_size == remote_file.size:
            return remote_file.size
        partial = partials / (remote_file.name + ".incomplete")
        return partial.stat().st_size if partial.exists() else 0

    counter = _Counter(sum(already(f) for f in remote.files))
    stop = threading.Event()

    def report() -> None:
        while not stop.wait(PROGRESS_INTERVAL_S):
            if on_progress is not None:
                on_progress(min(counter.value, remote.total), remote.total)

    watcher = threading.Thread(target=report, name="voxvault-download", daemon=True)
    watcher.start()
    try:
        for remote_file in remote.files:
            final = snapshot / remote_file.name
            if final.is_file() and final.stat().st_size == remote_file.size:
                continue
            partial = partials / (remote_file.name + ".incomplete")
            partial.parent.mkdir(parents=True, exist_ok=True)
            _fetch(_file_url(repo, remote.commit, remote_file.name), partial,
                   remote_file, counter)
            if not _matches(partial, remote_file):
                counter.add(-partial.stat().st_size)
                partial.unlink()
                raise DownloadCorrupted(
                    f"{remote_file.name} chegou diferente do que o repositorio "
                    f"{repo} publica; o arquivo foi descartado. Tente de novo."
                )
            final.parent.mkdir(parents=True, exist_ok=True)
            os.replace(partial, final)
    finally:
        stop.set()
        watcher.join()

    # The engine finds the snapshot through the branch reference: written last,
    # so a model is visible only once every file is in place.
    refs = folder / "refs"
    refs.mkdir(parents=True, exist_ok=True)
    temporary = refs / "main.tmp"
    temporary.write_text(remote.commit, encoding="utf-8")
    os.replace(temporary, refs / "main")
    try:
        partials.rmdir()
    except OSError:
        pass
    if on_progress is not None:
        on_progress(remote.total, remote.total)
    return snapshot


def installed(models_dir: Path, model: str) -> Path | None:
    """The model's folder if every file is on disk, without any network.

    The snapshot folder alone proves nothing: a download writes the reference
    only at the end, but a folder can be left behind by anything that stopped
    halfway, and so every file the engine needs is checked for.
    """
    try:
        from huggingface_hub import snapshot_download

        folder = Path(snapshot_download(
            repository(model), cache_dir=str(models_dir), allow_patterns=FILES,
            local_files_only=True,
        ))
    except Exception:
        return None
    complete = all((folder / name).is_file() for name in REQUIRED) and any(
        folder.glob("vocabulary.*")
    )
    return folder if complete else None
