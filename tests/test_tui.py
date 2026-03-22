"""
TUI tests using Textual's async pilot.

Each test replaces ``ControlServer.factorio_driver`` with a ``MagicMock`` so
no real Podman socket is needed.  Tests cover:
  - Widget composition and structure (sections, buttons, containers)
  - Reactive state markup (status labels, player count display)
  - Button interactions (rebuild trigger, new-server creation)
  - Refresh logic (adding and removing ServerEntry widgets)
  - Port picker behaviour (base port, port skipping)

Why monkeypatching the driver at the class level
-------------------------------------------------
``ControlServer.factorio_driver = FactorioServer()`` is evaluated at
class-definition time, which would fail if the Podman socket is absent.  The
conftest.py stub prevents the import error, but individual test methods still
need a mock driver so they can control what ``driver.games`` contains.  Using
``monkeypatch.setattr(ControlServer, "factorio_driver", ...)`` replaces the
class attribute before the app mounts, making all driver accesses in the app
and widgets return the mock.

``ControlServer.server`` is kept as a legacy alias; tests that monkeypatch
``server`` will also need to patch ``factorio_driver`` in the new architecture.
"""
import pytest
from pathlib import Path
from unittest.mock import MagicMock, patch

# server_control imports FactorioServer and executes ControlServer.factorio_driver = FactorioServer()
# at class-definition time.  The podman stub in conftest.py ensures that works.
from server_control import (
    ControlServer,
    FilteredDirectoryTree,
    NewServer,
    ServerEntry,
    UpdateSection,
)


# ─── helpers ──────────────────────────────────────────────────────────────────

def _mock_driver(games=None, game_prefix="factorio-", display_name="Factorio",
                 base_port=34197, supports_player_count=True, supports_save_picker=True):
    """
    Return a MagicMock GameDriver with an optional games dict and sensible defaults.

    Args:
        games (dict | None): Maps container name → mock game object.
            Defaults to an empty dict (no servers).
        game_prefix (str): Container name prefix.
        display_name (str): Tab label.
        base_port (int): Default starting port.
        supports_player_count (bool): Whether the driver reports player counts.
        supports_save_picker (bool): Whether new-server uses file picker.
    """
    d = MagicMock()
    d.games = games or {}
    d.game_prefix = game_prefix
    d.display_name = display_name
    d.base_port = base_port
    d.supports_player_count.return_value = supports_player_count
    d.supports_save_picker.return_value = supports_save_picker
    d.get_all_ports.return_value = {g.game_port for g in (games or {}).values()}
    return d


def _mock_server(games=None):
    """
    Return a MagicMock FactorioServer with an optional games dict.

    Kept for backwards compatibility; delegates to _mock_driver.

    Args:
        games (dict | None): Maps container name → mock game object.
            Defaults to an empty dict (no servers).
    """
    return _mock_driver(games=games)


def _mock_game(name, port, running):
    """
    Return a MagicMock imitating a FactorioServer.Game for TUI tests.

    Args:
        name (str): Short name without ``"factorio-"`` prefix.
        port (int): UDP game port.
        running (bool): Whether the container is running.
    """
    g = MagicMock()
    g.game_name = f"factorio-{name}"
    g.game_port = port
    g.active_status = running
    g.player_count.return_value = None  # default: unknown player count
    return g


def _patch_driver(monkeypatch, games=None, **kwargs):
    """
    Monkeypatch ControlServer.factorio_driver with a mock driver.

    Also patches the legacy ``server`` alias so old code paths that access
    ``self.app.server`` continue to work.

    Returns:
        MagicMock: The mock driver.
    """
    mock = _mock_driver(games=games, **kwargs)
    monkeypatch.setattr(ControlServer, "factorio_driver", mock)
    monkeypatch.setattr(ControlServer, "server", mock)
    return mock


# ─── FilteredDirectoryTree ────────────────────────────────────────────────────

class TestFilteredDirectoryTree:
    """
    Verify filter_paths keeps directories and user-created .zip files while
    excluding autosaves and non-zip files.

    ``filter_paths`` is a pure function on paths; fake ``Path`` objects are
    used so no real filesystem is needed.
    """

    def _make_path(self, name, is_dir=False):
        """Create a MagicMock Path with the given name, suffix, and is_dir() return."""
        p = MagicMock(spec=Path)
        p.name = name
        p.suffix = Path(name).suffix if not is_dir else ""
        p.is_dir.return_value = is_dir
        return p

    def _filter(self, paths):
        """Run filter_paths on *paths* without constructing a full Textual widget."""
        tree = object.__new__(FilteredDirectoryTree)
        return list(tree.filter_paths(paths))

    def test_accepts_directories(self):
        """Directories must pass through so the user can navigate the tree."""
        d = self._make_path("saves", is_dir=True)
        assert d in self._filter([d])

    def test_accepts_zip_files(self):
        """Normal .zip save files must be shown in the tree."""
        z = self._make_path("world.zip")
        assert z in self._filter([z])

    def test_rejects_autosave_zip_files(self):
        """Autosave zips (name starts with '_') must be filtered out."""
        a = self._make_path("_autosave1.zip")
        assert a not in self._filter([a])

    def test_rejects_non_zip_files(self):
        """Files that are not .zip (e.g. .txt, .json) must be filtered out."""
        txt = self._make_path("readme.txt")
        assert txt not in self._filter([txt])

    def test_mixed_list_filtered_correctly(self):
        """A realistic mixed list must pass only directories and user zips."""
        directory = self._make_path("saves", is_dir=True)
        good_zip = self._make_path("world.zip")
        auto_zip = self._make_path("_autosave2.zip")
        text_file = self._make_path("notes.txt")

        result = self._filter([directory, good_zip, auto_zip, text_file])
        assert directory in result
        assert good_zip in result
        assert auto_zip not in result
        assert text_file not in result


# ─── ServerEntry ──────────────────────────────────────────────────────────────

class TestServerEntryMarkup:
    """
    Verify _status_markup returns the correct Rich markup for each state.

    ``_status_markup`` only reads ``self.game_active``; it is called as an
    unbound function on a plain stub object to avoid Textual's reactive
    machinery (which requires a mounted widget).
    """

    def _markup(self, active: bool) -> str:
        """Call _status_markup on a minimal stub with the given active state."""
        class _Stub:
            pass
        stub = _Stub()
        stub.game_active = active
        return ServerEntry._status_markup(stub)

    def test_online_markup_when_active(self):
        """Active server must show 'Online' in its status markup."""
        assert "Online" in self._markup(True)

    def test_offline_markup_when_inactive(self):
        """Stopped server must show 'Offline' in its status markup."""
        assert "Offline" in self._markup(False)

    def test_online_is_not_offline(self):
        """Active and inactive markup strings must be distinct."""
        assert self._markup(True) != self._markup(False)


class TestPlayerCountMarkup:
    """
    Verify _player_count_markup returns the correct display string.

    Called as unbound to avoid Textual reactive machinery.
    """

    def _markup(self, count: int, driver=None) -> str:
        """Call _player_count_markup on a minimal stub with the given count."""
        class _Stub:
            pass
        stub = _Stub()
        stub.player_count = count
        stub._driver = driver
        return ServerEntry._player_count_markup(stub)

    def test_shows_count_when_non_negative(self):
        """Positive player count must render as a numeric string."""
        assert self._markup(3) == "3"

    def test_shows_zero(self):
        """Zero players is a valid count and must render as '0', not '-'."""
        assert self._markup(0) == "0"

    def test_shows_dash_when_negative(self):
        """Negative count (unknown/server offline) must render as '-'."""
        assert self._markup(-1) == "-"

    def test_shows_dash_when_minus_one(self):
        """Specifically -1 (the sentinel value used throughout the app) must show '-'."""
        assert self._markup(-1) == "-"


# ─── TUI composition ──────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_app_renders_with_no_games(monkeypatch):
    """App must mount successfully even when there are no managed containers."""
    _patch_driver(monkeypatch)
    async with ControlServer().run_test() as pilot:
        # Basic smoke-test: app mounted without exceptions
        assert pilot.app is not None


@pytest.mark.asyncio
async def test_app_has_server_container(monkeypatch):
    """The scrollable server list container must be present in the DOM."""
    _patch_driver(monkeypatch)
    async with ControlServer().run_test() as pilot:
        container = pilot.app.query_one("#server_container-factorio")
        assert container is not None


@pytest.mark.asyncio
async def test_app_has_update_section(monkeypatch):
    """The UpdateSection widget must be mounted in the DOM."""
    _patch_driver(monkeypatch)
    async with ControlServer().run_test() as pilot:
        section = pilot.app.query_one("#updatesection")
        assert section is not None


@pytest.mark.asyncio
async def test_app_has_rebuild_button(monkeypatch):
    """The rebuild button must be present and queryable."""
    _patch_driver(monkeypatch)
    async with ControlServer().run_test() as pilot:
        btn = pilot.app.query_one("#rebuild_button")
        assert btn is not None


@pytest.mark.asyncio
async def test_app_has_new_server_section(monkeypatch):
    """The NewServer section must be mounted in the DOM."""
    _patch_driver(monkeypatch)
    async with ControlServer().run_test() as pilot:
        section = pilot.app.query_one("#newserver")
        assert section is not None


@pytest.mark.asyncio
async def test_server_entries_rendered_for_existing_games(monkeypatch):
    """One ServerEntry widget must be mounted for each game in driver.games."""
    games = {
        "factorio-world1": _mock_game("world1", 34200, True),
        "factorio-world2": _mock_game("world2", 34201, False),
    }
    _patch_driver(monkeypatch, games=games)
    async with ControlServer().run_test() as pilot:
        entries = pilot.app.query(ServerEntry)
        assert len(list(entries)) == 2


@pytest.mark.asyncio
async def test_server_entry_shows_correct_port(monkeypatch):
    """The port label in a ServerEntry must display the game's UDP port."""
    games = {"factorio-mymap": _mock_game("mymap", 34197, False)}
    _patch_driver(monkeypatch, games=games)
    async with ControlServer().run_test() as pilot:
        port_label = pilot.app.query_one("#server-factorio-mymap #server_port")
        assert "34197" in str(port_label.renderable)


@pytest.mark.asyncio
async def test_server_entry_shows_name_without_prefix(monkeypatch):
    """The name label must strip the 'factorio-' prefix for display."""
    games = {"factorio-mymap": _mock_game("mymap", 34197, False)}
    _patch_driver(monkeypatch, games=games)
    async with ControlServer().run_test() as pilot:
        name_label = pilot.app.query_one("#server-factorio-mymap #server_name")
        rendered = str(name_label.renderable)
        assert "mymap" in rendered
        assert "factorio-" not in rendered


# ─── Rebuild button ───────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_rebuild_button_triggers_rebuild_and_recreate(monkeypatch):
    """
    Clicking the rebuild button must call driver.rebuild_and_recreate() exactly once.

    Uses a tall terminal (size=(120, 60)) so UpdateSection is visible and
    the button can receive the click event.
    """
    driver = _patch_driver(monkeypatch)
    driver.rebuild_and_recreate.return_value = {"recreated": [], "restarted": []}

    # Use a tall terminal so UpdateSection (below the server list) is in view.
    async with ControlServer().run_test(size=(120, 60)) as pilot:
        await pilot.click("#rebuild_button")
        await pilot.pause(delay=0.1)

    driver.rebuild_and_recreate.assert_called_once()


# ─── NewServer port picker ────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_new_server_port_defaults_to_base_port(monkeypatch):
    """When no servers exist, the port input must default to BASE_PORT."""
    _patch_driver(monkeypatch)
    async with ControlServer().run_test() as pilot:
        port_input = pilot.app.query_one("#port_selection")
        assert port_input.value == str(NewServer.BASE_PORT)


@pytest.mark.asyncio
async def test_new_server_port_skips_in_use_ports(monkeypatch):
    """
    When the first N ports from BASE_PORT are occupied, the input must show
    BASE_PORT + N (the first free port).
    """
    base = NewServer.BASE_PORT
    games = {f"factorio-g{i}": _mock_game(f"g{i}", base + i, True) for i in range(3)}
    _patch_driver(monkeypatch, games=games)
    async with ControlServer().run_test() as pilot:
        port_input = pilot.app.query_one("#port_selection")
        assert int(port_input.value) == base + 3


# ─── refresh_server_list ──────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_refresh_removes_deleted_game_entries(monkeypatch):
    """
    After a game disappears from driver.games, refresh_server_list must
    remove the corresponding ServerEntry widget from the DOM.
    """
    games = {"factorio-gone": _mock_game("gone", 34200, False)}
    driver = _patch_driver(monkeypatch, games=games)

    async with ControlServer().run_test() as pilot:
        # Simulate the game disappearing (container deleted externally)
        driver.games = {}
        pilot.app.refresh_server_list()
        await pilot.pause()

        entries = list(pilot.app.query(ServerEntry))
        assert len(entries) == 0


@pytest.mark.asyncio
async def test_refresh_adds_new_game_entries(monkeypatch):
    """
    After a new game appears in driver.games, refresh_server_list must
    mount a new ServerEntry widget for it.
    """
    driver = _patch_driver(monkeypatch)

    async with ControlServer().run_test() as pilot:
        new_game = _mock_game("newworld", 34200, True)
        driver.games = {"factorio-newworld": new_game}
        pilot.app.refresh_server_list()
        await pilot.pause()

        entries = list(pilot.app.query(ServerEntry))
        assert len(entries) == 1


# ─── Player count display ──────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_server_entry_shows_player_count(monkeypatch):
    """A running server with known player count must display that count."""
    game = _mock_game("mymap", 34200, True)
    game.player_count.return_value = 2
    games = {"factorio-mymap": game}
    _patch_driver(monkeypatch, games=games)
    async with ControlServer().run_test() as pilot:
        label = pilot.app.query_one("#server-factorio-mymap #player_count")
        assert "2" in str(label.renderable)


@pytest.mark.asyncio
async def test_stopped_server_shows_dash_for_player_count(monkeypatch):
    """Stopped game: player_count() is not called, label shows '-'."""
    game = _mock_game("mymap", 34200, False)
    games = {"factorio-mymap": game}
    _patch_driver(monkeypatch, games=games)
    async with ControlServer().run_test() as pilot:
        label = pilot.app.query_one("#server-factorio-mymap #player_count")
        assert str(label.renderable) == "-"


@pytest.mark.asyncio
async def test_player_count_not_queried_for_stopped_game(monkeypatch):
    """
    Verify player_count() is never called for a stopped container.

    Calling RCON on a stopped container would always fail; this guard prevents
    unnecessary timeouts during each refresh cycle.
    """
    game = _mock_game("mymap", 34200, False)
    games = {"factorio-mymap": game}
    _patch_driver(monkeypatch, games=games)
    async with ControlServer().run_test() as pilot:
        game.player_count.assert_not_called()


@pytest.mark.asyncio
async def test_refresh_updates_player_count(monkeypatch):
    """After refresh, player count label must reflect the updated value."""
    game = _mock_game("mymap", 34200, True)
    game.player_count.return_value = 0
    games = {"factorio-mymap": game}
    driver = _patch_driver(monkeypatch, games=games)

    async with ControlServer().run_test() as pilot:
        # Simulate a player joining between refreshes
        game.player_count.return_value = 1
        pilot.app.refresh_server_list()
        await pilot.pause()

        label = pilot.app.query_one("#server-factorio-mymap #player_count")
        assert "1" in str(label.renderable)
