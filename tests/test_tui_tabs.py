"""
TUI tab tests using Textual's async pilot.

Covers the multi-game tabbed interface introduced in the multi-game-tabs
rewrite.  Each test works with mock drivers so no real Podman socket or game
binary is needed.

Tests:
  - App composes with TabbedContent
  - Factorio TabPane is present
  - Satisfactory TabPane appears when driver is registered
  - GameTab widget is present
  - ServerEntry strips game_prefix from game_name for display
  - ServerEntry shows '-' when driver.supports_player_count() is False
  - _next_available_port skips ports in use across drivers
  - _all_ports_in_use returns union of all driver port sets
  - NewServer shows Input#name_input when supports_save_picker() is False
  - NewServer shows FilteredDirectoryTree when supports_save_picker() is True
  - UpdateSection rebuild button calls driver.rebuild_and_recreate()
"""
import pytest
from unittest.mock import MagicMock, patch

from server_control import (
    ControlServer,
    FilteredDirectoryTree,
    GameTab,
    NewServer,
    ServerEntry,
    UpdateSection,
)
from game_driver import _all_ports_in_use, _next_available_port
from textual.widgets import TabbedContent, TabPane, Tab, Input


# ─── helpers ──────────────────────────────────────────────────────────────────

def _mock_driver(games=None, game_prefix="factorio-", display_name="Factorio",
                 base_port=34197, supports_player_count=True, supports_save_picker=True):
    """
    Return a MagicMock GameDriver with sensible defaults for tab tests.

    Args:
        games (dict | None): Maps container name → mock game object.
        game_prefix (str): Container name prefix used by this driver.
        display_name (str): Human-readable tab label.
        base_port (int): Default starting port for new servers.
        supports_player_count (bool): Whether player count is available.
        supports_save_picker (bool): Whether new-server needs a save file.
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


def _mock_game(prefix, name, port, running, player_count=None):
    """
    Return a MagicMock game object for a given driver prefix.

    Args:
        prefix (str): Full game prefix, e.g. ``"factorio-"``.
        name (str): Short name without prefix.
        port (int): UDP game port.
        running (bool): Whether the container is currently running.
        player_count (int | None): Player count to return from player_count().
    """
    g = MagicMock()
    g.game_name = f"{prefix}{name}"
    g.game_port = port
    g.active_status = running
    g.player_count.return_value = player_count
    return g


def _patch_factorio_driver(monkeypatch, games=None, **kwargs):
    """
    Monkeypatch ControlServer.factorio_driver with a mock Factorio driver.

    Also patches the legacy ``server`` alias.

    Returns:
        MagicMock: The mock driver.
    """
    mock = _mock_driver(games=games, game_prefix="factorio-",
                        display_name="Factorio", **kwargs)
    monkeypatch.setattr(ControlServer, "factorio_driver", mock)
    monkeypatch.setattr(ControlServer, "server", mock)
    return mock


# ─── Tab composition ──────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_app_has_tabbed_content(monkeypatch):
    """App must compose with a TabbedContent widget."""
    _patch_factorio_driver(monkeypatch)
    async with ControlServer().run_test() as pilot:
        tc = pilot.app.query_one(TabbedContent)
        assert tc is not None


@pytest.mark.asyncio
async def test_factorio_tab_exists(monkeypatch):
    """A Tab with label 'Factorio' must be present in the DOM."""
    _patch_factorio_driver(monkeypatch)
    async with ControlServer().run_test() as pilot:
        tabs = list(pilot.app.query(Tab))
        labels = [str(t.label) for t in tabs]
        assert any("Factorio" in lbl for lbl in labels), \
            f"Expected 'Factorio' tab, found: {labels}"


@pytest.mark.asyncio
async def test_satisfactory_tab_exists(monkeypatch):
    """
    A Tab with label 'Satisfactory' must appear when a SatisfactoryServer
    driver is registered on ControlServer.

    We patch the satisfactory_server module into sys.modules so that the
    lazy import inside all_drivers succeeds and returns our mock driver.
    """
    _patch_factorio_driver(monkeypatch)
    sat_driver = _mock_driver(
        game_prefix="satisfactory-",
        display_name="Satisfactory",
        base_port=7777,
        supports_player_count=False,
        supports_save_picker=False,
    )
    # Patch the import inside all_drivers so SatisfactoryServer is "available"
    # and returns our sat_driver.
    fake_module = MagicMock()
    fake_module.SatisfactoryServer = MagicMock(return_value=sat_driver)
    # Remove any cached _satisfactory_driver from a prior test run so the
    # lazy import path in all_drivers is exercised fresh.
    if hasattr(ControlServer, "_satisfactory_driver"):
        try:
            delattr(ControlServer, "_satisfactory_driver")
        except AttributeError:
            pass
    with patch.dict("sys.modules", {"satisfactory_server": fake_module}):
        async with ControlServer().run_test() as pilot:
            tabs = list(pilot.app.query(Tab))
            labels = [str(t.label) for t in tabs]
            assert any("Satisfactory" in lbl for lbl in labels), \
                f"Expected 'Satisfactory' tab, found: {labels}"


@pytest.mark.asyncio
async def test_game_tab_widget_present(monkeypatch):
    """GameTab widget must be present in the composed app."""
    _patch_factorio_driver(monkeypatch)
    async with ControlServer().run_test() as pilot:
        tabs = list(pilot.app.query(GameTab))
        assert len(tabs) >= 1


# ─── ServerEntry display ──────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_server_entry_uses_driver_prefix(monkeypatch):
    """
    ServerEntry must strip the driver's game_prefix from the game_name for
    the display label, not hardcode 'factorio-'.
    """
    game = _mock_game("factorio-", "mymap", 34197, False)
    driver = _patch_factorio_driver(monkeypatch, games={"factorio-mymap": game})
    async with ControlServer().run_test() as pilot:
        name_label = pilot.app.query_one("#server-factorio-mymap #server_name")
        rendered = str(name_label.renderable)
        assert "mymap" in rendered
        assert "factorio-" not in rendered


@pytest.mark.asyncio
async def test_server_entry_shows_dash_for_no_player_count(monkeypatch):
    """
    When driver.supports_player_count() returns False, the player count label
    must always show '-' regardless of the actual player_count value.
    """
    game = _mock_game("factorio-", "mymap", 34200, True)
    game.player_count.return_value = 5  # would show '5' if player count was supported
    games = {"factorio-mymap": game}
    _patch_factorio_driver(monkeypatch, games=games, supports_player_count=False)
    async with ControlServer().run_test() as pilot:
        label = pilot.app.query_one("#server-factorio-mymap #player_count")
        assert str(label.renderable) == "-"


# ─── Port utilities ───────────────────────────────────────────────────────────

def test_port_suggestion_avoids_conflicts():
    """
    _next_available_port must skip ports that are in use across all drivers,
    even when the conflicts span multiple drivers.
    """
    driver_a = _mock_driver(games={
        "factorio-g1": _mock_game("factorio-", "g1", 34197, True),
        "factorio-g2": _mock_game("factorio-", "g2", 34198, True),
    })
    driver_b = _mock_driver(games={
        "sat-g1": _mock_game("sat-", "g1", 34199, True),
    })
    result = _next_available_port(34197, [driver_a, driver_b])
    assert result == 34200


def test_all_ports_in_use_union():
    """
    _all_ports_in_use must return the union of all ports across all drivers.
    """
    driver_a = _mock_driver(games={
        "factorio-g1": _mock_game("factorio-", "g1", 34197, True),
    })
    driver_b = _mock_driver(games={
        "sat-g1": _mock_game("sat-", "g1", 7777, True),
    })
    result = _all_ports_in_use([driver_a, driver_b])
    assert result == {34197, 7777}


def test_all_ports_in_use_empty():
    """_all_ports_in_use must return an empty set when no drivers have games."""
    result = _all_ports_in_use([_mock_driver(), _mock_driver()])
    assert result == set()


# ─── NewServer mode switching ─────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_new_server_shows_name_input_when_no_save_picker(monkeypatch):
    """
    When driver.supports_save_picker() returns False, NewServer must render
    an Input with id='name_input' instead of a FilteredDirectoryTree.
    """
    from textual.app import App

    driver = _mock_driver(supports_save_picker=False, supports_player_count=False,
                          game_prefix="sat-", display_name="Satisfactory", base_port=7777)

    # Use a minimal App (not ControlServer) to avoid the CSS_PATH dependency.
    class _TestApp(App):
        CSS_PATH = None

        def compose(self):
            yield NewServer(driver=driver, all_drivers=[driver], id="newserver")

        def on_mount(self):
            pass  # no auto-refresh timer needed

    async with _TestApp().run_test() as pilot:
        name_input = pilot.app.query_one("#name_input", Input)
        assert name_input is not None
        # FilteredDirectoryTree must NOT be present
        trees = list(pilot.app.query(FilteredDirectoryTree))
        assert len(trees) == 0


@pytest.mark.asyncio
async def test_new_server_shows_directory_tree_for_save_picker(monkeypatch):
    """
    When driver.supports_save_picker() returns True, NewServer must render
    a FilteredDirectoryTree (not a name Input).
    """
    from textual.app import App

    driver = _mock_driver(supports_save_picker=True, game_prefix="factorio-",
                          display_name="Factorio", base_port=34197)

    class _TestApp(App):
        CSS_PATH = None

        def compose(self):
            yield NewServer(driver=driver, all_drivers=[driver], id="newserver")

        def on_mount(self):
            pass  # no auto-refresh timer needed

    async with _TestApp().run_test() as pilot:
        tree = pilot.app.query_one(FilteredDirectoryTree)
        assert tree is not None
        # name_input must NOT be present
        inputs = [i for i in pilot.app.query(Input) if i.id == "name_input"]
        assert len(inputs) == 0


# ─── Per-tab rebuild ──────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_per_tab_rebuild_calls_driver_rebuild(monkeypatch):
    """
    Clicking the rebuild button inside a GameTab must call
    driver.rebuild_and_recreate() on that tab's driver, not a global server.
    """
    driver = _patch_factorio_driver(monkeypatch)
    driver.rebuild_and_recreate.return_value = {"recreated": [], "restarted": []}

    async with ControlServer().run_test(size=(120, 60)) as pilot:
        await pilot.click("#rebuild_button")
        await pilot.pause(delay=0.1)

    driver.rebuild_and_recreate.assert_called_once()
