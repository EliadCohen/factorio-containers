"""
satisfactory_tab — Textual tab widget for the Satisfactory Dedicated Server.

Replaces the generic ``GameTab`` for the Satisfactory driver.  Instead of
managing one container per game, this tab talks to the Satisfactory HTTP API
to display the running session, list saves, and trigger game-management actions.

Layout (when the API is reachable):
    ┌──────────────────────────────────────────────────────┐
    │  Server: ● Online   Session: MyFactory  Players: 2   │
    │  ────────────────────────────────────────────────    │
    │  ▸ MyFactory   2026-03-22  3h 12m  [Load]            │
    │  ▸ OldBase     2026-03-10  8h 45m  [Load]            │
    │                                                      │
    │  [Save]  New game: [____________]  [Start]  [Rebuild] │
    └──────────────────────────────────────────────────────┘

When no API token is configured:
    ┌──────────────────────────────────────────────────────┐
    │  ⚠ No API token configured.                          │
    │  Run  server.GenerateAPIToken  in the server console │
    │  Paste token: [________________________]  [Save]     │
    └──────────────────────────────────────────────────────┘

When the server container is offline:
    │  Server: ○ Offline   [Start container]               │
"""
from __future__ import annotations

import datetime
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

    DEFAULT_CSS = """
    SatisfactoryTab { height: 1fr; }
    #sat-save-list { height: 1fr; }
    #sat-status-bar { height: auto; }
    #sat-actions { height: auto; }
    #sat-newgame-input { width: 20; }
    """

    class LoadSave(Message):
        """Posted by ``SaveRow`` when the user clicks [Load]."""
        def __init__(self, save_name: str) -> None:
            super().__init__()
            self.save_name = save_name

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

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _build_client(self) -> SatisfactoryAPIClient | None:
        token = load_token()
        if not token:
            return None
        port = 7777
        if self._game_driver:
            # Use the port of the running container if available
            games = list(self._game_driver.games.values()) if self._game_driver.games else []
            if games:
                port = games[0].game_port
        return SatisfactoryAPIClient(host="localhost", port=port, token=token)

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
        """Render the 'no token configured' setup screen."""
        with Vertical(id="sat-setup"):
            yield Label("⚠ No API token configured.", id="sat-setup-warn")
            yield Label(
                "Generate one in the server console:  server.GenerateAPIToken",
                id="sat-setup-hint",
            )
            with Horizontal(id="sat-setup-row"):
                yield Input(placeholder="Paste token here", id="sat-token-input")
                yield Button("Save token", id="sat-token-save", variant="primary")

    def _compose_main(self) -> ComposeResult:
        """Render the main server management UI."""
        yield Horizontal(id="sat-status-bar")
        yield Rule()
        with ScrollableContainer(id="sat-save-list"):
            pass  # populated by refresh()
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

    def poll(self) -> None:
        """Called by ``ControlServer.refresh_all()`` every 5 seconds."""
        if not load_token():
            return
        if self._client is None:
            self._client = self._build_client()
        self.run_worker(self._do_refresh, thread=True, exit_on_error=False)

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
            bar.mount(Label("● Online", id="sat-online-label"))
            bar.mount(Label(f"Session: {self._session or '—'}", id="sat-session-label"))
            bar.mount(Label(f"Players: {self._players}", id="sat-players-label"))
        else:
            bar.mount(Label("○ Offline", id="sat-offline-label"))
            running = self._container_running()
            if not running:
                btn = Button("Start container", id="sat-start-btn", variant="success")
                bar.mount(btn)

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

    @on(Button.Pressed, "#sat-token-save")
    def save_token_pressed(self, event: Button.Pressed) -> None:
        inp = self.query_one("#sat-token-input", Input)
        token = inp.value.strip()
        if not token:
            self.app.notify("Token cannot be empty", severity="error")
            return
        save_token(token)
        self.app.notify("Token saved. Reloading tab…", title="API token")
        # Remount this widget with the token now available
        self.remove()
        from satisfactory_tab import SatisfactoryTab
        parent = self.parent
        if parent is not None:
            parent.mount(SatisfactoryTab(driver=self._game_driver, all_drivers=self._all_drivers))

    @on(Button.Pressed, "#sat-start-btn")
    def start_container(self, event: Button.Pressed) -> None:
        """Start the Satisfactory container via the Podman driver."""
        if not self._game_driver:
            return
        try:
            games = list(self._game_driver.games.values())
            if games:
                games[0].start()
            self.app.notify("Starting Satisfactory container…", title="Container")
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
