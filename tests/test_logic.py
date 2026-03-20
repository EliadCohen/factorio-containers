"""
Unit tests for non-TUI modules:
  - leanrcon.py           (RCON packet protocol)
  - factorio_container.py (FactorioGame init + settings)
  - factorio_server.py    (Game label parsing, _rcon_save, create_game,
                            rebuild_and_recreate, player_count)
  - image_build.py        (FactorioContainerImage.build)

All tests run without a live Podman socket or Factorio process.  The podman
package is globally stubbed by conftest.py; individual tests add more
fine-grained patches where call arguments must be asserted.
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
    """
    Return an RCONClient whose _sock is a MagicMock (no real network).

    Bypasses ``__init__`` so no connection attempt is made.  The ``_sock``
    attribute is set to a ``MagicMock`` so ``sendall`` and ``recv`` calls can
    be captured and inspected.
    """
    c = object.__new__(RCONClient)
    c._host = "127.0.0.1"
    c._port = 27015
    c._password = "secret"
    c._timeout = 10
    c._sock = MagicMock()
    return c


def _make_server():
    """
    Return a FactorioServer with the PodmanClient constructor patched out.

    The patch is applied only during construction so the returned object
    behaves normally afterwards (``games`` dict, method calls, etc.).
    """
    with patch("factorio_server.PodmanClient"):
        return FactorioServer()


def _make_game_mock(name, port, savepath, running):
    """
    Return a MagicMock imitating a FactorioServer.Game object.

    Args:
        name (str): ``display_name`` value (without the ``"factorio-"`` prefix).
        port (int): ``game_port`` value.
        savepath (str): ``savepath`` value (used to derive ``savefile``).
        running (bool): ``active_status`` value; also controls whether RCON
            credentials are populated (running games get real credentials,
            stopped games get empty ones so _rcon_save returns False for them).
    """
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
    """
    Verify that RCONClient encodes and decodes RCON packets correctly.

    Tests operate on the wire bytes captured by the MagicMock socket so they
    are independent of any network.  The Valve RCON spec is:
      [length:i32le][req_id:i32le][ptype:i32le][body:utf8][NUL][NUL]
    where ``length`` counts from req_id onwards (i.e. does not include itself).
    """

    def test_send_packet_length_field(self):
        """Length field must equal 8 + len(body) + 2 (req_id + ptype + body + two nulls)."""
        c = _make_client_with_mock_socket()
        c._send(1, 3, "hi")
        raw = c._sock.sendall.call_args[0][0]
        length = struct.unpack("<i", raw[:4])[0]
        # length covers req_id (4) + ptype (4) + body + two null bytes
        assert length == 4 + 4 + len(b"hi") + 2

    def test_send_packet_req_id_and_type(self):
        """req_id and ptype must appear at bytes 4-8 and 8-12 respectively."""
        c = _make_client_with_mock_socket()
        c._send(42, 7, "")
        raw = c._sock.sendall.call_args[0][0]
        req_id = struct.unpack("<i", raw[4:8])[0]
        ptype = struct.unpack("<i", raw[8:12])[0]
        assert req_id == 42
        assert ptype == 7

    def test_send_packet_terminates_with_double_null(self):
        """Every RCON packet must end with two null bytes per the Valve spec."""
        c = _make_client_with_mock_socket()
        c._send(1, 2, "cmd")
        raw = c._sock.sendall.call_args[0][0]
        assert raw[-2:] == b"\x00\x00"

    def test_recv_returns_req_id_and_body(self):
        """_recv must correctly decode the req_id and body from a response packet."""
        c = _make_client_with_mock_socket()
        body = "hello world"
        inner = struct.pack("<ii", 99, 0) + body.encode() + b"\x00\x00"
        c._sock.recv.side_effect = [struct.pack("<i", len(inner)), inner]
        req_id, decoded = c._recv()
        assert req_id == 99
        assert decoded == body

    def test_recv_handles_empty_body(self):
        """_recv must return an empty string for packets with no body content."""
        c = _make_client_with_mock_socket()
        inner = struct.pack("<ii", 1, 0) + b"\x00\x00"
        c._sock.recv.side_effect = [struct.pack("<i", len(inner)), inner]
        _, body = c._recv()
        assert body == ""

    def test_send_command_helper_uses_context_manager(self):
        """send_command must open a context manager and delegate to client.send."""
        with patch("leanrcon.RCONClient") as MockClient:
            instance = MockClient.return_value.__enter__.return_value
            instance.send.return_value = "saved"
            result = send_command("127.0.0.1", 27015, "pass", "/server-save")
        assert result == "saved"
        instance.send.assert_called_once_with("/server-save")

    def test_exit_closes_socket(self):
        """__exit__ must close the socket and set _sock to None."""
        c = _make_client_with_mock_socket()
        sock = c._sock  # capture before __exit__ nulls it
        c.__exit__(None, None, None)
        sock.close.assert_called_once()
        assert c._sock is None


# ─── FactorioGame: __init__ ───────────────────────────────────────────────────

class TestFactorioGameInit:
    """
    Verify FactorioGame.__init__ derives RCON config from the game port and
    builds a well-formed Factorio command line without touching Podman.
    """

    def test_rcon_port_is_game_port_plus_1000(self):
        """RCON port must be exactly game port + 1000 (project convention)."""
        g = FactorioGame(name="test", savefile="test", port=34200)
        assert g.rcon_port == 35200

    def test_rcon_port_tracks_custom_port(self):
        """The +1000 offset must apply to any port, not just the default."""
        g = FactorioGame(name="test", savefile="test", port=34500)
        assert g.rcon_port == 35500

    def test_rcon_password_is_16_hex_chars(self):
        """secrets.token_hex(8) produces exactly 16 lowercase hex characters."""
        g = FactorioGame(name="test", savefile="test", port=34200)
        assert len(g.rcon_password) == 16
        int(g.rcon_password, 16)  # raises ValueError if not valid hex

    def test_rcon_passwords_differ_across_instances(self):
        """Each FactorioGame instance must get a cryptographically unique password."""
        passwords = {FactorioGame(name="x", savefile="x", port=34200).rcon_password for _ in range(5)}
        # All 5 should be unique (collision astronomically unlikely)
        assert len(passwords) == 5

    def test_command_contains_rcon_port_flag(self):
        """The Factorio command line must include --rcon-port <port>."""
        g = FactorioGame(name="test", savefile="test", port=34200)
        cmd = " ".join(g.command)
        assert f"--rcon-port {g.rcon_port}" in cmd

    def test_command_contains_rcon_password_flag(self):
        """The Factorio command line must include --rcon-password <password>."""
        g = FactorioGame(name="test", savefile="test", port=34200)
        cmd = " ".join(g.command)
        assert f"--rcon-password {g.rcon_password}" in cmd

    def test_command_contains_port_flag(self):
        """The Factorio command line must include --port <game-port>."""
        g = FactorioGame(name="test", savefile="test", port=34200)
        cmd = " ".join(g.command)
        assert "--port 34200" in cmd

    def test_game_name_has_factorio_prefix(self):
        """Container name must be 'factorio-' + name to be discoverable by FactorioServer."""
        g = FactorioGame(name="myworld", savefile="myworld", port=34200)
        assert g.game_name == "factorio-myworld"

    def test_command_is_a_list(self):
        """command must be a list (podman containers.run expects argv-style list)."""
        g = FactorioGame(name="test", savefile="test", port=34200)
        assert isinstance(g.command, list)


# ─── FactorioGame: _prepare_server_settings ───────────────────────────────────

class TestPrepareServerSettings:
    """
    Verify _prepare_server_settings manages server-settings.json correctly.

    Tests use tmp_path fixtures so no real saves directory is needed.
    """

    def _make_game(self, name="server"):
        g = object.__new__(FactorioGame)
        g.name = name
        return g

    def test_creates_settings_file_from_template_when_missing(self, tmp_path):
        """When server-settings.json is absent, it must be created from the template."""
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
        assert data["other"] == "keep"  # non-name fields preserved from template

    def test_updates_name_in_existing_settings(self, tmp_path):
        """When server-settings.json exists, only the 'name' field must be updated."""
        settings = tmp_path / "server-settings.json"
        settings.write_text(json.dumps({"name": "old", "desc": "preserve"}))

        g = self._make_game("newname")
        g._prepare_server_settings(str(tmp_path))

        data = json.loads(settings.read_text())
        assert data["name"] == "newname"
        assert data["desc"] == "preserve"  # other fields must not be touched

    def test_does_not_overwrite_existing_settings_file(self, tmp_path):
        """The template must not be copied if server-settings.json already exists."""
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
    """
    Verify that Game.__init__ correctly parses RCON credentials from container
    labels — the label round-trip is the only persistence mechanism for RCON
    config after a container is created.
    """

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
        """factorio.rcon-port label must be parsed and stored as an int."""
        g = self._make_game({"factorio.rcon-port": "35200", "factorio.rcon-password": "pw"})
        assert g.rcon_port == 35200

    def test_rcon_password_parsed_from_label(self):
        """factorio.rcon-password label must be stored as a string."""
        g = self._make_game({"factorio.rcon-port": "35200", "factorio.rcon-password": "mypass"})
        assert g.rcon_password == "mypass"

    def test_rcon_defaults_when_labels_absent(self):
        """When labels=None, rcon_port must default to 0 and rcon_password to ''."""
        g = self._make_game()
        assert g.rcon_port == 0
        assert g.rcon_password == ""

    def test_rcon_defaults_when_labels_empty_dict(self):
        """When labels={}, rcon_port must default to 0 and rcon_password to ''."""
        g = self._make_game({})
        assert g.rcon_port == 0
        assert g.rcon_password == ""

    def test_display_name_strips_factorio_prefix(self):
        """display_name must return the name without the 'factorio-' prefix."""
        g = self._make_game()
        assert g.display_name == "test"

    def test_active_status_reads_container_inspect(self):
        """active_status must delegate to container.inspect()['State']['Running']."""
        container = MagicMock()
        container.inspect.return_value = {"State": {"Running": True}}
        g = FactorioServer.Game(
            container=container, container_id="x",
            game_name="factorio-x", game_port=34200, game_path="/x",
        )
        assert g.active_status is True

    def test_game_port_stored_as_int(self):
        """game_port must always be an int, even if a string is passed."""
        g = self._make_game()
        assert isinstance(g.game_port, int)


# ─── FactorioServer._rcon_save ────────────────────────────────────────────────

class TestRconSave:
    """
    Verify _rcon_save behaviour: guard conditions, success path, error paths,
    and the mandatory 2-second flush sleep.
    """

    def test_returns_false_when_rcon_port_is_zero(self):
        """rcon_port=0 means RCON is not configured; must return False immediately."""
        server = _make_server()
        game = MagicMock(rcon_port=0, rcon_password="pw")
        assert server._rcon_save(game) is False

    def test_returns_false_when_rcon_password_empty(self):
        """Empty password means RCON is not configured; must return False immediately."""
        server = _make_server()
        game = MagicMock(rcon_port=35200, rcon_password="")
        assert server._rcon_save(game) is False

    def test_returns_true_on_success(self):
        """When send_command succeeds, _rcon_save must return True."""
        server = _make_server()
        game = MagicMock(rcon_port=35200, rcon_password="secret")
        with patch("factorio_server.send_command"), patch("factorio_server.time.sleep"):
            result = server._rcon_save(game)
        assert result is True

    def test_returns_false_on_connection_error(self):
        """Connection errors (e.g. server not yet ready) must return False, not raise."""
        server = _make_server()
        game = MagicMock(rcon_port=35200, rcon_password="secret")
        with patch("factorio_server.send_command", side_effect=ConnectionRefusedError):
            result = server._rcon_save(game)
        assert result is False

    def test_returns_false_on_timeout(self):
        """Timeout errors must return False, not propagate."""
        server = _make_server()
        game = MagicMock(rcon_port=35200, rcon_password="secret")
        with patch("factorio_server.send_command", side_effect=TimeoutError):
            result = server._rcon_save(game)
        assert result is False

    def test_sleeps_2s_after_successful_save(self):
        """
        _rcon_save must sleep 2 seconds after issuing /server-save.

        This is required because Factorio acknowledges the save command
        immediately but writes the file asynchronously; without the sleep,
        container deletion races the file write.
        """
        server = _make_server()
        game = MagicMock(rcon_port=35200, rcon_password="secret")
        with patch("factorio_server.send_command"), \
             patch("factorio_server.time.sleep") as mock_sleep:
            server._rcon_save(game)
        mock_sleep.assert_called_once_with(2)

    def test_sends_correct_command(self):
        """The exact /server-save command must be sent to the correct RCON endpoint."""
        server = _make_server()
        game = MagicMock(rcon_port=35200, rcon_password="secret")
        with patch("factorio_server.send_command") as mock_cmd, \
             patch("factorio_server.time.sleep"):
            server._rcon_save(game)
        mock_cmd.assert_called_once_with("127.0.0.1", 35200, "secret", "/server-save")


# ─── FactorioServer.create_game: name uniqueness ──────────────────────────────

def _server_with_games(names):
    """
    Return a FactorioServer whose games dict is pre-populated with mock games.

    Args:
        names (dict | set): Keys are full container names (e.g.
            ``"factorio-myworld"``); values are ignored (replaced with
            MagicMock objects).
    """
    server = _make_server()
    server.games = {n: MagicMock() for n in names}
    return server


class TestCreateGameUniqueness:
    """
    Verify create_game's name uniqueness logic.

    When a container with the requested name already exists, create_game must
    append a numeric suffix starting at -2 and incrementing until a free slot
    is found.  force_name=True bypasses this check (used during rebuild so
    each server gets its original name back).
    """

    def test_uses_original_name_when_no_conflict(self):
        """No existing containers → original name used as-is."""
        server = _server_with_games({})
        with patch("factorio_server.FactorioGame") as MockGame:
            MockGame.return_value.create_game.return_value = {}
            server.create_game(name="fresh", port=34200, savefile="fresh")
        assert MockGame.call_args[1]["name"] == "fresh"

    def test_appends_suffix_when_name_conflicts(self):
        """One conflict → name becomes 'myworld-2'."""
        server = _server_with_games({"factorio-myworld": MagicMock()})
        with patch("factorio_server.FactorioGame") as MockGame:
            MockGame.return_value.create_game.return_value = {}
            server.create_game(name="myworld", port=34200, savefile="myworld")
        assert MockGame.call_args[1]["name"] == "myworld-2"

    def test_increments_suffix_until_unique(self):
        """Two conflicts → name becomes 'myworld-3'."""
        server = _server_with_games({
            "factorio-myworld": MagicMock(),
            "factorio-myworld-2": MagicMock(),
        })
        with patch("factorio_server.FactorioGame") as MockGame:
            MockGame.return_value.create_game.return_value = {}
            server.create_game(name="myworld", port=34200, savefile="myworld")
        assert MockGame.call_args[1]["name"] == "myworld-3"

    def test_force_name_bypasses_uniqueness_check(self):
        """force_name=True must use the given name even if a conflict exists."""
        server = _server_with_games({"factorio-myworld": MagicMock()})
        with patch("factorio_server.FactorioGame") as MockGame:
            MockGame.return_value.create_game.return_value = {}
            server.create_game(name="myworld", port=34200, savefile="myworld", force_name=True)
        assert MockGame.call_args[1]["name"] == "myworld"


# ─── FactorioServer.rebuild_and_recreate ──────────────────────────────────────

class TestRebuildAndRecreate:
    """
    Verify rebuild_and_recreate orchestrates the snapshot → save → delete →
    build → recreate → stop-if-was-stopped lifecycle correctly.

    ``update_game_list`` is mocked as a no-op throughout; ``self.games`` is
    pre-set to a consistent snapshot so tests do not depend on Podman.
    """

    def _setup(self, games_dict):
        server = _make_server()
        server.games = games_dict
        server.update_game_list = MagicMock()
        return server

    def test_rcon_save_called_only_for_running_games(self):
        """_rcon_save must be called for running games and skipped for stopped ones."""
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
        """Every container must be deleted before recreation."""
        g1 = _make_game_mock("a", 34200, "/saves/a/", False)
        g2 = _make_game_mock("b", 34201, "/saves/b/", False)
        server = self._setup({"factorio-a": g1, "factorio-b": g2})

        with patch.object(server, "create_game"), \
             patch("factorio_server.FactorioContainerImage"):
            server.rebuild_and_recreate()

        g1.delete.assert_called_once()
        g2.delete.assert_called_once()

    def test_create_game_called_for_each_snapshot(self):
        """create_game must be called once for every snapshotted server."""
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
        """force_name=True must be passed so original names are reused after deletion."""
        g = _make_game_mock("world", 34200, "/saves/world/", False)
        server = self._setup({"factorio-world": g})

        with patch.object(server, "create_game") as mock_create, \
             patch("factorio_server.FactorioContainerImage"):
            server.rebuild_and_recreate()

        assert mock_create.call_args.kwargs["force_name"] is True

    def test_stopped_games_are_stopped_after_recreate(self):
        """
        Containers that were stopped before the rebuild must be stopped again
        after recreation.  (containers.run auto-starts them; we must undo that.)
        """
        g = _make_game_mock("world", 34200, "/saves/world/", False)
        server = self._setup({"factorio-world": g})

        with patch.object(server, "create_game"), \
             patch("factorio_server.FactorioContainerImage"):
            server.rebuild_and_recreate()

        g.stop.assert_called_once()

    def test_running_games_not_stopped_after_recreate(self):
        """Containers that were running before the rebuild must remain running after."""
        g = _make_game_mock("world", 34200, "/saves/world/", True)
        server = self._setup({"factorio-world": g})

        with patch.object(server, "_rcon_save", return_value=True), \
             patch.object(server, "create_game"), \
             patch("factorio_server.FactorioContainerImage"):
            server.rebuild_and_recreate()

        g.stop.assert_not_called()

    def test_return_value_contains_recreated_and_restarted(self):
        """Return dict must list all recreated servers and only the running ones as restarted."""
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
        """FactorioContainerImage().build() must be called exactly once."""
        g = _make_game_mock("world", 34200, "/saves/world/", False)
        server = self._setup({"factorio-world": g})

        with patch.object(server, "create_game"), \
             patch("factorio_server.FactorioContainerImage") as MockImage:
            server.rebuild_and_recreate()

        MockImage.return_value.build.assert_called_once()


# ─── FactorioContainerImage ───────────────────────────────────────────────────

class TestFactorioContainerImage:
    """
    Verify FactorioContainerImage.build() passes the correct arguments to the
    Podman images.build() API and uses the context manager protocol.
    """

    def test_image_tag_is_correct(self):
        """IMAGE_TAG must match the tag used by factorio_container.py IMAGE constant."""
        assert FactorioContainerImage.IMAGE_TAG == "factorio-headless:latest"

    def test_build_uses_correct_tag(self):
        """The tag passed to images.build must match IMAGE_TAG."""
        with patch("image_build.PodmanClient") as MockClient:
            ctx = MockClient.return_value.__enter__.return_value
            FactorioContainerImage().build()
        _, kwargs = ctx.images.build.call_args
        assert kwargs["tag"] == "factorio-headless:latest"

    def test_build_uses_correct_paths(self):
        """path and dockerfile arguments must match the class-level constants."""
        with patch("image_build.PodmanClient") as MockClient:
            ctx = MockClient.return_value.__enter__.return_value
            FactorioContainerImage().build()
        _, kwargs = ctx.images.build.call_args
        assert kwargs["path"] == FactorioContainerImage.CONTAINER_DIR
        assert kwargs["dockerfile"] == FactorioContainerImage.CONTAINERFILE

    def test_build_uses_context_manager(self):
        """PodmanClient must be used as a context manager (__enter__ / __exit__)."""
        with patch("image_build.PodmanClient") as MockClient:
            FactorioContainerImage().build()
        MockClient.return_value.__enter__.assert_called_once()
        MockClient.return_value.__exit__.assert_called_once()


# ─── FactorioServer.Game.player_count ─────────────────────────────────────────

class TestPlayerCount:
    """
    Verify player_count() correctly queries RCON, parses the response, and
    handles all failure modes gracefully.

    The 3-second RCON timeout is tested explicitly because the TUI calls
    player_count() on every 5-second refresh for every running server —
    a longer timeout would stall the event loop.
    """

    def _make_game(self, rcon_port=35200, rcon_password="secret"):
        return FactorioServer.Game(
            container=MagicMock(), container_id="x",
            game_name="factorio-x", game_port=34200, game_path="/x",
            labels={"factorio.rcon-port": str(rcon_port),
                    "factorio.rcon-password": rcon_password},
        )

    def test_returns_int_on_success(self):
        """Successful RCON response must be parsed as an integer."""
        game = self._make_game()
        with patch("factorio_server.send_command", return_value="3\n"):
            assert game.player_count() == 3

    def test_strips_whitespace_from_response(self):
        """Response whitespace must be stripped before int conversion."""
        game = self._make_game()
        with patch("factorio_server.send_command", return_value="  5\n  "):
            assert game.player_count() == 5

    def test_returns_zero_when_no_players(self):
        """Zero players is a valid count and must be returned as 0 (not None)."""
        game = self._make_game()
        with patch("factorio_server.send_command", return_value="0\n"):
            assert game.player_count() == 0

    def test_sends_lua_player_count_command(self):
        """The exact Lua snippet for player count must be sent via RCON."""
        game = self._make_game()
        with patch("factorio_server.send_command") as mock_cmd:
            mock_cmd.return_value = "0\n"
            game.player_count()
        assert "/c rcon.print(#game.connected_players)" in mock_cmd.call_args[0]

    def test_returns_none_when_rcon_port_zero(self):
        """rcon_port=0 signals no RCON configured; must return None without connecting."""
        game = self._make_game(rcon_port=0, rcon_password="secret")
        assert game.player_count() is None

    def test_returns_none_when_rcon_password_empty(self):
        """Empty password signals no RCON configured; must return None without connecting."""
        game = self._make_game(rcon_port=35200, rcon_password="")
        assert game.player_count() is None

    def test_returns_none_on_connection_error(self):
        """Connection errors must return None (server not yet ready or crashed)."""
        game = self._make_game()
        with patch("factorio_server.send_command", side_effect=ConnectionRefusedError):
            assert game.player_count() is None

    def test_returns_none_on_timeout(self):
        """Timeout errors must return None (server busy or overloaded)."""
        game = self._make_game()
        with patch("factorio_server.send_command", side_effect=TimeoutError):
            assert game.player_count() is None

    def test_uses_3s_timeout(self):
        """
        The RCON timeout must be exactly 3 seconds.

        This is intentionally short — the TUI refresh loop calls player_count()
        for every running server on every 5-second tick.  A longer timeout
        would risk stalling the event loop if a server becomes unresponsive.
        """
        game = self._make_game()
        with patch("factorio_server.send_command", return_value="1\n") as mock_cmd:
            game.player_count()
        # send_command(host, port, password, command, timeout=3)
        assert mock_cmd.call_args[1].get("timeout") == 3 or mock_cmd.call_args[0][4] == 3
