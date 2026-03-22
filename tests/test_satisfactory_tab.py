"""
Tests for satisfactory_tab.SatisfactoryTab and satisfactory_api.SatisfactoryAPIClient.

All tests run without a live Satisfactory server or Podman socket.
The API client and token file are mocked throughout.
"""
import pytest
from unittest.mock import MagicMock, patch, mock_open
from textual.app import App

from satisfactory_api import SatisfactoryAPIClient, load_token, save_token
from satisfactory_tab import SatisfactoryTab, SaveRow


# ─── API client unit tests ────────────────────────────────────────────────────

def _make_client(token="test-token"):
    return SatisfactoryAPIClient(host="localhost", port=7777, token=token)


def test_client_health_check_success():
    client = _make_client()
    with patch.object(client._session, "post") as mock_post:
        mock_post.return_value.status_code = 200
        assert client.health_check() is True


def test_client_health_check_failure():
    client = _make_client()
    with patch.object(client._session, "post", side_effect=Exception("refused")):
        assert client.health_check() is False


def test_client_query_server_state():
    client = _make_client()
    resp = MagicMock()
    resp.ok = True
    resp.status_code = 200
    resp.json.return_value = {"data": {"serverGameState": {
        "activeSessionName": "MyFactory",
        "numConnectedPlayers": 2,
        "isGameRunning": True,
    }}}
    with patch.object(client._session, "post", return_value=resp):
        state = client.query_server_state()
    assert state["activeSessionName"] == "MyFactory"
    assert state["numConnectedPlayers"] == 2


def test_client_enumerate_sessions_flattens_saves():
    client = _make_client()
    resp = MagicMock()
    resp.ok = True
    resp.status_code = 200
    resp.json.return_value = {"data": {"sessions": [
        {
            "sessionName": "MyFactory",
            "saveHeaders": [
                {"saveName": "MyFactory", "saveDateTime": "2026-03-22T10:00:00Z", "playDurationSeconds": 3600},
                {"saveName": "MyFactory_auto", "saveDateTime": "2026-03-21T10:00:00Z", "playDurationSeconds": 3000},
            ],
        },
        {
            "sessionName": "OldBase",
            "saveHeaders": [
                {"saveName": "OldBase", "saveDateTime": "2026-03-10T08:00:00Z", "playDurationSeconds": 7200},
            ],
        },
    ]}}
    with patch.object(client._session, "post", return_value=resp):
        saves = client.enumerate_sessions()
    assert len(saves) == 3
    # Sorted newest first
    assert saves[0]["saveName"] == "MyFactory"
    assert saves[0]["sessionName"] == "MyFactory"
    assert saves[2]["saveName"] == "OldBase"


def test_client_load_game_posts_correct_body():
    client = _make_client()
    resp = MagicMock()
    resp.ok = True
    resp.status_code = 204
    with patch.object(client._session, "post", return_value=resp) as mock_post:
        client.load_game("MyFactory")
    call_kwargs = mock_post.call_args
    body = call_kwargs.kwargs["json"]
    assert body["data"]["saveName"] == "MyFactory"
    assert body["data"]["enableAdvancedGameSettings"] is False


def test_client_save_game_posts_correct_body():
    client = _make_client()
    resp = MagicMock()
    resp.ok = True
    resp.status_code = 204
    with patch.object(client._session, "post", return_value=resp) as mock_post:
        client.save_game("MySave")
    body = mock_post.call_args.kwargs["json"]
    assert body["data"]["saveName"] == "MySave"


def test_client_create_new_game_posts_correct_body():
    client = _make_client()
    resp = MagicMock()
    resp.ok = True
    resp.status_code = 204
    with patch.object(client._session, "post", return_value=resp) as mock_post:
        client.create_new_game("NewSession")
    body = mock_post.call_args.kwargs["json"]
    assert body["data"]["newGameData"]["sessionName"] == "NewSession"


# ─── Token helpers ────────────────────────────────────────────────────────────

def test_load_token_returns_none_when_missing():
    with patch("builtins.open", side_effect=FileNotFoundError):
        assert load_token() is None


def test_load_token_returns_stripped_value():
    with patch("builtins.open", mock_open(read_data="  my-token  \n")):
        assert load_token() == "my-token"


def test_save_token_writes_stripped():
    m = mock_open()
    with patch("builtins.open", m):
        save_token("  abc123  ")
    m().write.assert_called_once_with("abc123")


# ─── SatisfactoryTab TUI tests ────────────────────────────────────────────────

def _make_driver():
    d = MagicMock()
    d.games = {}
    d.display_name = "Satisfactory"
    d.rebuild_and_recreate.return_value = {"recreated": [], "restarted": []}
    return d


class _TabApp(App):
    """Minimal app that hosts a SatisfactoryTab for testing."""
    CSS_PATH = None

    def __init__(self, driver=None):
        super().__init__()
        self._sat_driver = driver or _make_driver()

    def compose(self):
        yield SatisfactoryTab(driver=self._sat_driver, all_drivers=[], id="sat-tab")


@pytest.mark.asyncio
async def test_tab_shows_setup_when_no_token():
    """When no token file exists the tab must render the setup UI."""
    with patch("satisfactory_tab.load_token", return_value=None):
        async with _TabApp().run_test() as pilot:
            warn = pilot.app.query_one("#sat-setup-warn")
            assert "No API token" in str(warn.renderable)


@pytest.mark.asyncio
async def test_tab_shows_main_ui_when_token_present():
    """When a token is available the main UI (status bar + actions) must render."""
    with patch("satisfactory_tab.load_token", return_value="tok"), \
         patch("satisfactory_tab.SatisfactoryAPIClient") as MockClient:
        MockClient.return_value.health_check.return_value = False
        async with _TabApp().run_test() as pilot:
            assert pilot.app.query_one("#sat-status-bar") is not None
            assert pilot.app.query_one("#sat-actions") is not None


@pytest.mark.asyncio
async def test_save_button_calls_api_save():
    """Pressing [Save] must call client.save_game() with the active session name."""
    mock_client = MagicMock()
    mock_client.health_check.return_value = True
    mock_client.query_server_state.return_value = {
        "activeSessionName": "MyFactory",
        "numConnectedPlayers": 1,
        "isGameRunning": True,
    }
    mock_client.enumerate_sessions.return_value = []

    with patch("satisfactory_tab.load_token", return_value="tok"), \
         patch("satisfactory_tab.SatisfactoryAPIClient", return_value=mock_client):
        async with _TabApp().run_test(size=(120, 40)) as pilot:
            tab = pilot.app.query_one(SatisfactoryTab)
            tab._client = mock_client
            tab._session = "MyFactory"
            await pilot.click("#sat-save-btn")
            await pilot.pause(delay=0.2)

    mock_client.save_game.assert_called_once_with("MyFactory")


@pytest.mark.asyncio
async def test_new_game_button_calls_api_create():
    """Pressing [Start] with a session name must call client.create_new_game()."""
    mock_client = MagicMock()

    with patch("satisfactory_tab.load_token", return_value="tok"), \
         patch("satisfactory_tab.SatisfactoryAPIClient", return_value=mock_client), \
         patch.object(SatisfactoryTab, "_do_refresh", lambda self: None):
        async with _TabApp().run_test(size=(120, 40)) as pilot:
            tab = pilot.app.query_one(SatisfactoryTab)
            tab._client = mock_client
            inp = pilot.app.query_one("#sat-newgame-input")
            await pilot.click(inp)
            for ch in "TestWorld":
                await pilot.press(ch)
            await pilot.click("#sat-newgame-btn")
            await pilot.pause(delay=0.2)

    mock_client.create_new_game.assert_called_once_with("TestWorld")



@pytest.mark.asyncio
async def test_rebuild_button_calls_driver_rebuild():
    """Pressing [Rebuild] must call driver.rebuild_and_recreate()."""
    driver = _make_driver()

    with patch("satisfactory_tab.load_token", return_value="tok"), \
         patch("satisfactory_tab.SatisfactoryAPIClient"), \
         patch.object(SatisfactoryTab, "_do_refresh", lambda self: None):

        class _App(App):
            CSS_PATH = None
            def compose(self):
                yield SatisfactoryTab(driver=driver, all_drivers=[], id="sat-tab")

        async with _App().run_test(size=(120, 40)) as pilot:
            await pilot.click("#sat-rebuild-btn")
            await pilot.pause(delay=0.2)

    driver.rebuild_and_recreate.assert_called_once()
