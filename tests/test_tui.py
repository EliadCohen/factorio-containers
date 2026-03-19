"""
TUI tests using Textual's async pilot.

Each test replaces ControlServer.server with a MagicMock so no real
podman socket is needed.  Tests cover widget composition, reactive
state updates, and button interactions.
"""
import pytest
from pathlib import Path
from unittest.mock import MagicMock, patch

# server_control imports FactorioServer and executes ControlServer.server = FactorioServer()
# at class-definition time.  The podman stub in conftest.py ensures that works.
from server_control import (
    ControlServer,
    FilteredDirectoryTree,
    NewServer,
    ServerEntry,
    UpdateSection,
)


# ─── helpers ──────────────────────────────────────────────────────────────────

def _mock_server(games=None):
    """Return a MagicMock FactorioServer with an optional games dict."""
    s = MagicMock()
    s.games = games or {}
    return s


def _mock_game(name, port, running):
    g = MagicMock()
    g.game_name = f"factorio-{name}"
    g.game_port = port
    g.active_status = running
    return g


# ─── FilteredDirectoryTree ────────────────────────────────────────────────────

class TestFilteredDirectoryTree:
    """
    filter_paths is a pure function on paths; we fake Path objects so no
    real filesystem is needed.
    """

    def _make_path(self, name, is_dir=False):
        p = MagicMock(spec=Path)
        p.name = name
        p.suffix = Path(name).suffix if not is_dir else ""
        p.is_dir.return_value = is_dir
        return p

    def _filter(self, paths):
        tree = object.__new__(FilteredDirectoryTree)
        return list(tree.filter_paths(paths))

    def test_accepts_directories(self):
        d = self._make_path("saves", is_dir=True)
        assert d in self._filter([d])

    def test_accepts_zip_files(self):
        z = self._make_path("world.zip")
        assert z in self._filter([z])

    def test_rejects_autosave_zip_files(self):
        a = self._make_path("_autosave1.zip")
        assert a not in self._filter([a])

    def test_rejects_non_zip_files(self):
        txt = self._make_path("readme.txt")
        assert txt not in self._filter([txt])

    def test_mixed_list_filtered_correctly(self):
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
    _status_markup only reads self.game_active; call it as an unbound function
    on a plain object to avoid Textual's reactive machinery.
    """

    def _markup(self, active: bool) -> str:
        class _Stub:
            pass
        stub = _Stub()
        stub.game_active = active
        return ServerEntry._status_markup(stub)

    def test_online_markup_when_active(self):
        assert "Online" in self._markup(True)

    def test_offline_markup_when_inactive(self):
        assert "Offline" in self._markup(False)

    def test_online_is_not_offline(self):
        assert self._markup(True) != self._markup(False)


# ─── TUI composition ──────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_app_renders_with_no_games(monkeypatch):
    monkeypatch.setattr(ControlServer, "server", _mock_server())
    async with ControlServer().run_test() as pilot:
        # Basic smoke-test: app mounted without exceptions
        assert pilot.app is not None


@pytest.mark.asyncio
async def test_app_has_server_container(monkeypatch):
    monkeypatch.setattr(ControlServer, "server", _mock_server())
    async with ControlServer().run_test() as pilot:
        container = pilot.app.query_one("#server_container")
        assert container is not None


@pytest.mark.asyncio
async def test_app_has_update_section(monkeypatch):
    monkeypatch.setattr(ControlServer, "server", _mock_server())
    async with ControlServer().run_test() as pilot:
        section = pilot.app.query_one("#updatesection")
        assert section is not None


@pytest.mark.asyncio
async def test_app_has_rebuild_button(monkeypatch):
    monkeypatch.setattr(ControlServer, "server", _mock_server())
    async with ControlServer().run_test() as pilot:
        btn = pilot.app.query_one("#rebuild_button")
        assert btn is not None


@pytest.mark.asyncio
async def test_app_has_new_server_section(monkeypatch):
    monkeypatch.setattr(ControlServer, "server", _mock_server())
    async with ControlServer().run_test() as pilot:
        section = pilot.app.query_one("#newserver")
        assert section is not None


@pytest.mark.asyncio
async def test_server_entries_rendered_for_existing_games(monkeypatch):
    games = {
        "factorio-world1": _mock_game("world1", 34200, True),
        "factorio-world2": _mock_game("world2", 34201, False),
    }
    monkeypatch.setattr(ControlServer, "server", _mock_server(games))
    async with ControlServer().run_test() as pilot:
        entries = pilot.app.query(ServerEntry)
        assert len(list(entries)) == 2


@pytest.mark.asyncio
async def test_server_entry_shows_correct_port(monkeypatch):
    games = {"factorio-mymap": _mock_game("mymap", 34197, False)}
    monkeypatch.setattr(ControlServer, "server", _mock_server(games))
    async with ControlServer().run_test() as pilot:
        port_label = pilot.app.query_one("#server-factorio-mymap #server_port")
        assert "34197" in str(port_label.renderable)


@pytest.mark.asyncio
async def test_server_entry_shows_name_without_prefix(monkeypatch):
    games = {"factorio-mymap": _mock_game("mymap", 34197, False)}
    monkeypatch.setattr(ControlServer, "server", _mock_server(games))
    async with ControlServer().run_test() as pilot:
        name_label = pilot.app.query_one("#server-factorio-mymap #server_name")
        rendered = str(name_label.renderable)
        assert "mymap" in rendered
        assert "factorio-" not in rendered


# ─── Rebuild button ───────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_rebuild_button_triggers_rebuild_and_recreate(monkeypatch):
    server = _mock_server()
    server.rebuild_and_recreate.return_value = {"recreated": [], "restarted": []}
    monkeypatch.setattr(ControlServer, "server", server)

    # Use a tall terminal so UpdateSection (below the server list) is in view.
    async with ControlServer().run_test(size=(120, 60)) as pilot:
        await pilot.click("#rebuild_button")
        await pilot.pause(delay=0.1)

    server.rebuild_and_recreate.assert_called_once()


# ─── NewServer port picker ────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_new_server_port_defaults_to_base_port(monkeypatch):
    monkeypatch.setattr(ControlServer, "server", _mock_server())
    async with ControlServer().run_test() as pilot:
        port_input = pilot.app.query_one("#port_selection")
        assert port_input.value == str(NewServer.BASE_PORT)


@pytest.mark.asyncio
async def test_new_server_port_skips_in_use_ports(monkeypatch):
    base = NewServer.BASE_PORT
    games = {f"factorio-g{i}": _mock_game(f"g{i}", base + i, True) for i in range(3)}
    monkeypatch.setattr(ControlServer, "server", _mock_server(games))
    async with ControlServer().run_test() as pilot:
        port_input = pilot.app.query_one("#port_selection")
        assert int(port_input.value) == base + 3


# ─── refresh_server_list ──────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_refresh_removes_deleted_game_entries(monkeypatch):
    games = {"factorio-gone": _mock_game("gone", 34200, False)}
    server = _mock_server(games)
    monkeypatch.setattr(ControlServer, "server", server)

    async with ControlServer().run_test() as pilot:
        # Simulate the game disappearing
        server.games = {}
        pilot.app.refresh_server_list()
        await pilot.pause()

        entries = list(pilot.app.query(ServerEntry))
        assert len(entries) == 0


@pytest.mark.asyncio
async def test_refresh_adds_new_game_entries(monkeypatch):
    server = _mock_server()
    monkeypatch.setattr(ControlServer, "server", server)

    async with ControlServer().run_test() as pilot:
        new_game = _mock_game("newworld", 34200, True)
        server.games = {"factorio-newworld": new_game}
        pilot.app.refresh_server_list()
        await pilot.pause()

        entries = list(pilot.app.query(ServerEntry))
        assert len(entries) == 1
