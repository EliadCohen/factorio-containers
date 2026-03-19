"""
Unit tests for non-TUI modules:
  - leanrcon.py         (RCON packet protocol)
  - factorio_container.py (FactorioGame init + settings)
  - factorio_server.py   (Game label parsing, _rcon_save, create_game, rebuild_and_recreate)
  - image_build.py       (FactorioContainerImage.build)
"""
import json
import struct
from pathlib import Path
from unittest.mock import MagicMock, call, patch

import pytest

from leanrcon import RCONClient, send_command
from factorio_container import FactorioGame
from factorio_server import FactorioServer
from image_build import FactorioContainerImage


# ─── helpers ──────────────────────────────────────────────────────────────────

def _make_client_with_mock_socket():
    """Return an RCONClient whose _sock is a MagicMock (no real network)."""
    c = object.__new__(RCONClient)
    c._host = "127.0.0.1"
    c._port = 27015
    c._password = "secret"
    c._timeout = 10
    c._sock = MagicMock()
    return c


def _make_server():
    with patch("factorio_server.PodmanClient"):
        return FactorioServer()


def _make_game_mock(name, port, savepath, running):
    g = MagicMock()
    g.display_name = name
    g.game_port = port
    g.savepath = savepath
    g.active_status = running
    g.rcon_port = port + 1000 if running else 0
    g.rcon_password = "deadbeef" if running else ""
    return g


# ─── leanrcon: packet encoding ────────────────────────────────────────────────

class TestRCONPacketEncoding:
    def test_send_packet_length_field(self):
        c = _make_client_with_mock_socket()
        c._send(1, 3, "hi")
        raw = c._sock.sendall.call_args[0][0]
        length = struct.unpack("<i", raw[:4])[0]
        # length covers req_id (4) + ptype (4) + body + two null bytes
        assert length == 4 + 4 + len(b"hi") + 2

    def test_send_packet_req_id_and_type(self):
        c = _make_client_with_mock_socket()
        c._send(42, 7, "")
        raw = c._sock.sendall.call_args[0][0]
        req_id = struct.unpack("<i", raw[4:8])[0]
        ptype = struct.unpack("<i", raw[8:12])[0]
        assert req_id == 42
        assert ptype == 7

    def test_send_packet_terminates_with_double_null(self):
        c = _make_client_with_mock_socket()
        c._send(1, 2, "cmd")
        raw = c._sock.sendall.call_args[0][0]
        assert raw[-2:] == b"\x00\x00"

    def test_recv_returns_req_id_and_body(self):
        c = _make_client_with_mock_socket()
        body = "hello world"
        inner = struct.pack("<ii", 99, 0) + body.encode() + b"\x00\x00"
        c._sock.recv.side_effect = [struct.pack("<i", len(inner)), inner]
        req_id, decoded = c._recv()
        assert req_id == 99
        assert decoded == body

    def test_recv_handles_empty_body(self):
        c = _make_client_with_mock_socket()
        inner = struct.pack("<ii", 1, 0) + b"\x00\x00"
        c._sock.recv.side_effect = [struct.pack("<i", len(inner)), inner]
        _, body = c._recv()
        assert body == ""

    def test_send_command_helper_uses_context_manager(self):
        with patch("leanrcon.RCONClient") as MockClient:
            instance = MockClient.return_value.__enter__.return_value
            instance.send.return_value = "saved"
            result = send_command("127.0.0.1", 27015, "pass", "/server-save")
        assert result == "saved"
        instance.send.assert_called_once_with("/server-save")

    def test_exit_closes_socket(self):
        c = _make_client_with_mock_socket()
        sock = c._sock  # capture before __exit__ nulls it
        c.__exit__(None, None, None)
        sock.close.assert_called_once()
        assert c._sock is None


# ─── FactorioGame: __init__ ───────────────────────────────────────────────────

class TestFactorioGameInit:
    def test_rcon_port_is_game_port_plus_1000(self):
        g = FactorioGame(name="test", savefile="test", port=34200)
        assert g.rcon_port == 35200

    def test_rcon_port_tracks_custom_port(self):
        g = FactorioGame(name="test", savefile="test", port=34500)
        assert g.rcon_port == 35500

    def test_rcon_password_is_16_hex_chars(self):
        g = FactorioGame(name="test", savefile="test", port=34200)
        assert len(g.rcon_password) == 16
        int(g.rcon_password, 16)  # raises ValueError if not valid hex

    def test_rcon_passwords_differ_across_instances(self):
        passwords = {FactorioGame(name="x", savefile="x", port=34200).rcon_password for _ in range(5)}
        # All 5 should be unique (collision astronomically unlikely)
        assert len(passwords) == 5

    def test_command_contains_rcon_port_flag(self):
        g = FactorioGame(name="test", savefile="test", port=34200)
        cmd = " ".join(g.command)
        assert f"--rcon-port {g.rcon_port}" in cmd

    def test_command_contains_rcon_password_flag(self):
        g = FactorioGame(name="test", savefile="test", port=34200)
        cmd = " ".join(g.command)
        assert f"--rcon-password {g.rcon_password}" in cmd

    def test_command_contains_port_flag(self):
        g = FactorioGame(name="test", savefile="test", port=34200)
        cmd = " ".join(g.command)
        assert "--port 34200" in cmd

    def test_game_name_has_factorio_prefix(self):
        g = FactorioGame(name="myworld", savefile="myworld", port=34200)
        assert g.game_name == "factorio-myworld"

    def test_command_is_a_list(self):
        g = FactorioGame(name="test", savefile="test", port=34200)
        assert isinstance(g.command, list)


# ─── FactorioGame: _prepare_server_settings ───────────────────────────────────

class TestPrepareServerSettings:
    def _make_game(self, name="server"):
        g = object.__new__(FactorioGame)
        g.name = name
        return g

    def test_creates_settings_file_from_template_when_missing(self, tmp_path):
        template = tmp_path / "template.json"
        template.write_text(json.dumps({"name": "default", "other": "keep"}))
        saves_dir = tmp_path / "mysave"
        saves_dir.mkdir()

        g = self._make_game("myserver")
        g.TEMPLATE_PATH = str(template)
        g._prepare_server_settings(str(saves_dir))

        settings = saves_dir / "server-settings.json"
        assert settings.exists()
        data = json.loads(settings.read_text())
        assert data["name"] == "myserver"
        assert data["other"] == "keep"

    def test_updates_name_in_existing_settings(self, tmp_path):
        settings = tmp_path / "server-settings.json"
        settings.write_text(json.dumps({"name": "old", "desc": "preserve"}))

        g = self._make_game("newname")
        g._prepare_server_settings(str(tmp_path))

        data = json.loads(settings.read_text())
        assert data["name"] == "newname"
        assert data["desc"] == "preserve"

    def test_does_not_overwrite_existing_settings_file(self, tmp_path):
        settings = tmp_path / "server-settings.json"
        original = {"name": "existing", "custom": True}
        settings.write_text(json.dumps(original))
        template = tmp_path / "template.json"
        template.write_text(json.dumps({"name": "template", "custom": False}))

        g = self._make_game("existing")
        g.TEMPLATE_PATH = str(template)
        g._prepare_server_settings(str(tmp_path))

        data = json.loads(settings.read_text())
        assert data["custom"] is True  # template value was NOT used


# ─── FactorioServer.Game: label parsing ───────────────────────────────────────

class TestGameLabelParsing:
    def _make_game(self, labels=None):
        return FactorioServer.Game(
            container=MagicMock(),
            container_id="abc",
            game_name="factorio-test",
            game_port=34200,
            game_path="/saves/test/",
            labels=labels,
        )

    def test_rcon_port_parsed_from_label(self):
        g = self._make_game({"factorio.rcon-port": "35200", "factorio.rcon-password": "pw"})
        assert g.rcon_port == 35200

    def test_rcon_password_parsed_from_label(self):
        g = self._make_game({"factorio.rcon-port": "35200", "factorio.rcon-password": "mypass"})
        assert g.rcon_password == "mypass"

    def test_rcon_defaults_when_labels_absent(self):
        g = self._make_game()
        assert g.rcon_port == 0
        assert g.rcon_password == ""

    def test_rcon_defaults_when_labels_empty_dict(self):
        g = self._make_game({})
        assert g.rcon_port == 0
        assert g.rcon_password == ""

    def test_display_name_strips_factorio_prefix(self):
        g = self._make_game()
        assert g.display_name == "test"

    def test_active_status_reads_container_inspect(self):
        container = MagicMock()
        container.inspect.return_value = {"State": {"Running": True}}
        g = FactorioServer.Game(
            container=container, container_id="x",
            game_name="factorio-x", game_port=34200, game_path="/x",
        )
        assert g.active_status is True

    def test_game_port_stored_as_int(self):
        g = self._make_game()
        assert isinstance(g.game_port, int)


# ─── FactorioServer._rcon_save ────────────────────────────────────────────────

class TestRconSave:
    def test_returns_false_when_rcon_port_is_zero(self):
        server = _make_server()
        game = MagicMock(rcon_port=0, rcon_password="pw")
        assert server._rcon_save(game) is False

    def test_returns_false_when_rcon_password_empty(self):
        server = _make_server()
        game = MagicMock(rcon_port=35200, rcon_password="")
        assert server._rcon_save(game) is False

    def test_returns_true_on_success(self):
        server = _make_server()
        game = MagicMock(rcon_port=35200, rcon_password="secret")
        with patch("factorio_server.send_command"), patch("factorio_server.time.sleep"):
            result = server._rcon_save(game)
        assert result is True

    def test_returns_false_on_connection_error(self):
        server = _make_server()
        game = MagicMock(rcon_port=35200, rcon_password="secret")
        with patch("factorio_server.send_command", side_effect=ConnectionRefusedError):
            result = server._rcon_save(game)
        assert result is False

    def test_returns_false_on_timeout(self):
        server = _make_server()
        game = MagicMock(rcon_port=35200, rcon_password="secret")
        with patch("factorio_server.send_command", side_effect=TimeoutError):
            result = server._rcon_save(game)
        assert result is False

    def test_sleeps_2s_after_successful_save(self):
        server = _make_server()
        game = MagicMock(rcon_port=35200, rcon_password="secret")
        with patch("factorio_server.send_command"), \
             patch("factorio_server.time.sleep") as mock_sleep:
            server._rcon_save(game)
        mock_sleep.assert_called_once_with(2)

    def test_sends_correct_command(self):
        server = _make_server()
        game = MagicMock(rcon_port=35200, rcon_password="secret")
        with patch("factorio_server.send_command") as mock_cmd, \
             patch("factorio_server.time.sleep"):
            server._rcon_save(game)
        mock_cmd.assert_called_once_with("127.0.0.1", 35200, "secret", "/server-save")


# ─── FactorioServer.create_game: name uniqueness ──────────────────────────────

class TestCreateGameUniqueness:
    def _server_with_games(self, names):
        server = _make_server()
        server.games = {n: MagicMock() for n in names}
        return server

    def test_uses_original_name_when_no_conflict(self):
        server = _server_with_games(self, {})
        with patch("factorio_server.FactorioGame") as MockGame:
            MockGame.return_value.create_game.return_value = {}
            server.create_game(name="fresh", port=34200, savefile="fresh")
        assert MockGame.call_args[1]["name"] == "fresh"

    def test_appends_suffix_when_name_conflicts(self):
        server = _server_with_games(self, {"factorio-myworld": MagicMock()})
        with patch("factorio_server.FactorioGame") as MockGame:
            MockGame.return_value.create_game.return_value = {}
            server.create_game(name="myworld", port=34200, savefile="myworld")
        assert MockGame.call_args[1]["name"] == "myworld-2"

    def test_increments_suffix_until_unique(self):
        server = _server_with_games(self, {
            "factorio-myworld": MagicMock(),
            "factorio-myworld-2": MagicMock(),
        })
        with patch("factorio_server.FactorioGame") as MockGame:
            MockGame.return_value.create_game.return_value = {}
            server.create_game(name="myworld", port=34200, savefile="myworld")
        assert MockGame.call_args[1]["name"] == "myworld-3"

    def test_force_name_bypasses_uniqueness_check(self):
        server = _server_with_games(self, {"factorio-myworld": MagicMock()})
        with patch("factorio_server.FactorioGame") as MockGame:
            MockGame.return_value.create_game.return_value = {}
            server.create_game(name="myworld", port=34200, savefile="myworld", force_name=True)
        assert MockGame.call_args[1]["name"] == "myworld"

    # workaround: make helper callable as static-ish
    def __get__(self, obj, objtype=None):
        return self


# fix: use module-level helper instead
def _server_with_games(names):
    server = _make_server()
    server.games = {n: MagicMock() for n in names}
    return server


class TestCreateGameUniqueness:  # noqa: F811 — redefine cleanly
    def test_uses_original_name_when_no_conflict(self):
        server = _server_with_games({})
        with patch("factorio_server.FactorioGame") as MockGame:
            MockGame.return_value.create_game.return_value = {}
            server.create_game(name="fresh", port=34200, savefile="fresh")
        assert MockGame.call_args[1]["name"] == "fresh"

    def test_appends_suffix_when_name_conflicts(self):
        server = _server_with_games({"factorio-myworld": MagicMock()})
        with patch("factorio_server.FactorioGame") as MockGame:
            MockGame.return_value.create_game.return_value = {}
            server.create_game(name="myworld", port=34200, savefile="myworld")
        assert MockGame.call_args[1]["name"] == "myworld-2"

    def test_increments_suffix_until_unique(self):
        server = _server_with_games({
            "factorio-myworld": MagicMock(),
            "factorio-myworld-2": MagicMock(),
        })
        with patch("factorio_server.FactorioGame") as MockGame:
            MockGame.return_value.create_game.return_value = {}
            server.create_game(name="myworld", port=34200, savefile="myworld")
        assert MockGame.call_args[1]["name"] == "myworld-3"

    def test_force_name_bypasses_uniqueness_check(self):
        server = _server_with_games({"factorio-myworld": MagicMock()})
        with patch("factorio_server.FactorioGame") as MockGame:
            MockGame.return_value.create_game.return_value = {}
            server.create_game(name="myworld", port=34200, savefile="myworld", force_name=True)
        assert MockGame.call_args[1]["name"] == "myworld"


# ─── FactorioServer.rebuild_and_recreate ──────────────────────────────────────

class TestRebuildAndRecreate:
    """
    update_game_list is mocked as a no-op throughout; self.games is pre-set
    so the method reads a consistent snapshot.
    """

    def _setup(self, games_dict):
        server = _make_server()
        server.games = games_dict
        server.update_game_list = MagicMock()
        return server

    def test_rcon_save_called_only_for_running_games(self):
        running = _make_game_mock("running", 34200, "/saves/running/", True)
        stopped = _make_game_mock("stopped", 34201, "/saves/stopped/", False)
        server = self._setup({"factorio-running": running, "factorio-stopped": stopped})

        with patch.object(server, "_rcon_save", return_value=True) as mock_rcon, \
             patch.object(server, "create_game"), \
             patch("factorio_server.FactorioContainerImage"):
            server.rebuild_and_recreate()

        called_with = [c.args[0] for c in mock_rcon.call_args_list]
        assert running in called_with
        assert stopped not in called_with

    def test_all_games_deleted(self):
        g1 = _make_game_mock("a", 34200, "/saves/a/", False)
        g2 = _make_game_mock("b", 34201, "/saves/b/", False)
        server = self._setup({"factorio-a": g1, "factorio-b": g2})

        with patch.object(server, "create_game"), \
             patch("factorio_server.FactorioContainerImage"):
            server.rebuild_and_recreate()

        g1.delete.assert_called_once()
        g2.delete.assert_called_once()

    def test_create_game_called_for_each_snapshot(self):
        g1 = _make_game_mock("world1", 34200, "/saves/world1/", True)
        g2 = _make_game_mock("world2", 34201, "/saves/world2/", False)
        server = self._setup({"factorio-world1": g1, "factorio-world2": g2})

        with patch.object(server, "_rcon_save", return_value=True), \
             patch.object(server, "create_game") as mock_create, \
             patch("factorio_server.FactorioContainerImage"):
            server.rebuild_and_recreate()

        names_created = {c.kwargs["name"] for c in mock_create.call_args_list}
        assert names_created == {"world1", "world2"}

    def test_create_game_uses_force_name(self):
        g = _make_game_mock("world", 34200, "/saves/world/", False)
        server = self._setup({"factorio-world": g})

        with patch.object(server, "create_game") as mock_create, \
             patch("factorio_server.FactorioContainerImage"):
            server.rebuild_and_recreate()

        assert mock_create.call_args.kwargs["force_name"] is True

    def test_stopped_games_are_stopped_after_recreate(self):
        g = _make_game_mock("world", 34200, "/saves/world/", False)
        server = self._setup({"factorio-world": g})
        # games dict stays as-is (update_game_list is a no-op)

        with patch.object(server, "create_game"), \
             patch("factorio_server.FactorioContainerImage"):
            server.rebuild_and_recreate()

        g.stop.assert_called_once()

    def test_running_games_not_stopped_after_recreate(self):
        g = _make_game_mock("world", 34200, "/saves/world/", True)
        server = self._setup({"factorio-world": g})

        with patch.object(server, "_rcon_save", return_value=True), \
             patch.object(server, "create_game"), \
             patch("factorio_server.FactorioContainerImage"):
            server.rebuild_and_recreate()

        g.stop.assert_not_called()

    def test_return_value_contains_recreated_and_restarted(self):
        running = _make_game_mock("r", 34200, "/saves/r/", True)
        stopped = _make_game_mock("s", 34201, "/saves/s/", False)
        server = self._setup({"factorio-r": running, "factorio-s": stopped})

        with patch.object(server, "_rcon_save", return_value=True), \
             patch.object(server, "create_game"), \
             patch("factorio_server.FactorioContainerImage"):
            result = server.rebuild_and_recreate()

        assert set(result["recreated"]) == {"r", "s"}
        assert result["restarted"] == ["r"]

    def test_image_rebuild_called(self):
        g = _make_game_mock("world", 34200, "/saves/world/", False)
        server = self._setup({"factorio-world": g})

        with patch.object(server, "create_game"), \
             patch("factorio_server.FactorioContainerImage") as MockImage:
            server.rebuild_and_recreate()

        MockImage.return_value.build.assert_called_once()


# ─── FactorioContainerImage ───────────────────────────────────────────────────

class TestFactorioContainerImage:
    def test_image_tag_is_correct(self):
        assert FactorioContainerImage.IMAGE_TAG == "factorio-headless:latest"

    def test_build_uses_correct_tag(self):
        with patch("image_build.PodmanClient") as MockClient:
            ctx = MockClient.return_value.__enter__.return_value
            FactorioContainerImage().build()
        _, kwargs = ctx.images.build.call_args
        assert kwargs["tag"] == "factorio-headless:latest"

    def test_build_uses_correct_paths(self):
        with patch("image_build.PodmanClient") as MockClient:
            ctx = MockClient.return_value.__enter__.return_value
            FactorioContainerImage().build()
        _, kwargs = ctx.images.build.call_args
        assert kwargs["path"] == FactorioContainerImage.CONTAINER_DIR
        assert kwargs["dockerfile"] == FactorioContainerImage.CONTAINERFILE

    def test_build_uses_context_manager(self):
        with patch("image_build.PodmanClient") as MockClient:
            FactorioContainerImage().build()
        MockClient.return_value.__enter__.assert_called_once()
        MockClient.return_value.__exit__.assert_called_once()
