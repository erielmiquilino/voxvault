"""Configuration precedence and validation.

These cover the scenarios in specs/environment-check/spec.md under
"Configuracao compartilhada e precedencia".
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from voxvault.config import (
    SOURCE_ARGUMENT,
    SOURCE_DEFAULT,
    Config,
    load_config,
    write_config_file,
)
from voxvault.errors import ConfigError


@pytest.fixture
def config_file(tmp_path: Path) -> Path:
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"data_dir": r"D:\DoArquivo"}), encoding="utf-8")
    return path


def test_defaults_are_reported_as_defaults(tmp_path: Path) -> None:
    cfg = load_config(env={}, config_path=tmp_path / "ausente.json")
    assert cfg.model == "large-v3"
    assert cfg.source_of("model") == SOURCE_DEFAULT
    assert cfg.data_dir == Path(r"D:\VoxVault")


def test_argument_beats_environment_and_file(config_file: Path) -> None:
    cfg = load_config(
        {"data_dir": r"D:\DoArgumento"},
        env={"VOXVAULT_DATA_DIR": r"D:\DoAmbiente"},
        config_path=config_file,
    )
    assert cfg.data_dir == Path(r"D:\DoArgumento")
    assert cfg.source_of("data_dir") == SOURCE_ARGUMENT


def test_environment_beats_file_and_reports_its_origin(config_file: Path) -> None:
    """Scenario: Variavel de ambiente sobrepoe o arquivo."""
    cfg = load_config(
        env={"VOXVAULT_DATA_DIR": r"D:\DoAmbiente"}, config_path=config_file
    )
    assert cfg.data_dir == Path(r"D:\DoAmbiente")
    assert "variavel de ambiente" in cfg.source_of("data_dir")
    assert "VOXVAULT_DATA_DIR" in cfg.source_of("data_dir")


def test_file_beats_default(config_file: Path) -> None:
    cfg = load_config(env={}, config_path=config_file)
    assert cfg.data_dir == Path(r"D:\DoArquivo")
    assert "arquivo de configuracao" in cfg.source_of("data_dir")


def test_invalid_value_names_field_and_source(tmp_path: Path) -> None:
    """Scenario: Configuracao invalida -- nenhum padrao aplicado em silencio."""
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"compute_type": "float8"}), encoding="utf-8")

    with pytest.raises(ConfigError) as caught:
        load_config(env={}, config_path=path)

    assert caught.value.field == "compute_type"
    assert "arquivo de configuracao" in caught.value.source
    # The point of failing: the default must NOT have been substituted.
    assert "float8" in str(caught.value)


def test_unknown_field_is_rejected_rather_than_ignored(tmp_path: Path) -> None:
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"modelo": "large-v3"}), encoding="utf-8")
    with pytest.raises(ConfigError, match="desconhecido"):
        load_config(env={}, config_path=path)


def test_pinned_policy_requires_a_device_identifier(tmp_path: Path) -> None:
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"mic_policy": "fixado"}), encoding="utf-8")
    with pytest.raises(ConfigError) as caught:
        load_config(env={}, config_path=path)
    assert caught.value.field == "mic_device_id"


def test_relative_data_dir_is_rejected(tmp_path: Path) -> None:
    """The store must never be derived from wherever a process was launched."""
    with pytest.raises(ConfigError, match="absoluto"):
        load_config({"data_dir": "dados"}, env={}, config_path=tmp_path / "x.json")


def test_boolean_accepts_words_and_rejects_nonsense(tmp_path: Path) -> None:
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"allow_cpu_fallback": "sim"}), encoding="utf-8")
    assert load_config(env={}, config_path=path).allow_cpu_fallback is True

    path.write_text(json.dumps({"allow_cpu_fallback": "talvez"}), encoding="utf-8")
    with pytest.raises(ConfigError, match="booleano"):
        load_config(env={}, config_path=path)


def test_write_config_file_validates_before_writing(tmp_path: Path) -> None:
    path = tmp_path / "config.json"
    with pytest.raises(ConfigError):
        write_config_file({"device": "tpu"}, path)
    assert not path.exists(), "um arquivo invalido nao pode chegar ao disco"


def test_write_then_read_round_trip(tmp_path: Path) -> None:
    path = tmp_path / "config.json"
    write_config_file({"model": "large-v3-turbo", "language": "pt"}, path)
    cfg = load_config(env={}, config_path=path)
    assert cfg.model == "large-v3-turbo"


def test_two_processes_from_different_cwds_resolve_the_same_store(
    tmp_path: Path,
) -> None:
    """Scenario: Processos diferentes resolvem o mesmo diretorio de dados.

    Run as real subprocesses from two different working directories, because
    the failure this guards against is precisely a path derived from the cwd.
    """
    config_path = tmp_path / "config.json"
    write_config_file({"data_dir": r"D:\Compartilhado"}, config_path)

    cwd_a = tmp_path / "a"
    cwd_b = tmp_path / "b"
    cwd_a.mkdir()
    cwd_b.mkdir()

    program = textwrap.dedent(
        f"""
        from pathlib import Path
        from voxvault.config import load_config
        cfg = load_config(env={{}}, config_path=Path(r"{config_path}"))
        print(cfg.data_dir)
        """
    )
    env = {**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[1] / "src")}

    results = []
    for cwd in (cwd_a, cwd_b):
        out = subprocess.run(
            [sys.executable, "-c", program],
            cwd=cwd, capture_output=True, text=True, check=True, env=env,
        )
        results.append(out.stdout.strip())

    assert results[0] == results[1] == r"D:\Compartilhado"


def test_derived_paths_hang_off_the_data_dir() -> None:
    cfg = Config(data_dir=Path(r"D:\Qualquer"))
    assert cfg.db_path == Path(r"D:\Qualquer\voxvault.db")
    assert cfg.recordings_dir == Path(r"D:\Qualquer\recordings")


# -- where per-user state lives --------------------------------------------

windows_only = pytest.mark.skipif(os.name != "nt", reason="a pasta antiga so existe no Windows")


@pytest.fixture
def profile(tmp_path: Path, monkeypatch) -> tuple[Path, Path]:
    """A user profile and an AppData of our own, so nothing real is touched."""
    home, appdata = tmp_path / "perfil", tmp_path / "perfil" / "AppData" / "Roaming"
    appdata.mkdir(parents=True)
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setenv("APPDATA", str(appdata))
    return home, appdata


@windows_only
def test_per_user_state_is_not_under_appdata(profile) -> None:
    """A process started inside a packaged app has AppData redirected.

    The service started that way published its address where nothing outside
    the package could read it, and still held the machine-wide claim. Nothing
    per-user may live where that redirection applies.
    """
    from voxvault.config import user_config_path, user_state_dir

    home, appdata = profile
    assert user_state_dir() == home / ".voxvault"
    assert appdata not in user_config_path().parents


@windows_only
def test_a_configuration_in_the_old_place_is_carried_over(profile) -> None:
    from voxvault.config import read_config_file, user_config_path

    _home, appdata = profile
    legacy = appdata / "VoxVault" / "config.json"
    legacy.parent.mkdir(parents=True)
    legacy.write_text(json.dumps({"mic_policy": "seguir_padrao"}), encoding="utf-8")

    values, source = read_config_file()

    assert values == {"mic_policy": "seguir_padrao"}
    assert source == user_config_path()
    assert json.loads(user_config_path().read_text(encoding="utf-8")) == values
    assert legacy.is_file(), "copiado, nao movido: um app antigo ainda le dali"


@windows_only
def test_the_new_place_wins_over_the_old(profile) -> None:
    from voxvault.config import read_config_file, user_config_path

    _home, appdata = profile
    legacy = appdata / "VoxVault" / "config.json"
    legacy.parent.mkdir(parents=True)
    legacy.write_text(json.dumps({"language": "en"}), encoding="utf-8")
    user_config_path().parent.mkdir(parents=True)
    user_config_path().write_text(json.dumps({"language": "pt"}), encoding="utf-8")

    values, _source = read_config_file()

    assert values == {"language": "pt"}


@windows_only
def test_writing_one_field_keeps_the_choices_still_in_the_old_place(profile) -> None:
    """The first write after the move must not start from an empty file."""
    from voxvault.config import read_config_file

    _home, appdata = profile
    legacy = appdata / "VoxVault" / "config.json"
    legacy.parent.mkdir(parents=True)
    legacy.write_text(json.dumps({"language": "pt"}), encoding="utf-8")

    write_config_file({"model": "large-v3-turbo"})

    values, _source = read_config_file()
    assert values == {"language": "pt", "model": "large-v3-turbo"}
