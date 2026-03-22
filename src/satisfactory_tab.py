"""
satisfactory_tab — Textual tab widget for the Satisfactory Dedicated Server.

Replaces the generic ``GameTab`` for the Satisfactory driver.  The tab handles
the full lifecycle — from first-time server setup through ongoing game
management — without ever requiring the user to drop to a shell.

Setup flow (no saved token)
---------------------------
On first launch the tab probes the server and shows the appropriate form:

  * **Server offline** — start-container button + retry.
  * **Server unclaimed** (fresh install) — name + optional admin password →
    claims the server and generates a long-lived token automatically.
  * **Server claimed, no admin password** — one-click token generation.
  * **Server claimed with password** — password input → login → generate token.

Main UI (token saved and valid)
---------------------------------
::

    ┌──────────────────────────────────────────────────────┐
    │  Server: ● Online   Session: MyFactory  Players: 2   │
    │  ─────────────────────────────────────────────────   │
    │  ▸ MyFactory   2026-03-22  3h 12m  [Load]            │
    │  ▸ OldBase     2026-03-10  8h 45m  [Load]            │
    │                                                      │
    │  [Save]  New game: [____________]  [Start]  [Rebuild] │
    └──────────────────────────────────────────────────────┘
"""
from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import ScrollableContainer, Horizontal, Vertical
from textual.message import Message
from textual.widget import Widget
from textual.widgets import Button, Input, Label, Rule
from textual import on
from textual.reactive import reactive

from satisfactory_api import SatisfactoryAPIClient, load_token, save_token


def _fmt_duration(seconds: int) -> str:
    """Format *seconds* as ``Xh Ym``."""
    h, m = divmod(seconds // 60, 60)
    return f"{h}h {m:02d}m"


def _fmt_date(iso: str) -> str:
    """Format an ISO 8601 datetime string as ``YYYY-MM-DD``."""
    try:
        return iso[:10]
    except Exception:
        return iso


class SaveRow(Widget):
    """One row in the save list: name | date | duration | [Load] button."""

    def __init__(self, save: dict, **kwargs):
        super().__init__(**kwargs)
        self._save = save

    def compose(self) -> ComposeResult:
        with Horizontal():
            yield Label(self._save["saveName"], classes="save-name")
            yield Label(_fmt_date(self._save["saveDateTime"]), classes="save-date")
            yield Label(_fmt_duration(self._save["playDurationSeconds"]), classes="save-duration")
            yield Button("Load", classes="load-btn", id=f"load-{self._save['saveName']}")

    @on(Button.Pressed)
    def load_pressed(self, event: Button.Pressed) -> None:
        self.post_message(SatisfactoryTab.LoadSave(self._save["saveName"]))


class SatisfactoryTab(Widget):
    """
    Full tab widget for the Satisfactory Dedicated Server.

    Uses the Satisfactory HTTP API (``SatisfactoryAPIClient``) for game
    management and the ``driver`` (``SatisfactoryServer``) for container
    lifecycle (start, rebuild).
    """

    class LoadSave(Message):
        """Posted by ``SaveRow`` when the user clicks [Load]."""
        def __init__(self, save_name: str) -> None:
            super().__init__()
            self.save_name = save_name

    DEFAULT_CSS = """
    SatisfactoryTab { height: 1fr; }
    #sat-save-list { height: 1fr; }
    #sat-status-bar { height: auto; }
    #sat-actions { height: auto; }
    #sat-newgame-input { width: 20; }
    #sat-setup { padding: 1 2; height: auto; }
    #sat-setup-form { height: auto; margin-top: 1; }
    """

    _online: bool = reactive(False, init=False)
    _session: str = reactive("", init=False)
    _players: int = reactive(0, init=False)
    _saves: list = reactive([], init=False)
    _token_configured: bool = reactive(False, init=False)

    def __init__(self, driver=None, all_drivers=None, **kwargs):
        super().__init__(**kwargs)
        self._game_driver = driver
        self._all_drivers = all_drivers or []
        self._client: SatisfactoryAPIClient | None = None
        # Carries state between the setup worker and UI callbacks.
        self._setup_state: dict = {}

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _get_port(self) -> int:
        """Return the game port from the driver, or fall back to 7777."""
        if self._game_driver:
            games = list(self._game_driver.games.values()) if self._game_driver.games else []
            if games:
                return games[0].game_port
        return 7777

    def _build_client(self) -> SatisfactoryAPIClient | None:
        token = load_token()
        if not token:
            return None
        return SatisfactoryAPIClient(host="localhost", port=self._get_port(), token=token)

    def _container_running(self) -> bool:
        """Return True if the Satisfactory container is running."""
        if not self._game_driver:
            return False
        try:
            self._game_driver.update_game_list()
            return any(g.active_status for g in self._game_driver.games.values())
        except Exception:
            return False

    # ── Compose ───────────────────────────────────────────────────────────────

    def compose(self) -> ComposeResult:
        token = load_token()
        if not token:
            yield from self._compose_setup()
            return
        self._token_configured = True
        yield from self._compose_main()

    def _compose_setup(self) -> ComposeResult:
        """Initial setup screen — content is filled in by the background worker."""
        with Vertical(id="sat-setup"):
            yield Label("Checking server status…", id="sat-setup-status")
            yield Vertical(id="sat-setup-form")

    def _compose_main(self) -> ComposeResult:
        """Render the main server management UI."""
        yield Horizontal(id="sat-status-bar")
        yield Rule()
        with ScrollableContainer(id="sat-save-list"):
            pass  # populated by _do_refresh
        yield Rule()
        with Horizontal(id="sat-actions"):
            yield Button("Save", id="sat-save-btn", variant="success")
            yield Label("New game:", id="sat-newgame-label")
            yield Input(placeholder="session name", id="sat-newgame-input")
            yield Button("Start", id="sat-newgame-btn", variant="primary")
            yield Button("Rebuild", id="sat-rebuild-btn", variant="error")

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    def on_mount(self) -> None:
        if self._token_configured:
            self._client = self._build_client()
            self.run_worker(self._do_refresh, thread=True, exit_on_error=False)
        else:
            self.run_worker(self._do_setup_check, thread=True, exit_on_error=False)

    def poll(self) -> None:
        """Called by ``ControlServer.refresh_all()`` every 5 seconds."""
        if not load_token():
            return
        if self._client is None:
            self._client = self._build_client()
        self.run_worker(self._do_refresh, thread=True, exit_on_error=False)

    # ── Setup worker + UI callbacks ───────────────────────────────────────────

    def _do_setup_check(self) -> None:
        """
        Background worker: probe the server and decide which setup form to show.

        Possible outcomes:
        - Server unreachable → show offline form
        - Passwordless login returns InitialAdmin token → unclaimed → claim form
        - Passwordless login returns Administrator token → claimed, no password → one-click
        - Passwordless login fails → needs admin password → password form

        Claimed vs unclaimed is detected by decoding the privilege level from
        the returned token: InitialAdmin = unclaimed, Administrator = claimed.
        """
        client = SatisfactoryAPIClient(host="localhost", port=self._get_port())
        if not client.health_check():
            self.app.call_from_thread(self._show_setup_offline)
            return

        try:
            initial_token = client.passwordless_login()
            self._setup_state["initial_token"] = initial_token
            level = SatisfactoryAPIClient.decode_privilege_level(initial_token)
            if level == "InitialAdmin":
                # Server is unclaimed (fresh install).
                self.app.call_from_thread(self._show_setup_unclaimed)
            else:
                # Server is claimed but has no admin password.
                self.app.call_from_thread(self._show_setup_no_password)
        except Exception:
            # Passwordless login failed — admin password is set.
            self.app.call_from_thread(self._show_setup_need_password)

    def _update_setup_status(self, text: str) -> None:
        try:
            self.query_one("#sat-setup-status", Label).update(text)
        except Exception:
            pass

    def _replace_setup_form(self, *widgets) -> None:
        """Replace the contents of #sat-setup-form with *widgets*."""
        try:
            form = self.query_one("#sat-setup-form", Vertical)
            form.remove_children()
            for w in widgets:
                form.mount(w)
        except Exception:
            pass

    def _show_setup_offline(self) -> None:
        self._update_setup_status("○ Server not reachable at localhost:7777")
        self._setup_state["mode"] = "offline"
        widgets = []
        if self._game_driver:
            widgets.append(Button("Start container", classes="sat-start-btn", variant="success"))
        widgets.append(Button("Retry", classes="sat-setup-retry", variant="default"))
        self._replace_setup_form(*widgets)

    def _show_setup_unclaimed(self) -> None:
        self._update_setup_status("● Server online — not yet claimed")
        self._setup_state["mode"] = "claim"
        self._replace_setup_form(
            Label("Server name:"),
            Input(placeholder="My Factory", id="sat-claim-name"),
            Label("Admin password (leave blank for none):"),
            Input(placeholder="(optional)", password=True, id="sat-claim-password"),
            Button("Claim server & generate token", classes="sat-setup-submit", variant="primary"),
        )

    def _show_setup_no_password(self) -> None:
        self._update_setup_status("● Server online — no admin password set")
        self._setup_state["mode"] = "generate"
        self._replace_setup_form(
            Label("Server has no admin password — click to generate a token:"),
            Button("Generate & save API token", classes="sat-setup-submit", variant="primary"),
        )

    def _show_setup_need_password(self) -> None:
        self._update_setup_status("● Server online — enter admin password to generate token")
        self._setup_state["mode"] = "password"
        self._replace_setup_form(
            Label("Admin password:"),
            Input(placeholder="Admin password", password=True, id="sat-auth-password"),
            Button("Login & generate token", classes="sat-setup-submit", variant="primary"),
        )

    # ── Setup button handlers ─────────────────────────────────────────────────

    @on(Button.Pressed, ".sat-setup-retry")
    def setup_retry(self, event: Button.Pressed) -> None:
        """Re-run the server probe."""
        self._update_setup_status("Checking server status…")
        self._replace_setup_form()
        self.run_worker(self._do_setup_check, thread=True, exit_on_error=False)

    @on(Button.Pressed, ".sat-setup-submit")
    def setup_submit(self, event: Button.Pressed) -> None:
        """Dispatch to the correct worker based on the current setup mode."""
        mode = self._setup_state.get("mode")
        if mode == "claim":
            self._start_claim()
        elif mode == "generate":
            token = self._setup_state.get("initial_token", "")
            self.run_worker(
                lambda: self._do_generate_token(token),
                thread=True, exit_on_error=False,
            )
        elif mode == "password":
            self._start_password_login()

    def _start_claim(self) -> None:
        try:
            name = self.query_one("#sat-claim-name", Input).value.strip()
            password = self.query_one("#sat-claim-password", Input).value.strip()
        except Exception:
            return
        if not name:
            self.app.notify("Enter a server name", severity="error")
            return
        token = self._setup_state.get("initial_token", "")
        self.run_worker(
            lambda: self._do_claim(name, password, token),
            thread=True, exit_on_error=False,
        )

    def _start_password_login(self) -> None:
        try:
            password = self.query_one("#sat-auth-password", Input).value.strip()
        except Exception:
            return
        self.run_worker(
            lambda: self._do_password_login(password),
            thread=True, exit_on_error=False,
        )

    # ── Setup background workers ──────────────────────────────────────────────

    def _do_claim(self, server_name: str, admin_password: str, auth_token: str) -> None:
        try:
            client = SatisfactoryAPIClient(host="localhost", port=self._get_port())
            new_token = client.claim_server(server_name, admin_password, auth_token)
            api_token = client.generate_api_token(new_token)
            save_token(api_token)
            self.app.call_from_thread(self._finish_setup)
        except Exception as e:
            self.app.notify(f"Claim failed: {e}", severity="error", title="Setup error")

    def _do_generate_token(self, auth_token: str) -> None:
        try:
            client = SatisfactoryAPIClient(host="localhost", port=self._get_port())
            api_token = client.generate_api_token(auth_token)
            save_token(api_token)
            self.app.call_from_thread(self._finish_setup)
        except Exception as e:
            self.app.notify(f"Token generation failed: {e}", severity="error", title="Setup error")

    def _do_password_login(self, password: str) -> None:
        try:
            client = SatisfactoryAPIClient(host="localhost", port=self._get_port())
            auth_token = client.password_login(password)
            api_token = client.generate_api_token(auth_token)
            save_token(api_token)
            self.app.call_from_thread(self._finish_setup)
        except Exception as e:
            self.app.notify(f"Login failed: {e}", severity="error", title="Setup error")

    def _finish_setup(self) -> None:
        """Token saved — remount this widget as the main UI."""
        self.app.notify("Token saved! Connecting to server…", title="Setup complete")
        parent = self.parent
        self.remove()
        if parent is not None:
            parent.mount(SatisfactoryTab(
                driver=self._game_driver,
                all_drivers=self._all_drivers,
                id=self.id,
            ))

    # ── Main refresh worker ───────────────────────────────────────────────────

    def _do_refresh(self) -> None:
        """Background worker: query API and update reactive state."""
        if self._client is None:
            self.app.call_from_thread(self._set_offline)
            return
        online = self._client.health_check()
        if not online:
            self.app.call_from_thread(self._set_offline)
            return
        try:
            state = self._client.query_server_state()
            saves = self._client.enumerate_sessions()
            self.app.call_from_thread(self._update_state, state, saves)
        except Exception as e:
            self.app.notify(f"Satisfactory API error: {e}", severity="warning")

    def _set_offline(self) -> None:
        self._online = False
        self._session = ""
        self._players = 0
        self._saves = []
        self._render_status_bar()

    def _update_state(self, state: dict, saves: list) -> None:
        self._online = True
        self._session = state.get("activeSessionName", "")
        self._players = state.get("numConnectedPlayers", 0)
        self._saves = saves
        self._render_status_bar()
        self._render_save_list()

    # ── UI updaters ───────────────────────────────────────────────────────────

    def _render_status_bar(self) -> None:
        """Rebuild the status bar content to reflect current state."""
        try:
            bar = self.query_one("#sat-status-bar", Horizontal)
        except Exception:
            return
        bar.remove_children()
        if self._online:
            bar.mount(Label("● Online", classes="sat-online-label"))
            bar.mount(Label(f"Session: {self._session or '—'}", classes="sat-session-label"))
            bar.mount(Label(f"Players: {self._players}", classes="sat-players-label"))
        else:
            bar.mount(Label("○ Offline", classes="sat-offline-label"))
            running = self._container_running()
            if not running:
                bar.mount(Button("Start container", classes="sat-start-btn", variant="success"))

    def _render_save_list(self) -> None:
        """Rebuild the scrollable save list from ``self._saves``."""
        try:
            container = self.query_one("#sat-save-list", ScrollableContainer)
        except Exception:
            return
        container.remove_children()
        for save in self._saves:
            container.mount(SaveRow(save, id=f"saverow-{save['saveName']}"))

    # ── Event handlers ────────────────────────────────────────────────────────

    @on(Button.Pressed, ".sat-start-btn")
    def start_container(self, event: Button.Pressed) -> None:
        """Start (or create-and-start) the Satisfactory container via the Podman driver."""
        if not self._game_driver:
            return
        self.run_worker(self._do_start_container, thread=True, exit_on_error=False)

    def _do_start_container(self) -> None:
        try:
            self._game_driver.update_game_list()
            games = list(self._game_driver.games.values())
            if games:
                games[0].start()
                self.app.notify("Starting Satisfactory container…", title="Container")
            else:
                # No container exists yet — create one with defaults.
                port = self._get_port()
                self._game_driver.create_game(name="server", port=port)
                self.app.notify(
                    "Satisfactory container created and started.",
                    title="Container",
                )
        except Exception as e:
            self.app.notify(f"Failed to start container: {e}", severity="error")

    def on_satisfactory_tab_load_save(self, event: "SatisfactoryTab.LoadSave") -> None:
        """Load the selected save file via the API (runs in background thread)."""
        if self._client is None:
            self.app.notify("API not connected", severity="error")
            return
        save_name = event.save_name
        self.app.notify(f"Loading '{save_name}'…", title="Load save")
        self.run_worker(
            lambda: self._client.load_game(save_name),
            thread=True,
            exit_on_error=False,
        )

    @on(Button.Pressed, "#sat-save-btn")
    def save_game(self, event: Button.Pressed) -> None:
        """Save the current session using the active session name."""
        if self._client is None:
            self.app.notify("API not connected", severity="error")
            return
        save_name = self._session or "autosave"
        self.run_worker(
            lambda: self._client.save_game(save_name),
            thread=True,
            exit_on_error=False,
        )
        self.app.notify(f"Saving as '{save_name}'…", title="Save")

    @on(Button.Pressed, "#sat-newgame-btn")
    def new_game(self, event: Button.Pressed) -> None:
        """Create a new game session."""
        if self._client is None:
            self.app.notify("API not connected", severity="error")
            return
        inp = self.query_one("#sat-newgame-input", Input)
        session_name = inp.value.strip()
        if not session_name:
            self.app.notify("Enter a session name", severity="error")
            return
        self.app.notify(f"Creating new game '{session_name}'…", title="New game")
        self.run_worker(
            lambda: self._client.create_new_game(session_name),
            thread=True,
            exit_on_error=False,
        )
        inp.value = ""

    @on(Button.Pressed, "#sat-rebuild-btn")
    def rebuild(self, event: Button.Pressed) -> None:
        """Rebuild the Satisfactory container image and recreate the container."""
        if not self._game_driver:
            return
        self.app.notify("Rebuilding Satisfactory image…", title="Rebuild")
        self.run_worker(self._do_rebuild, thread=True, exit_on_error=False)

    def _do_rebuild(self) -> None:
        try:
            result = self._game_driver.rebuild_and_recreate()
            recreated = len(result["recreated"])
            self.app.notify(
                f"Image built. {recreated} container{'s' if recreated != 1 else ''} recreated.",
                title="Rebuild complete",
            )
            self.app.call_from_thread(self.poll)
        except Exception as e:
            self.app.notify(f"Rebuild failed: {e}", severity="error", title="Rebuild")
