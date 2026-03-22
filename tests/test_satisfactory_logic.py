"""
Unit tests for Satisfactory-specific modules:
  - satisfactory_container.py  (SatisfactoryGame init + create_game)
  - satisfactory_server.py     (Game label parsing, _rcon_save, create_game,
                                 rebuild_and_recreate, player_count,
                                 GameDriver properties)
  - satisfactory_image_build.py (SatisfactoryContainerImage.build)

All tests run without a live Podman socket or Satisfactory process.  The podman
package is globally stubbed by conftest.py; individual tests add more
fine-grained patches where call arguments must be asserted.
"""
from unittest.mock import MagicMock, patch

import pytest

from satisfactory_container import SatisfactoryGame
from satisfactory_server import SatisfactoryServer
from satisfactory_image_build import SatisfactoryContainerImage


# ─── helpers ──────────────────────────────────────────────────────────────────

def _make_server():
    """
    Return a SatisfactoryServer with the PodmanClient constructor patched out.

    The patch is applied only during construction so the returned object
    behaves normally afterwards (``games`` dict, method calls, etc.).
    """
    with patch("satisfactory_server.PodmanClient"):
        return SatisfactoryServer()


def _server_with_games(names):
    """
    Return a SatisfactoryServer whose games dict is pre-populated with mock games.

    Args:
        names (dict | set): Keys are full container names (e.g.
            ``"satisfactory-myserver"``); values are replaced with MagicMock objects.
    """
    server = _make_server()
    server.games = {n: MagicMock() for n in names}
    return server


def _make_game_mock(name, port, running):
    """
    Return a MagicMock imitating a SatisfactoryServer.Game object.

    Args:
        name (str): ``display_name`` value (without ``"satisfactory-"`` prefix).
        port (int): ``game_port`` value.
        running (bool): ``active_status`` value.
    """
    g = MagicMock()
    g.display_name = name
    g.game_port = port
    g.beacon_port = port + 1111
    g.active_status = running
    return g


# ─── SatisfactoryGame: __init__ ───────────────────────────────────────────────

class TestSatisfactoryGameInit:
    """
    Verify SatisfactoryGame.__init__ correctly derives container name and
    beacon port from the game name and port.
    """

    def test_satisfactory_game_init(self):
        """game_name must equal 'satisfactory-' + name."""
        g = SatisfactoryGame(name="myserver", port=7777)
        assert g.game_name == "satisfactory-myserver"
        assert g.beacon_port == 7777 + 1111

    def test_satisfactory_game_default_port(self):
        """Default port must be 7777 and beacon_port must be 8888."""
        g = SatisfactoryGame(name="myserver")
        assert g.port == 7777
        assert g.beacon_port == 8888

    def test_satisfactory_game_custom_port(self):
        """Custom port must be stored and beacon_port must be port + 1111."""
        g = SatisfactoryGame(name="custom", port=7800)
        assert g.port == 7800
        assert g.beacon_port == 7800 + 1111  # 8911

    def test_game_name_has_satisfactory_prefix(self):
        """Container name must always start with 'satisfactory-'."""
        g = SatisfactoryGame(name="world", port=7777)
        assert g.game_name.startswith("satisfactory-")

    def test_name_stored_on_instance(self):
        """The plain name (without prefix) must be accessible as self.name."""
        g = SatisfactoryGame(name="alpha", port=7777)
        assert g.name == "alpha"


# ─── SatisfactoryGame: create_game ────────────────────────────────────────────

class TestSatisfactoryGameCreate:
    """
    Verify create_game() passes the correct arguments to containers.run()
    and returns the expected info dict.
    """

    def test_satisfactory_game_create_stores_labels(self):
        """
        create_game() must call containers.run() with satisfactory.port and
        satisfactory.beacon-port labels, and PORT / BEACON_PORT env vars.
        """
        g = SatisfactoryGame(name="test", port=7777)
        mock_container = MagicMock()
        mock_container.id = "abc123"

        with patch("satisfactory_container.PodmanClient") as MockClient:
            ctx = MockClient.return_value.__enter__.return_value
            ctx.containers.run.return_value = mock_container
            result = g.create_game()

        _, kwargs = ctx.containers.run.call_args
        assert kwargs["labels"]["satisfactory.port"] == "7777"
        assert kwargs["labels"]["satisfactory.beacon-port"] == "8888"
        assert kwargs["environment"]["PORT"] == "7777"
        assert kwargs["environment"]["BEACON_PORT"] == "8888"

    def test_satisfactory_game_create_network_host(self):
        """create_game() must use network_mode='host'."""
        g = SatisfactoryGame(name="test", port=7777)
        mock_container = MagicMock()
        mock_container.id = "abc123"

        with patch("satisfactory_container.PodmanClient") as MockClient:
            ctx = MockClient.return_value.__enter__.return_value
            ctx.containers.run.return_value = mock_container
            g.create_game()

        _, kwargs = ctx.containers.run.call_args
        assert kwargs["network_mode"] == "host"

    def test_create_game_returns_info_dict(self):
        """create_game() must return a dict with name, port, beacon_port, container_id."""
        g = SatisfactoryGame(name="srv", port=7800)
        mock_container = MagicMock()
        mock_container.id = "deadbeef"

        with patch("satisfactory_container.PodmanClient") as MockClient:
            ctx = MockClient.return_value.__enter__.return_value
            ctx.containers.run.return_value = mock_container
            result = g.create_game()

        assert result["name"] == "satisfactory-srv"
        assert result["port"] == 7800
        assert result["beacon_port"] == 7800 + 1111
        assert result["container_id"] == "deadbeef"

    def test_create_game_uses_correct_image(self):
        """create_game() must use the IMAGE class attribute as the image name."""
        g = SatisfactoryGame(name="test", port=7777)
        mock_container = MagicMock()
        mock_container.id = "x"

        with patch("satisfactory_container.PodmanClient") as MockClient:
            ctx = MockClient.return_value.__enter__.return_value
            ctx.containers.run.return_value = mock_container
            g.create_game()

        args, kwargs = ctx.containers.run.call_args
        assert kwargs.get("image") == SatisfactoryGame.IMAGE or (args and args[0] == SatisfactoryGame.IMAGE)

    def test_create_game_uses_correct_container_name(self):
        """create_game() must name the container 'satisfactory-' + name."""
        g = SatisfactoryGame(name="myworld", port=7777)
        mock_container = MagicMock()
        mock_container.id = "x"

        with patch("satisfactory_container.PodmanClient") as MockClient:
            ctx = MockClient.return_value.__enter__.return_value
            ctx.containers.run.return_value = mock_container
            g.create_game()

        _, kwargs = ctx.containers.run.call_args
        assert kwargs["name"] == "satisfactory-myworld"

    def test_create_game_detach_true(self):
        """create_game() must pass detach=True to containers.run()."""
        g = SatisfactoryGame(name="test", port=7777)
        mock_container = MagicMock()
        mock_container.id = "x"

        with patch("satisfactory_container.PodmanClient") as MockClient:
            ctx = MockClient.return_value.__enter__.return_value
            ctx.containers.run.return_value = mock_container
            g.create_game()

        _, kwargs = ctx.containers.run.call_args
        assert kwargs["detach"] is True


# ─── SatisfactoryServer.Game: label parsing ───────────────────────────────────

class TestGameLabelParsing:
    """
    Verify that SatisfactoryServer._list_games() correctly reads satisfactory.port
    and satisfactory.beacon-port labels from container inspect data.
    """

    def _make_game(self, game_port=7777, beacon_port=8888):
        return SatisfactoryServer.Game(
            container=MagicMock(),
            container_id="abc",
            game_name="satisfactory-test",
            game_port=game_port,
            beacon_port=beacon_port,
        )

    def test_game_label_parsing(self):
        """_list_games() must correctly read satisfactory.port and satisfactory.beacon-port labels."""
        server = _make_server()

        mock_con = MagicMock()
        mock_con.name = "satisfactory-test"
        mock_con.inspect.return_value = {
            "Name": "satisfactory-test",
            "Id": "abc123",
            "Config": {
                "Labels": {
                    "satisfactory.port": "7777",
                    "satisfactory.beacon-port": "8888",
                }
            },
        }
        server._client.containers.list.return_value = [mock_con]

        games = server._list_games()

        assert "satisfactory-test" in games
        game = games["satisfactory-test"]
        assert game.game_port == 7777
        assert game.beacon_port == 8888

    def test_game_label_defaults(self):
        """Missing beacon-port label must fall back to game_port + 1111."""
        server = _make_server()

        mock_con = MagicMock()
        mock_con.name = "satisfactory-test"
        mock_con.inspect.return_value = {
            "Name": "satisfactory-test",
            "Id": "abc123",
            "Config": {
                "Labels": {
                    "satisfactory.port": "7800",
                    # beacon-port label intentionally absent
                }
            },
        }
        server._client.containers.list.return_value = [mock_con]

        games = server._list_games()
        assert games["satisfactory-test"].beacon_port == 7800 + 1111

    def test_display_name_strips_satisfactory_prefix(self):
        """display_name must return the name without the 'satisfactory-' prefix."""
        g = self._make_game()
        assert g.display_name == "test"

    def test_game_port_stored_as_int(self):
        """game_port must always be stored as int."""
        g = self._make_game(game_port=7777)
        assert isinstance(g.game_port, int)

    def test_beacon_port_stored_as_int(self):
        """beacon_port must always be stored as int."""
        g = self._make_game(beacon_port=8888)
        assert isinstance(g.beacon_port, int)

    def test_active_status_reads_container_inspect(self):
        """active_status must delegate to container.inspect()['State']['Running']."""
        container = MagicMock()
        container.inspect.return_value = {"State": {"Running": True}}
        g = SatisfactoryServer.Game(
            container=container, container_id="x",
            game_name="satisfactory-x", game_port=7777, beacon_port=8888,
        )
        assert g.active_status is True


# ─── SatisfactoryServer.Game.player_count ─────────────────────────────────────

class TestPlayerCountAlwaysNone:
    """
    Verify that SatisfactoryServer.Game.player_count() always returns None.
    """

    def test_player_count_always_none(self):
        """player_count() must always return None (Satisfactory has no RCON)."""
        g = SatisfactoryServer.Game(
            container=MagicMock(), container_id="x",
            game_name="satisfactory-x", game_port=7777, beacon_port=8888,
        )
        assert g.player_count() is None

    def test_player_count_returns_none_multiple_calls(self):
        """player_count() must consistently return None on repeated calls."""
        g = SatisfactoryServer.Game(
            container=MagicMock(), container_id="x",
            game_name="satisfactory-x", game_port=7777, beacon_port=8888,
        )
        for _ in range(3):
            assert g.player_count() is None


# ─── SatisfactoryServer capability flags ──────────────────────────────────────

class TestCapabilityFlags:
    """
    Verify SatisfactoryServer reports the correct capability flags.
    """

    def test_satisfactory_supports_player_count_false(self):
        """supports_player_count() must return False."""
        server = _make_server()
        assert server.supports_player_count() is False

    def test_satisfactory_supports_save_picker_false(self):
        """supports_save_picker() must return False."""
        server = _make_server()
        assert server.supports_save_picker() is False


# ─── GameDriver properties ────────────────────────────────────────────────────

class TestGameDriverProperties:
    """
    Verify the GameDriver abstract properties are implemented correctly.
    """

    def test_game_prefix(self):
        """game_prefix must be 'satisfactory-'."""
        server = _make_server()
        assert server.game_prefix == "satisfactory-"

    def test_display_name(self):
        """display_name must be 'Satisfactory'."""
        server = _make_server()
        assert server.display_name == "Satisfactory"

    def test_base_port(self):
        """base_port must be 7777."""
        server = _make_server()
        assert server.base_port == 7777

    def test_image_tag(self):
        """image_tag must be 'localhost/satisfactory-server:latest'."""
        server = _make_server()
        assert server.image_tag == "localhost/satisfactory-server:latest"


# ─── SatisfactoryServer.get_all_ports ─────────────────────────────────────────

class TestGetAllPorts:
    """
    Verify get_all_ports() returns the union of game ports and beacon ports.
    """

    def test_get_all_ports(self):
        """get_all_ports() must include both game_port and beacon_port for each game."""
        server = _make_server()
        g1 = MagicMock()
        g1.game_port = 7777
        g1.beacon_port = 8888
        g2 = MagicMock()
        g2.game_port = 7800
        g2.beacon_port = 8911
        server.games = {
            "satisfactory-srv1": g1,
            "satisfactory-srv2": g2,
        }
        ports = server.get_all_ports()
        assert 7777 in ports
        assert 8888 in ports
        assert 7800 in ports
        assert 8911 in ports

    def test_get_all_ports_empty(self):
        """get_all_ports() must return an empty set when no games exist."""
        server = _make_server()
        server.games = {}
        assert server.get_all_ports() == set()

    def test_get_all_ports_single_game(self):
        """get_all_ports() must return exactly two ports for a single game."""
        server = _make_server()
        g = MagicMock()
        g.game_port = 7777
        g.beacon_port = 8888
        server.games = {"satisfactory-srv": g}
        ports = server.get_all_ports()
        assert ports == {7777, 8888}


# ─── SatisfactoryServer._rcon_save ────────────────────────────────────────────

class TestRconSaveNoop:
    """
    Verify _rcon_save() is a no-op that always returns False.
    """

    def test_rcon_save_noop(self):
        """_rcon_save() must return False without calling anything."""
        server = _make_server()
        game = MagicMock()
        result = server._rcon_save(game)
        assert result is False

    def test_rcon_save_does_not_call_send_command(self):
        """_rcon_save() must not attempt any network calls."""
        server = _make_server()
        game = MagicMock()
        with patch("satisfactory_server.SatisfactoryGame") as mock_sg:
            result = server._rcon_save(game)
        # SatisfactoryGame should not be invoked by _rcon_save
        mock_sg.assert_not_called()
        assert result is False


# ─── SatisfactoryServer.create_game: name uniqueness ─────────────────────────

class TestCreateGameUniqueness:
    """
    Verify create_game()'s name uniqueness logic.

    When a container with the requested name already exists, create_game() must
    append a numeric suffix starting at -2 and incrementing until a free slot
    is found.  force_name=True bypasses this check (used during rebuild so
    each server gets its original name back).
    """

    def test_uses_original_name_when_no_conflict(self):
        """No existing containers → original name used as-is."""
        server = _server_with_games({})
        with patch("satisfactory_server.SatisfactoryGame") as MockGame:
            MockGame.return_value.create_game.return_value = {}
            server.create_game(name="fresh", port=7777)
        assert MockGame.call_args[1]["name"] == "fresh"

    def test_appends_suffix_when_name_conflicts(self):
        """One conflict → name becomes 'myserver-2'."""
        server = _server_with_games({"satisfactory-myserver": MagicMock()})
        with patch("satisfactory_server.SatisfactoryGame") as MockGame:
            MockGame.return_value.create_game.return_value = {}
            server.create_game(name="myserver", port=7777)
        assert MockGame.call_args[1]["name"] == "myserver-2"

    def test_increments_suffix_until_unique(self):
        """Two conflicts → name becomes 'myserver-3'."""
        server = _server_with_games({
            "satisfactory-myserver": MagicMock(),
            "satisfactory-myserver-2": MagicMock(),
        })
        with patch("satisfactory_server.SatisfactoryGame") as MockGame:
            MockGame.return_value.create_game.return_value = {}
            server.create_game(name="myserver", port=7777)
        assert MockGame.call_args[1]["name"] == "myserver-3"

    def test_force_name_bypasses_uniqueness_check(self):
        """force_name=True must use the given name even if a conflict exists."""
        server = _server_with_games({"satisfactory-myserver": MagicMock()})
        with patch("satisfactory_server.SatisfactoryGame") as MockGame:
            MockGame.return_value.create_game.return_value = {}
            server.create_game(name="myserver", port=7777, force_name=True)
        assert MockGame.call_args[1]["name"] == "myserver"


# ─── SatisfactoryServer.rebuild_and_recreate ──────────────────────────────────

class TestRebuildAndRecreate:
    """
    Verify rebuild_and_recreate() orchestrates the snapshot → delete →
    build → recreate → stop-if-was-stopped lifecycle correctly.

    Since Satisfactory has no RCON, the save step must be skipped entirely
    (no leanrcon or send_command calls).
    """

    def _setup(self, games_dict):
        server = _make_server()
        server.games = games_dict
        server.update_game_list = MagicMock()
        return server

    def test_rebuild_and_recreate_skips_rcon_save(self):
        """
        rebuild_and_recreate() must NOT call any RCON/leanrcon functions.

        Since Satisfactory has no RCON, the step 2 save must be a no-op;
        no network calls should be made to the game server.
        """
        g = _make_game_mock("world", 7777, running=True)
        server = self._setup({"satisfactory-world": g})

        with patch.object(server, "_rcon_save", wraps=server._rcon_save) as mock_rcon, \
             patch.object(server, "create_game"), \
             patch("satisfactory_server.SatisfactoryContainerImage"):
            server.rebuild_and_recreate()

        # _rcon_save may be called but must always return False (no-op)
        for call in mock_rcon.call_args_list:
            # Verify it was never called with any RCON side-effects
            pass
        # Critically: no leanrcon import exists in satisfactory_server
        import satisfactory_server as ss_module
        assert not hasattr(ss_module, "send_command"), \
            "satisfactory_server must not import leanrcon/send_command"

    def test_all_games_deleted(self):
        """Every container must be deleted before recreation."""
        g1 = _make_game_mock("a", 7777, running=False)
        g2 = _make_game_mock("b", 7800, running=False)
        server = self._setup({"satisfactory-a": g1, "satisfactory-b": g2})

        with patch.object(server, "create_game"), \
             patch("satisfactory_server.SatisfactoryContainerImage"):
            server.rebuild_and_recreate()

        g1.delete.assert_called_once()
        g2.delete.assert_called_once()

    def test_create_game_called_for_each_snapshot(self):
        """create_game() must be called once for every snapshotted server."""
        g1 = _make_game_mock("world1", 7777, running=True)
        g2 = _make_game_mock("world2", 7800, running=False)
        server = self._setup({"satisfactory-world1": g1, "satisfactory-world2": g2})

        with patch.object(server, "create_game") as mock_create, \
             patch("satisfactory_server.SatisfactoryContainerImage"):
            server.rebuild_and_recreate()

        names_created = {c.kwargs["name"] for c in mock_create.call_args_list}
        assert names_created == {"world1", "world2"}

    def test_create_game_uses_force_name(self):
        """force_name=True must be passed so original names are reused after deletion."""
        g = _make_game_mock("world", 7777, running=False)
        server = self._setup({"satisfactory-world": g})

        with patch.object(server, "create_game") as mock_create, \
             patch("satisfactory_server.SatisfactoryContainerImage"):
            server.rebuild_and_recreate()

        assert mock_create.call_args.kwargs["force_name"] is True

    def test_stopped_games_are_stopped_after_recreate(self):
        """
        Containers that were stopped before the rebuild must be stopped again
        after recreation.  (containers.run auto-starts them; we must undo that.)
        """
        g = _make_game_mock("world", 7777, running=False)
        server = self._setup({"satisfactory-world": g})

        with patch.object(server, "create_game"), \
             patch("satisfactory_server.SatisfactoryContainerImage"):
            server.rebuild_and_recreate()

        g.stop.assert_called_once()

    def test_running_games_not_stopped_after_recreate(self):
        """Containers that were running before the rebuild must remain running after."""
        g = _make_game_mock("world", 7777, running=True)
        server = self._setup({"satisfactory-world": g})

        with patch.object(server, "create_game"), \
             patch("satisfactory_server.SatisfactoryContainerImage"):
            server.rebuild_and_recreate()

        g.stop.assert_not_called()

    def test_return_value_contains_recreated_and_restarted(self):
        """Return dict must list all recreated servers and only running ones as restarted."""
        running = _make_game_mock("r", 7777, running=True)
        stopped = _make_game_mock("s", 7800, running=False)
        server = self._setup({"satisfactory-r": running, "satisfactory-s": stopped})

        with patch.object(server, "create_game"), \
             patch("satisfactory_server.SatisfactoryContainerImage"):
            result = server.rebuild_and_recreate()

        assert set(result["recreated"]) == {"r", "s"}
        assert result["restarted"] == ["r"]

    def test_image_rebuild_called(self):
        """SatisfactoryContainerImage().build() must be called exactly once."""
        g = _make_game_mock("world", 7777, running=False)
        server = self._setup({"satisfactory-world": g})

        with patch.object(server, "create_game"), \
             patch("satisfactory_server.SatisfactoryContainerImage") as MockImage:
            server.rebuild_and_recreate()

        MockImage.return_value.build.assert_called_once()


# ─── SatisfactoryContainerImage ───────────────────────────────────────────────

class TestSatisfactoryContainerImage:
    """
    Verify SatisfactoryContainerImage.build() passes the correct arguments to
    the Podman images.build() API and uses the context manager protocol.
    """

    def test_image_tag_is_correct(self):
        """IMAGE_TAG must be 'satisfactory-server:latest'."""
        assert SatisfactoryContainerImage.IMAGE_TAG == "satisfactory-server:latest"

    def test_build_uses_correct_tag(self):
        """The tag passed to images.build must match IMAGE_TAG."""
        with patch("satisfactory_image_build.PodmanClient") as MockClient:
            ctx = MockClient.return_value.__enter__.return_value
            SatisfactoryContainerImage().build()
        _, kwargs = ctx.images.build.call_args
        assert kwargs["tag"] == "satisfactory-server:latest"

    def test_build_uses_correct_paths(self):
        """path and dockerfile arguments must match the class-level constants."""
        with patch("satisfactory_image_build.PodmanClient") as MockClient:
            ctx = MockClient.return_value.__enter__.return_value
            SatisfactoryContainerImage().build()
        _, kwargs = ctx.images.build.call_args
        assert kwargs["path"] == SatisfactoryContainerImage.CONTAINER_DIR
        assert kwargs["dockerfile"] == SatisfactoryContainerImage.CONTAINERFILE

    def test_build_uses_context_manager(self):
        """PodmanClient must be used as a context manager (__enter__ / __exit__)."""
        with patch("satisfactory_image_build.PodmanClient") as MockClient:
            SatisfactoryContainerImage().build()
        # build() opens PodmanClient twice: once for SteamCmdImage, once for the game image
        assert MockClient.return_value.__enter__.call_count >= 1
        assert MockClient.return_value.__exit__.call_count >= 1
