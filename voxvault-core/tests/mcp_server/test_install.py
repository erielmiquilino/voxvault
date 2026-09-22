"""Registering with another application's configuration.

Worth testing carefully because the failure is not ours to pay: a client that
cannot parse its own configuration loses every server the person had set up,
not only this one.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from voxvault.mcp.install import SERVER_KEY, ClientConfig, apply, inspect, server_entry


@pytest.fixture
def client(tmp_path: Path) -> ClientConfig:
    return ClientConfig("Cliente de teste", tmp_path / "config.json")


def test_a_missing_file_is_reported_as_absent(client: ClientConfig) -> None:
    estado, detalhe = inspect(client)
    assert estado == "ausente"
    assert str(client.path) in detalhe


def test_an_unregistered_client_is_reported(client: ClientConfig) -> None:
    client.path.write_text(json.dumps({"mcpServers": {}}), encoding="utf-8")
    assert inspect(client)[0] == "nao registrado"


def test_a_correct_registration_is_recognised(client: ClientConfig) -> None:
    client.path.write_text(
        json.dumps({"mcpServers": {SERVER_KEY: server_entry()}}), encoding="utf-8"
    )
    assert inspect(client)[0] == "registrado"


def test_a_stale_registration_is_reported_as_divergent(client: ClientConfig) -> None:
    client.path.write_text(
        json.dumps({"mcpServers": {SERVER_KEY: {"command": "python-antigo"}}}),
        encoding="utf-8",
    )
    estado, detalhe = inspect(client)
    assert estado == "divergente"
    assert "python-antigo" in detalhe


def test_applying_preserves_the_other_servers_and_settings(
    client: ClientConfig,
) -> None:
    """The entry is added beside what is already there, never instead of it."""
    original = {
        "mcpServers": {"outro": {"command": "outro.exe"}},
        "preferences": {"tema": "escuro"},
    }
    client.path.write_text(json.dumps(original), encoding="utf-8")

    apply(client)

    raw = json.loads(client.path.read_text(encoding="utf-8"))
    assert raw["mcpServers"]["outro"] == {"command": "outro.exe"}
    assert raw["preferences"] == {"tema": "escuro"}
    assert raw["mcpServers"][SERVER_KEY] == server_entry()


def test_applying_backs_up_what_was_there(client: ClientConfig) -> None:
    client.path.write_text(json.dumps({"mcpServers": {}}), encoding="utf-8")
    apply(client)

    backup = client.path.with_suffix(client.path.suffix + ".voxvault-backup")
    assert backup.is_file()
    assert json.loads(backup.read_text(encoding="utf-8")) == {"mcpServers": {}}


def test_applying_to_a_missing_file_creates_it(client: ClientConfig) -> None:
    apply(client)
    raw = json.loads(client.path.read_text(encoding="utf-8"))
    assert SERVER_KEY in raw["mcpServers"]


def test_unparseable_configuration_is_refused_rather_than_overwritten(
    client: ClientConfig,
) -> None:
    """Rewriting a file we cannot read would discard whatever it held."""
    client.path.write_text("{ isto nao e json", encoding="utf-8")

    with pytest.raises(ValueError, match="Nada foi alterado"):
        apply(client)

    assert client.path.read_text(encoding="utf-8") == "{ isto nao e json"


def test_applying_twice_is_idempotent(client: ClientConfig) -> None:
    apply(client)
    first = client.path.read_text(encoding="utf-8")
    apply(client)
    assert client.path.read_text(encoding="utf-8") == first


def test_the_entry_names_the_interpreter_by_absolute_path() -> None:
    """The client launches this from its own environment, not from a shell."""
    entry = server_entry()
    assert Path(entry["command"]).is_absolute()
    assert entry["args"] == ["-m", "voxvault.mcp"]
