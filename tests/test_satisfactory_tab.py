"""
Tests for satisfactory_tab.SatisfactoryTab and satisfactory_api.SatisfactoryAPIClient.

All tests run without a live Satisfactory server or Podman socket.
The API client and token file are mocked throughout.
"""
import pytest
from unittest.mock import MagicMock, patch, mock_open
from textual.app import App
from textual.widgets import Input

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


def test_decode_privilege_level_initial_admin():
    """InitialAdmin token (unclaimed server) must decode correctly."""
    # Real token from passwordless login on unclaimed server
    token = "ewoJInBsIjogIkluaXRpYWxBZG1pbiIKfQ==.sig"
    assert SatisfactoryAPIClient.decode_privilege_level(token) == "InitialAdmin"


def test_decode_privilege_level_administrator():
    """Administrator token (claimed server, no password) must decode correctly."""
    token = "ewoJInBsIjogIkFkbWluaXN0cmF0b3IiCn0=.sig"
    assert SatisfactoryAPIClient.decode_privilege_level(token) == "Administrator"


def test_decode_privilege_level_bad_token():
    """Malformed token must return empty string without raising."""
    assert SatisfactoryAPIClient.decode_privilege_level("notavalidtoken") == ""


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


# ─── Auth / setup flow API tests ──────────────────────────────────────────────

def _resp(status, body):
    """Return a mock requests.Response."""
    r = MagicMock()
    r.ok = status < 400
    r.status_code = status
    r.json.return_value = body
    r.text = str(body)
    return r


def test_client_passwordless_login_returns_token():
    client = SatisfactoryAPIClient(host="localhost", port=7777)  # no token
    with patch.object(client._session, "post",
                      return_value=_resp(200, {"data": {"authenticationToken": "tmp-tok"}})):
        assert client.passwordless_login() == "tmp-tok"


def test_client_passwordless_login_sends_no_auth_header():
    """PasswordlessLogin must suppress any Authorization header."""
    client = SatisfactoryAPIClient(host="localhost", port=7777, token="stored-tok")
    captured = {}
    def fake_post(url, **kwargs):
        captured["headers"] = kwargs.get("headers")
        return _resp(200, {"data": {"authenticationToken": "tmp"}})
    with patch.object(client._session, "post", side_effect=fake_post):
        client.passwordless_login()
    # auth_token="" → header value is None → requests drops it
    assert captured["headers"].get("Authorization") is None


def test_client_password_login_returns_token():
    client = SatisfactoryAPIClient(host="localhost", port=7777)
    with patch.object(client._session, "post",
                      return_value=_resp(200, {"data": {"authenticationToken": "auth-tok"}})):
        assert client.password_login("secret") == "auth-tok"


def test_client_password_login_sends_password_in_body():
    client = SatisfactoryAPIClient(host="localhost", port=7777)
    captured = {}
    def fake_post(url, **kwargs):
        captured["body"] = kwargs.get("json")
        return _resp(200, {"data": {"authenticationToken": "tok"}})
    with patch.object(client._session, "post", side_effect=fake_post):
        client.password_login("hunter2")
    assert captured["body"]["data"]["Password"] == "hunter2"


def test_client_claim_server_sends_name_and_password():
    client = SatisfactoryAPIClient(host="localhost", port=7777)
    captured = {}
    def fake_post(url, **kwargs):
        captured["body"] = kwargs.get("json")
        captured["headers"] = kwargs.get("headers") or {}
        return _resp(200, {"data": {"authenticationToken": "new-tok"}})
    with patch.object(client._session, "post", side_effect=fake_post):
        result = client.claim_server("MyFactory", "pw123", "initial-tok")
    assert result == "new-tok"
    assert captured["body"]["data"]["serverName"] == "MyFactory"
    assert captured["body"]["data"]["adminPassword"] == "pw123"
    assert "initial-tok" in captured["headers"].get("Authorization", "")


def test_client_generate_api_token_returns_token():
    """generate_api_token must parse the token out of the RunCommand response."""
    client = SatisfactoryAPIClient(host="localhost", port=7777)
    cmd_result = "New Server API Authentication Token: long-lived-tok\n"
    with patch.object(client._session, "post",
                      return_value=_resp(200, {"data": {"commandResult": cmd_result,
                                                        "returnValue": True}})):
        assert client.generate_api_token("auth-tok") == "long-lived-tok"


def test_client_generate_api_token_uses_run_command():
    """generate_api_token must call RunCommand with server.GenerateAPIToken."""
    client = SatisfactoryAPIClient(host="localhost", port=7777, token="stored-tok")
    captured = {}
    def fake_post(url, **kwargs):
        captured["body"] = kwargs.get("json")
        captured["headers"] = kwargs.get("headers") or {}
        return _resp(200, {"data": {"commandResult": "New Server API Authentication Token: tok\n",
                                    "returnValue": True}})
    with patch.object(client._session, "post", side_effect=fake_post):
        client.generate_api_token("temp-tok")
    assert captured["body"]["function"] == "RunCommand"
    assert captured["body"]["data"]["Command"] == "server.GenerateAPIToken"
    assert "temp-tok" in captured["headers"].get("Authorization", "")


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
    with patch("satisfactory_tab.load_token", return_value=None), \
         patch.object(SatisfactoryTab, "_do_setup_check", lambda self: None):
        async with _TabApp().run_test() as pilot:
            status = pilot.app.query_one("#sat-setup-status")
            assert status is not None


@pytest.mark.asyncio
async def test_tab_shows_main_ui_when_token_present():
    """When a token is available the main UI (status bar + actions) must render."""
    with patch("satisfactory_tab.load_token", return_value="tok"), \
         patch("satisfactory_tab.SatisfactoryAPIClient") as MockClient:
        MockClient.return_value.health_check.return_value = False
        async with _TabApp().run_test() as pilot:
            assert pilot.app.query_one("#sat-status-bar") is not None
            assert pilot.app.query_one("#sat-actions") is not None


# ─── Setup flow TUI tests ─────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_setup_shows_offline_form_when_server_unreachable():
    """When the server is unreachable the setup form must show a Retry button."""
    with patch("satisfactory_tab.load_token", return_value=None), \
         patch("satisfactory_tab.SatisfactoryAPIClient") as MockClient:
        MockClient.return_value.health_check.return_value = False

        async with _TabApp().run_test() as pilot:
            await pilot.pause(delay=0.3)  # let _do_setup_check worker finish
            retry_btn = pilot.app.query_one("#sat-setup-retry")
            assert retry_btn is not None


@pytest.mark.asyncio
async def test_setup_shows_claim_form_when_unclaimed():
    """An unclaimed server (InitialAdmin token) must show the claim form."""
    # InitialAdmin token (base64 of {"pl": "InitialAdmin"})
    initial_admin_token = "ewoJInBsIjogIkluaXRpYWxBZG1pbiIKfQ==.sig"
    with patch("satisfactory_tab.load_token", return_value=None), \
         patch("satisfactory_tab.SatisfactoryAPIClient") as MockClient:
        MockClient.return_value.health_check.return_value = True
        MockClient.return_value.passwordless_login.return_value = initial_admin_token
        MockClient.decode_privilege_level = SatisfactoryAPIClient.decode_privilege_level

        async with _TabApp().run_test() as pilot:
            await pilot.pause(delay=0.3)
            name_input = pilot.app.query_one("#sat-claim-name")
            assert name_input is not None


@pytest.mark.asyncio
async def test_setup_shows_generate_button_when_no_password():
    """A claimed server with no admin password (Administrator token) shows one-click generation."""
    # Administrator token (base64 of {"pl": "Administrator"})
    admin_token = "ewoJInBsIjogIkFkbWluaXN0cmF0b3IiCn0=.sig"
    with patch("satisfactory_tab.load_token", return_value=None), \
         patch("satisfactory_tab.SatisfactoryAPIClient") as MockClient:
        MockClient.return_value.health_check.return_value = True
        MockClient.return_value.passwordless_login.return_value = admin_token
        MockClient.decode_privilege_level = SatisfactoryAPIClient.decode_privilege_level

        async with _TabApp().run_test() as pilot:
            await pilot.pause(delay=0.3)
            submit = pilot.app.query_one("#sat-setup-submit")
            assert submit is not None
            # Claim form must NOT be shown
            assert len(list(pilot.app.query("#sat-claim-name"))) == 0


@pytest.mark.asyncio
async def test_setup_shows_password_form_when_server_has_password():
    """A server with an admin password must show the password input form."""
    from satisfactory_api import SatisfactoryAPIError
    with patch("satisfactory_tab.load_token", return_value=None), \
         patch("satisfactory_tab.SatisfactoryAPIClient") as MockClient:
        MockClient.return_value.health_check.return_value = True
        MockClient.return_value.passwordless_login.side_effect = SatisfactoryAPIError("401")

        async with _TabApp().run_test() as pilot:
            await pilot.pause(delay=0.3)
            pw_input = pilot.app.query_one("#sat-auth-password")
            assert pw_input is not None


@pytest.mark.asyncio
async def test_claim_flow_saves_token_and_reloads():
    """Submitting the claim form must call claim_server + generate_api_token and save."""
    initial_admin_token = "ewoJInBsIjogIkluaXRpYWxBZG1pbiIKfQ==.sig"
    with patch("satisfactory_tab.load_token", return_value=None), \
         patch("satisfactory_tab.SatisfactoryAPIClient") as MockClient, \
         patch("satisfactory_tab.save_token") as mock_save, \
         patch.object(SatisfactoryTab, "_finish_setup", lambda self: None):
        inst = MockClient.return_value
        inst.health_check.return_value = True
        inst.passwordless_login.return_value = initial_admin_token
        inst.claim_server.return_value = "claimed-tok"
        inst.generate_api_token.return_value = "api-tok"
        MockClient.decode_privilege_level = SatisfactoryAPIClient.decode_privilege_level

        async with _TabApp().run_test(size=(120, 40)) as pilot:
            await pilot.pause(delay=0.3)  # let setup check finish

            # Fill in and submit the claim form
            name_inp = pilot.app.query_one("#sat-claim-name", Input)
            name_inp.value = "MyFactory"
            await pilot.click("#sat-setup-submit")
            await pilot.pause(delay=0.3)

    mock_save.assert_called_once_with("api-tok")


@pytest.mark.asyncio
async def test_password_login_flow_saves_token():
    """Submitting a password generates and saves an API token."""
    from satisfactory_api import SatisfactoryAPIError
    with patch("satisfactory_tab.load_token", return_value=None), \
         patch("satisfactory_tab.SatisfactoryAPIClient") as MockClient, \
         patch("satisfactory_tab.save_token") as mock_save, \
         patch.object(SatisfactoryTab, "_finish_setup", lambda self: None):
        inst = MockClient.return_value
        inst.health_check.return_value = True
        inst.get_server_name.return_value = "MyServer"
        inst.passwordless_login.side_effect = SatisfactoryAPIError("401")
        inst.password_login.return_value = "auth-tok"
        inst.generate_api_token.return_value = "api-tok"

        async with _TabApp().run_test(size=(120, 40)) as pilot:
            await pilot.pause(delay=0.3)

            pw_inp = pilot.app.query_one("#sat-auth-password", Input)
            pw_inp.value = "hunter2"
            await pilot.click("#sat-setup-submit")
            await pilot.pause(delay=0.3)

    mock_save.assert_called_once_with("api-tok")


# ─── Main UI action tests ─────────────────────────────────────────────────────

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
