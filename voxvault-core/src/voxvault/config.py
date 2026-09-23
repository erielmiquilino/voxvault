"""Shared configuration with an explicit precedence chain.

Every VoxVault process -- the resident service, the command line, the MCP
servers launched by an agent client, and the desktop app -- must resolve the
same configuration. An MCP client launches its servers from whatever working
directory it happens to have, so nothing here may be derived from the current
working directory: doing that is how two processes end up quietly pointed at
two different stores.

Precedence, strongest first:

    1. explicit argument passed at the call site
    2. environment variable
    3. the user's configuration file, at a fixed per-user path
    4. built-in default

The source of every effective value is recorded alongside it, so a divergence
between two processes is diagnosable rather than mysterious.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import Any, Final

from .errors import ConfigError

ENV_PREFIX: Final = "VOXVAULT_"

SOURCE_ARGUMENT: Final = "argumento"
SOURCE_ENV: Final = "variavel de ambiente"
SOURCE_FILE: Final = "arquivo de configuracao"
SOURCE_DEFAULT: Final = "padrao embutido"
SOURCE_LEGACY: Final = "padrao anterior (dados existentes)"

#: Where the data directory lived by default before there was an installer.
#: A store already there stays where it is; nothing is ever moved.
LEGACY_DATA_DIR: Final = Path(r"D:\VoxVault")


def default_data_dir() -> tuple[Path, str]:
    """The data directory when none is configured, and why that one.

    ``VoxVault`` inside the user's profile -- a path every Windows machine has
    -- unless a store already exists at the previous default, which then keeps
    being the effective one: meetings do not disappear from the library just
    because a newer build changed its mind about where they should live.
    Evaluated at every load, never written anywhere.
    """
    if (LEGACY_DATA_DIR / "voxvault.db").is_file():
        return LEGACY_DATA_DIR, SOURCE_LEGACY
    profile = os.environ.get("USERPROFILE") if os.name == "nt" else None
    base = Path(profile) if profile else Path.home()
    return base / "VoxVault", SOURCE_DEFAULT

#: Roles Windows exposes for a default audio endpoint. Communications is the
#: default for VoxVault because a meeting client follows it, and following the
#: multimedia role would capture the wrong endpoint whenever they differ.
DEVICE_ROLE_COMMUNICATIONS: Final = "comunicacoes"
DEVICE_ROLE_MULTIMEDIA: Final = "multimidia"

#: "follow_default" migrates when Windows changes the default endpoint.
#: "pinned" never migrates. They are incompatible on purpose -- without the
#: distinction, plugging in a headset produces undefined behaviour.
POLICY_FOLLOW_DEFAULT: Final = "seguir_padrao"
POLICY_PINNED: Final = "fixado"

_VALID_ROLES = frozenset({DEVICE_ROLE_COMMUNICATIONS, DEVICE_ROLE_MULTIMEDIA})
_VALID_POLICIES = frozenset({POLICY_FOLLOW_DEFAULT, POLICY_PINNED})
_VALID_DEVICES = frozenset({"cuda", "cpu", "auto"})
_VALID_COMPUTE = frozenset({"float16", "float32", "int8", "int8_float16", "auto"})


def user_state_dir() -> Path:
    """Where every per-user file lives: configuration, rendezvous, service log.

    Deliberately not under ``%APPDATA%``. A process started from inside a
    packaged Windows app -- the terminal of the Claude desktop app, or an MCP
    server it launches -- has everything it writes under AppData redirected
    into that package's private folder, and nothing outside the package ever
    sees it. Measured here: a resident service started that way published its
    address where no other surface could read it, while still holding the
    machine-wide claim -- so no surface could find the service, and none could
    start another. The redirection covers the whole process tree and cannot be
    escaped from inside; the user profile root is not redirected at all.
    """
    if os.name == "nt":
        profile = os.environ.get("USERPROFILE")
        return (Path(profile) if profile else Path.home()) / ".voxvault"
    return Path.home() / ".config" / "voxvault"


def user_config_path() -> Path:
    """The fixed per-user configuration file.

    Fixed is the point: it does not move with the working directory, and every
    surface finds the same file no matter how it was launched.
    """
    return user_state_dir() / "config.json"


def legacy_user_config_path() -> Path | None:
    """Where the configuration lived before :func:`user_state_dir` moved it."""
    if os.name != "nt":
        return None
    appdata = os.environ.get("APPDATA")
    return Path(appdata) / "VoxVault" / "config.json" if appdata else None


def _carry_over_legacy_config(target: Path) -> None:
    """Move a configuration written before the move to where it is now read.

    Copied, not moved: an older build of the desktop app still reads the old
    place, and taking the file from under it would silently reset its choices.
    """
    legacy = legacy_user_config_path()
    if legacy is None or target.exists() or not legacy.is_file():
        return
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_suffix(".json.tmp")
        tmp.write_bytes(legacy.read_bytes())
        tmp.replace(target)
    except OSError:
        pass  # read_config_file falls back to reading the legacy file


@dataclass(slots=True)
class Config:
    """Effective configuration, with the provenance of each value."""

    # --- storage -------------------------------------------------------
    data_dir: Path = field(default_factory=lambda: default_data_dir()[0])

    # --- transcription engine ------------------------------------------
    # Provisional until the qualitative comparison in Fase 0 concludes.
    # Replacing it is a configuration change, never a code change.
    model: str = "large-v3"
    device: str = "cuda"
    compute_type: str = "float16"
    language: str = "pt"
    vocabulary: str = ""
    #: Off by default, and deliberately so. With a GPU present but its
    #: libraries broken, falling back to CPU turns a one-hour meeting into
    #: hours of processing and hides a defect the user needs to fix. Running
    #: on CPU in that situation has to be an explicit choice.
    allow_cpu_fallback: bool = False

    # --- audio devices --------------------------------------------------
    device_role: str = DEVICE_ROLE_COMMUNICATIONS
    mic_policy: str = POLICY_FOLLOW_DEFAULT
    system_policy: str = POLICY_FOLLOW_DEFAULT
    mic_device_id: str = ""
    system_device_id: str = ""

    # --- durability -----------------------------------------------------
    #: How often buffered audio is forced to disk. The guarantee "at most N
    #: seconds lost" is this number, not a wish: potential loss equals what
    #: has accumulated since the last completed flush.
    flush_interval_s: float = 2.0
    #: No write completing for this long stops the recording cleanly rather
    #: than letting it accumulate a loss nobody bounded.
    write_stall_abort_s: float = 10.0
    #: Free space below this stops recording cleanly.
    min_free_mb: int = 500

    # --- alignment ------------------------------------------------------
    #: Divergence below this is delivery jitter, not a real gap. Inserting
    #: silence for jitter fabricates the very error alignment exists to remove.
    silence_fill_threshold_ms: int = 200
    #: Measured drift above this is surfaced as a warning on the recording.
    drift_warn_ms: int = 500

    # --- service --------------------------------------------------------
    idle_shutdown_s: int = 300
    service_port: int = 0  # 0 = ephemeral, published via the rendezvous file

    # --- provenance -----------------------------------------------------
    sources: dict[str, str] = field(default_factory=dict)

    def source_of(self, name: str) -> str:
        return self.sources.get(name, SOURCE_DEFAULT)

    @property
    def db_path(self) -> Path:
        return self.data_dir / "voxvault.db"

    @property
    def recordings_dir(self) -> Path:
        return self.data_dir / "recordings"

    @property
    def models_dir(self) -> Path:
        return self.data_dir / "models"

    @property
    def exports_dir(self) -> Path:
        return self.data_dir / "exports"


_PATH_FIELDS = frozenset({"data_dir"})
_INT_FIELDS = frozenset(
    {"min_free_mb", "silence_fill_threshold_ms", "drift_warn_ms",
     "idle_shutdown_s", "service_port"}
)
_FLOAT_FIELDS = frozenset({"flush_interval_s", "write_stall_abort_s"})
_BOOL_FIELDS = frozenset({"allow_cpu_fallback"})

_TRUE_WORDS = frozenset({"1", "true", "sim", "yes", "on"})
_FALSE_WORDS = frozenset({"0", "false", "nao", "não", "no", "off"})


def _coerce(name: str, raw: Any, source: str) -> Any:
    """Convert a raw value to the field's type, naming the source on failure."""
    if name in _BOOL_FIELDS:
        if isinstance(raw, bool):
            return raw
        text = str(raw).strip().lower()
        if text in _TRUE_WORDS:
            return True
        if text in _FALSE_WORDS:
            return False
        raise ConfigError(name, source, f"esperado booleano, recebido {raw!r}")
    if name in _PATH_FIELDS:
        text = str(raw).strip()
        if not text:
            raise ConfigError(name, source, "caminho vazio")
        return Path(text)
    if name in _INT_FIELDS:
        try:
            return int(raw)
        except (TypeError, ValueError):
            raise ConfigError(name, source, f"esperado numero inteiro, recebido {raw!r}") from None
    if name in _FLOAT_FIELDS:
        try:
            return float(raw)
        except (TypeError, ValueError):
            raise ConfigError(name, source, f"esperado numero, recebido {raw!r}") from None
    return str(raw)


def _validate(cfg: Config) -> None:
    """Reject invalid values instead of silently falling back to a default.

    A silent fallback is what makes two processes disagree without either of
    them reporting anything wrong.
    """
    def bad(name: str, reason: str) -> None:
        raise ConfigError(name, cfg.source_of(name), reason)

    if cfg.device not in _VALID_DEVICES:
        bad("device", f"esperado um de {sorted(_VALID_DEVICES)}, recebido {cfg.device!r}")
    if cfg.compute_type not in _VALID_COMPUTE:
        bad("compute_type", f"esperado um de {sorted(_VALID_COMPUTE)}, recebido {cfg.compute_type!r}")
    if cfg.device_role not in _VALID_ROLES:
        bad("device_role", f"esperado um de {sorted(_VALID_ROLES)}, recebido {cfg.device_role!r}")
    for name in ("mic_policy", "system_policy"):
        value = getattr(cfg, name)
        if value not in _VALID_POLICIES:
            bad(name, f"esperado um de {sorted(_VALID_POLICIES)}, recebido {value!r}")
    if cfg.mic_policy == POLICY_PINNED and not cfg.mic_device_id:
        bad("mic_device_id", "politica 'fixado' exige um identificador de dispositivo")
    if cfg.system_policy == POLICY_PINNED and not cfg.system_device_id:
        bad("system_device_id", "politica 'fixado' exige um identificador de dispositivo")
    if not cfg.language.strip():
        bad("language", "codigo de idioma vazio")
    if not cfg.model.strip():
        bad("model", "nome de modelo vazio")
    if cfg.flush_interval_s <= 0:
        bad("flush_interval_s", "deve ser maior que zero")
    if cfg.write_stall_abort_s <= cfg.flush_interval_s:
        bad(
            "write_stall_abort_s",
            f"deve ser maior que flush_interval_s ({cfg.flush_interval_s})",
        )
    if cfg.min_free_mb < 0:
        bad("min_free_mb", "nao pode ser negativo")
    if cfg.silence_fill_threshold_ms < 0:
        bad("silence_fill_threshold_ms", "nao pode ser negativo")
    if cfg.drift_warn_ms <= cfg.silence_fill_threshold_ms:
        bad(
            "drift_warn_ms",
            f"deve ser maior que silence_fill_threshold_ms "
            f"({cfg.silence_fill_threshold_ms})",
        )
    if not (0 <= cfg.service_port <= 65535):
        bad("service_port", "fora da faixa 0-65535")
    if cfg.data_dir.is_absolute() is False:
        bad("data_dir", f"deve ser um caminho absoluto, recebido {cfg.data_dir}")


def read_config_file(path: Path | None = None) -> tuple[dict[str, Any], Path]:
    """Read the user's configuration file. A missing file is not an error."""
    target = path or user_config_path()
    if path is None:
        _carry_over_legacy_config(target)
        if not target.exists():
            legacy = legacy_user_config_path()
            if legacy is not None and legacy.is_file():
                target = legacy  # the copy failed; read it where it is
    if not target.exists():
        return {}, target
    try:
        raw = json.loads(target.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ConfigError(
            "(arquivo)", f"{SOURCE_FILE} {target}", f"JSON invalido: {exc}"
        ) from None
    if not isinstance(raw, dict):
        raise ConfigError(
            "(arquivo)", f"{SOURCE_FILE} {target}", "o conteudo deve ser um objeto JSON"
        )
    return raw, target


def load_config(
    overrides: dict[str, Any] | None = None,
    *,
    env: dict[str, str] | None = None,
    config_path: Path | None = None,
) -> Config:
    """Resolve the effective configuration and record where each value came from.

    ``overrides`` are explicit call-site arguments and win over everything.
    Nothing is ever derived from the current working directory.
    """
    environ = os.environ if env is None else env
    overrides = overrides or {}
    file_values, file_path = read_config_file(config_path)

    cfg = Config()
    known = {f.name for f in fields(Config)} - {"sources"}

    for name in known:
        if name in overrides and overrides[name] is not None:
            value, source = overrides[name], SOURCE_ARGUMENT
        elif (env_key := ENV_PREFIX + name.upper()) in environ:
            value, source = environ[env_key], f"{SOURCE_ENV} {env_key}"
        elif name in file_values:
            value, source = file_values[name], f"{SOURCE_FILE} {file_path}"
        else:
            continue  # keep the dataclass default
        setattr(cfg, name, _coerce(name, value, source))
        cfg.sources[name] = source

    if "data_dir" not in cfg.sources:
        cfg.data_dir, cfg.sources["data_dir"] = default_data_dir()
    for name in known:
        cfg.sources.setdefault(name, SOURCE_DEFAULT)

    unknown = set(file_values) - known
    if unknown:
        raise ConfigError(
            ", ".join(sorted(unknown)),
            f"{SOURCE_FILE} {file_path}",
            "campo desconhecido",
        )

    _validate(cfg)
    return cfg


def write_config_file(values: dict[str, Any], path: Path | None = None) -> Path:
    """Persist configuration for every process to read.

    Validates before writing: a file that cannot be loaded would break every
    surface at once, and the moment to catch that is here.
    """
    target = path or user_config_path()
    if path is None:
        # Before merging: writing one field into a fresh file would otherwise
        # drop every choice still sitting in the old location.
        _carry_over_legacy_config(target)
    merged, _ = read_config_file(target)
    merged.update({k: (str(v) if isinstance(v, Path) else v) for k, v in values.items()})

    _probe = Config()
    known = {f.name for f in fields(Config)} - {"sources"}
    for key, raw in merged.items():
        if key not in known:
            raise ConfigError(key, f"{SOURCE_FILE} {target}", "campo desconhecido")
        setattr(_probe, key, _coerce(key, raw, f"{SOURCE_FILE} {target}"))
        _probe.sources[key] = f"{SOURCE_FILE} {target}"
    _validate(_probe)

    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(merged, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(target)
    return target
