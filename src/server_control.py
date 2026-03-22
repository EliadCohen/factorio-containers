#!/usr/bin/env python
"""
server_control — Textual TUI for managing game server containers.

Entry point for the ``factainer`` CLI command.  Renders a terminal UI with
one tab per game driver (Factorio, Satisfactory, …).  Each tab contains:
  - A scrollable list of running/stopped servers (``ServerEntry`` widgets).
  - A "Rebuild & Recreate All" button (``UpdateSection``).
  - A save/name picker and port picker for launching new server instances
    (``NewServer``).

Consumed by:  ``pyproject.toml`` entry-point ``factainer = server_control:main``
Depends on:   ``game_driver.GameDriver`` ABC; ``factorio_server.FactorioServer``
              implements it for Factorio containers.
"""
from typing import Iterable
from pathlib import Path
from textual.widgets import (
    Input, Switch, Label, DirectoryTree, Button, Header, Footer, Rule,
    TabbedContent, TabPane,
)
from textual.containers import ScrollableContainer, HorizontalGroup, Horizontal, Vertical
from textual.app import App
from textual.containers import ItemGrid
from textual.widget import Widget
from textual import on
from textual.css.query import NoMatches
from factorio_server import FactorioServer
from textual.reactive import reactive
from game_driver import GameDriver, _all_ports_in_use, _next_available_port

BASE_PORT = 34197  # Default Factorio multiplayer port; fallback when no driver


class FilteredDirectoryTree(DirectoryTree):
    """
    A ``DirectoryTree`` that shows only save-relevant paths.

    Filters the file tree so the user sees only:
      - Directories (needed to navigate into save subdirectories).
      - ``.zip`` files whose name does NOT start with ``"_"`` (Factorio names
        autosave files ``_autosave1.zip``, ``_autosave2.zip``, etc.).

    This keeps the tree clean — autosaves clutter the picker and cannot be
    used to start a new server with the same name as the directory.
    """

    def filter_paths(self, paths: Iterable[Path]) -> Iterable[Path]:
        """
        Filter tree paths to directories and non-autosave zip files.

        Args:
            paths: All child paths that Textual proposes to show.

        Returns:
            Subset of *paths* containing only directories and user-created
            ``.zip`` files (name does not start with ``"_"``).
        """
        return [path for path in paths if path.is_dir() or (path.suffix == ".zip" and not path.name.startswith("_"))]


class ServerEntry(HorizontalGroup):
    """
    One row in the server list representing a single game server container.

    Displays: server name, port, online/offline status, player count,
    a toggle switch for start/stop, and a Delete button.

    Reactive attributes allow ``GameTab.refresh_game_list`` to update
    individual fields without remounting the widget:
      - ``game_name`` (str): Full container name including the game prefix.
      - ``game_port`` (int): UDP game port.
      - ``game_active`` (bool): Whether the container is running.
      - ``player_count`` (int): Connected player count; ``-1`` means unknown.
    """
    game_name = reactive("")
    game_port = reactive(0)
    game_active: bool = reactive(False, init=False)
    player_count = reactive(-1)

    def __init__(self, game_name, game_port, game_active, player_count=-1, driver=None, **kwargs):
        """
        Initialise the widget with the server's current state.

        Uses ``set_reactive`` rather than direct assignment so the reactive
        values are populated before ``compose()`` runs (avoiding a flash of
        the default values on first render).

        Args:
            game_name (str): Full container name (e.g. ``"factorio-myworld"``).
            game_port (int): UDP game port.
            game_active (bool): ``True`` if the container is currently running.
            player_count (int): Connected players, or ``-1`` if unknown.
            driver (GameDriver | None): The game driver; used to derive the
                display name prefix and to reach the correct Podman containers.
            **kwargs: Forwarded to ``HorizontalGroup.__init__`` (e.g. ``id``).
        """
        super().__init__(**kwargs)
        self._driver = driver
        self.set_reactive(ServerEntry.game_name, game_name)
        self.set_reactive(ServerEntry.game_port, game_port)
        self.set_reactive(ServerEntry.game_active, game_active)
        self.set_reactive(ServerEntry.player_count, player_count)

    def _status_markup(self) -> str:
        """
        Rich markup string for the server status label.

        Returns:
            str: ``"[bold orange1]Online[/bold orange1]"`` when active,
                ``"[bold red]Offline[/bold red]"`` otherwise.
        """
        if self.game_active:
            return "[bold orange1]Online[/bold orange1]"
        return "[bold red]Offline[/bold red]"

    def _player_count_markup(self) -> str:
        """
        Display string for the player count label.

        Returns ``"-"`` when:
          - The driver does not support player count queries, OR
          - The count is ``-1`` (unknown / server offline).

        Returns:
            str: Stringified count when ``>= 0``, or ``"-"`` otherwise.
        """
        if self._driver and not self._driver.supports_player_count():
            return "-"
        if self.player_count < 0:
            return "-"
        return str(self.player_count)

    def watch_game_active(self):
        """
        Textual reactive watcher — called whenever ``game_active`` changes.

        Syncs the Switch widget value and updates the status label markup so
        the UI stays consistent with the reactive state without a full remount.
        """
        self.query_one("#server_active", Switch).value = self.game_active
        self.query_one("#server_status", Label).update(self._status_markup())

    def watch_player_count(self):
        """
        Textual reactive watcher — called whenever ``player_count`` changes.

        Updates the player count label text.
        """
        self.query_one("#player_count", Label).update(self._player_count_markup())

    def update_server_fields(self, name, port, active, player_count=-1):
        """
        Bulk-update all reactive fields for this server entry.

        Triggers the relevant ``watch_*`` callbacks automatically.

        Args:
            name (str): Full container name.
            port (int): UDP game port.
            active (bool): Running state.
            player_count (int): Connected players, or ``-1`` if unknown.
        """
        self.game_name = name
        self.game_port = port
        self.game_active = active
        self.player_count = player_count

    def compose(self):
        """
        Build the row's child widgets inside an ``ItemGrid``.

        Strips the game prefix from the display name using the driver's
        ``game_prefix`` if available, otherwise falls back to ``"factorio-"``.

        Yields: name label, port label, status label, player count label,
        on/off Switch, Delete button — all in a single uniform grid row.
        """
        if self._driver:
            display = self.game_name.removeprefix(self._driver.game_prefix)
        else:
            display = self.game_name.removeprefix("factorio-")
        with ItemGrid(min_column_width=10, regular=True, id="server_grid"):
            yield Label(display, id="server_name")
            yield Label(f"{self.game_port}", id="server_port")
            yield Label(self._status_markup(), id="server_status", markup=True)
            yield Label(self._player_count_markup(), id="player_count")
            yield Switch(value=self.game_active, id="server_active")
            yield Button("Delete", id="delete_button", variant="error")

    @on(Button.Pressed, "#delete_button")
    def delete_game(self, event: Button.Pressed):
        """
        Handle the Delete button: remove the container and refresh the list.

        Args:
            event: The button-pressed event (unused beyond selector matching).
        """
        if self._driver:
            self._driver.games[self.game_name].delete()
            self.ancestor(GameTab).refresh_game_list()
        else:
            # Fallback: reach driver via app (legacy path)
            self.app.server.games[self.game_name].delete()
            self.app.refresh_server_list()

    @on(Switch.Changed, "#server_active")
    def toggle_game_state(self, event):
        """
        Handle the on/off Switch: start or stop the container.

        Guards against spurious events triggered by reactive watchers by
        comparing the new switch value against the current reactive state.

        Args:
            event: The switch-changed event carrying the new boolean value.
        """
        if event.value != self.game_active:
            if self._driver:
                if event.value:
                    self._driver.games[self.game_name].start()
                else:
                    self._driver.games[self.game_name].stop()
            else:
                # Fallback: reach driver via app (legacy path)
                if event.value:
                    self.app.server.games[self.game_name].start()
                else:
                    self.app.server.games[self.game_name].stop()
            self.game_active = event.value


class UpdateSection(HorizontalGroup):
    """
    Section containing the "Rebuild & Recreate All" button.

    Triggers a full image rebuild and container recreation cycle when pressed.
    The operation runs in a background thread via Textual's worker system so
    the TUI remains responsive during the multi-minute build.
    """

    def __init__(self, driver=None, **kwargs):
        """
        Args:
            driver (GameDriver | None): The game driver to rebuild.  Falls back
                to ``self.app.server`` if not provided.
            **kwargs: Forwarded to ``HorizontalGroup.__init__``.
        """
        super().__init__(**kwargs)
        self._driver = driver

    def compose(self):
        """Yield the rebuild button."""
        yield Button("Rebuild & Recreate All", id="rebuild_button", variant="error")

    @on(Button.Pressed, "#rebuild_button")
    def rebuild(self, event: Button.Pressed):
        """
        Handle the rebuild button press.

        Notifies the user that the process has started, then offloads the
        actual work to ``_run_rebuild`` in a background thread (``thread=True``
        in ``run_worker``).  Using a thread is required because
        ``rebuild_and_recreate`` blocks on Podman API calls and the image
        build, which can take minutes.

        Args:
            event: The button-pressed event (unused beyond selector matching).
        """
        self.app.notify("Saving running games and rebuilding image...", title="Update")
        # thread=True: run _run_rebuild in a worker thread so the TUI event
        # loop is not blocked during the multi-step rebuild process.
        self.run_worker(self._run_rebuild, thread=True, exit_on_error=False)

    def _run_rebuild(self):
        """
        Execute the rebuild in a background thread.

        Calls ``driver.rebuild_and_recreate()`` (or ``app.server`` as
        fallback), notifies the user of the outcome, and refreshes the game
        list for this tab.

        Uses ``call_from_thread`` to schedule TUI mutations on the event loop
        rather than calling them directly.  Directly manipulating TUI state
        from a non-event-loop thread is unsafe in Textual.
        """
        try:
            if self._driver:
                result = self._driver.rebuild_and_recreate()
            else:
                result = self.app.server.rebuild_and_recreate()
            self.app.notify(
                f"Done: {len(result['recreated'])} recreated, {len(result['restarted'])} started.",
                title="Update",
            )
            # Schedule TUI refresh on the event loop.
            if self._driver:
                self.app.call_from_thread(self.ancestor(GameTab).refresh_game_list)
            else:
                self.app.call_from_thread(self.app.refresh_server_list)
        except Exception as e:
            self.app.notify(f"Rebuild failed: {e}", title="Update", severity="error")


class NewServer(HorizontalGroup):
    """
    Section for creating a new game server instance.

    When the driver supports save picking (``supports_save_picker() == True``),
    shows a filtered directory tree + file label; otherwise shows a plain text
    ``Input`` for the server name.

    Always shows a port input and a "Run server" button.
    """
    BASE_PORT = 34197  # Default Factorio multiplayer port; shown in the old tests

    def __init__(self, driver=None, all_drivers=None, **kwargs):
        """
        Args:
            driver (GameDriver | None): The game driver for this new-server
                section.
            all_drivers (list | None): All active drivers, used to avoid port
                conflicts across game types.
            **kwargs: Forwarded to ``HorizontalGroup.__init__``.
        """
        super().__init__(**kwargs)
        self._driver = driver
        self._all_drivers = all_drivers or []

    def compose(self):
        """
        Build the new-server section widgets.

        If the driver supports save selection, yields a directory tree and
        file-selection label.  Otherwise yields a name text input.
        Always yields: port input, run button.
        """
        if self._driver and not self._driver.supports_save_picker():
            yield Input(placeholder="server name", id="name_input")
        else:
            yield FilteredDirectoryTree("./saves/", id="filetree")
            yield Label("Select file", id="file_selection")
        yield Input(placeholder="port number to serve on", id="port_selection")
        yield Button("Run server", id="run_button")
        return super().compose()

    def on_mount(self):
        """
        Populate the port input with the next available port after mounting.

        Uses ``_next_available_port`` across all drivers so ports are unique
        even when multiple game types are active.
        """
        if self._driver:
            base = self._driver.base_port
        else:
            base = self.BASE_PORT
        suggested = _next_available_port(base, self._all_drivers)
        self.query_one("#port_selection", Input).value = str(suggested)

    @on(FilteredDirectoryTree.FileSelected, "#filetree")
    def update_file_label(self, event: FilteredDirectoryTree.FileSelected):
        """
        Update the file label and refresh the suggested port when a file is selected.

        Also recalculates the suggested port in case new servers were started
        while the user was browsing the tree.

        Args:
            event: ``FileSelected`` event carrying the chosen ``Path``.
        """
        lbl = self.query_one("#file_selection")
        lbl.update(event.path.name if event.path.is_file() else "select file")
        self.filepath = event.path
        if self._driver:
            base = self._driver.base_port
        else:
            base = self.BASE_PORT
        self.query_one("#port_selection", Input).value = str(
            _next_available_port(base, self._all_drivers)
        )

    @on(Button.Pressed, "#run_button")
    def new_server(self, event: Button.Pressed):
        """
        Handle the "Run server" button: validate port and create the container.

        For save-picker drivers, derives the name from the selected filename
        (without ``.zip``).  For non-save-picker drivers, reads the name from
        the ``#name_input`` text field.

        Validates that the chosen port is not already occupied by any driver,
        then delegates to ``driver.create_game()``.

        Args:
            event: The button-pressed event (unused beyond selector matching).
        """
        port_str = self.query_one("#port_selection", Input).value
        try:
            port = int(port_str)
        except ValueError:
            self.app.notify("Port must be a number", severity="error")
            return

        in_use = _all_ports_in_use(self._all_drivers)
        if port in in_use:
            self.app.notify(f"Port {port} is already in use", severity="error")
            return

        if self._driver and not self._driver.supports_save_picker():
            # Name-input mode: no save file needed
            name = self.query_one("#name_input", Input).value.strip()
            if not name:
                self.app.notify("Server name cannot be empty", severity="error")
                return
            self._driver.create_game(name=name, port=port)
        else:
            # Save-picker mode: derive name from selected file
            if self._driver:
                name = self.query_one("#file_selection").renderable.removesuffix(".zip")
                self._driver.create_game(name=name, port=port, savefile=name)
            else:
                # Legacy fallback (no driver provided)
                name = self.query_one("#file_selection").renderable.removesuffix(".zip")
                from factorio_server import FactorioServer
                FactorioServer.create_game(self.app.server, name=name, port=port, savefile=name)

        # Refresh: prefer GameTab, fall back to app-level refresh
        try:
            self.ancestor(GameTab).refresh_game_list()
        except Exception:
            self.app.refresh_server_list()


class GameTab(Widget):
    """
    A full tab panel for one game driver.

    Composes a ``ScrollableContainer`` of ``ServerEntry`` widgets, a rebuild
    section (``UpdateSection``), and a new-server section (``NewServer``).

    ``refresh_game_list()`` synchronises the server list with live Podman
    state without remounting the whole tab.
    """

    def __init__(self, driver: GameDriver, all_drivers: list, **kwargs):
        """
        Args:
            driver (GameDriver): The game driver this tab manages.
            all_drivers (list[GameDriver]): All active drivers (for port
                conflict checking in ``NewServer``).
            **kwargs: Forwarded to ``Widget.__init__``.
        """
        super().__init__(**kwargs)
        self._driver = driver
        self._all_drivers = all_drivers

    def compose(self):
        """
        Build the tab's child widgets.

        Queries live game list from the driver and yields one ``ServerEntry``
        per game, followed by the update and new-server sections.
        """
        prefix = self._driver.game_prefix.rstrip("-")
        self._driver.update_game_list()
        with Horizontal():
            with Vertical(id="left-panel"):
                with ScrollableContainer(id=f"server_container-{prefix}"):
                    for game in self._driver.games.values():
                        if self._driver.supports_player_count() and game.active_status:
                            pc = game.player_count()
                            count = pc if pc is not None else -1
                        else:
                            count = -1
                        yield ServerEntry(
                            game_name=game.game_name,
                            game_port=game.game_port,
                            game_active=game.active_status,
                            player_count=count,
                            driver=self._driver,
                            id=f"server-{game.game_name}",
                        )
                yield Rule()
                yield UpdateSection(driver=self._driver, id="updatesection")
            yield NewServer(driver=self._driver, all_drivers=self._all_drivers, id="newserver")

    def refresh_game_list(self):
        """
        Refresh this tab's server list from the live Podman state.

        Algorithm:
          1. Refresh ``driver.games`` from Podman.
          2. Remove ``ServerEntry`` widgets whose container no longer exists.
          3. For each existing container, update the matching widget's reactive
             fields or mount a new ``ServerEntry`` if one doesn't exist yet.

        Player count is fetched only for running containers (calling RCON on a
        stopped container would always fail).
        """
        prefix = self._driver.game_prefix.rstrip("-")
        container_id = f"server_container-{prefix}"
        self._driver.update_game_list()
        container = self.query_one(f"#{container_id}", ScrollableContainer)

        # Remove widgets for containers that disappeared since last refresh.
        for entry in list(container.query(ServerEntry)):
            if entry.game_name not in self._driver.games:
                entry.remove()

        # Update existing entries or mount new ones.
        for game_name, game in self._driver.games.items():
            try:
                entry = container.query_one(f"#server-{game_name}", ServerEntry)
                entry.game_active = game.active_status
                if self._driver.supports_player_count() and game.active_status:
                    pc = game.player_count()
                    entry.player_count = pc if pc is not None else -1
                else:
                    entry.player_count = -1
            except NoMatches:
                count = -1
                if self._driver.supports_player_count() and game.active_status:
                    pc = game.player_count()
                    count = pc if pc is not None else -1
                container.mount(
                    ServerEntry(
                        game_name=game_name,
                        game_port=game.game_port,
                        game_active=game.active_status,
                        player_count=count,
                        driver=self._driver,
                        id=f"server-{game_name}",
                    )
                )


class ControlServer(App):
    """
    Root Textual application — the Game Server Manager TUI.

    Composes one ``TabPane`` / ``GameTab`` per registered game driver.
    Registers a 5-second auto-refresh timer so the server list stays current.

    Key bindings:
      q — quit the application.
      r — manually refresh all tabs immediately.

    Class attributes:
        CSS_PATH (str): Path to the Textual CSS stylesheet.
        BINDINGS (list): Key binding declarations consumed by Textual.
        factorio_driver (FactorioServer): Shared Factorio driver instance.
            Defined at class scope so tests can monkeypatch it directly.
    """
    CSS_PATH = "./control_server.css"
    BINDINGS = [("q", "quit", "Quit"), ("r", "refresh", "Refresh")]

    # Class-level driver instances.  Tests monkeypatch ``factorio_driver``
    # directly (e.g. ``monkeypatch.setattr(ControlServer, "factorio_driver", mock)``).
    factorio_driver = FactorioServer()

    # Legacy alias kept so existing tests that reference ``ControlServer.server``
    # continue to work.  Both attributes point to the same object by default;
    # when tests monkeypatch one, they should also monkeypatch the other if
    # both are accessed.
    server = factorio_driver

    @property
    def all_drivers(self) -> list:
        """
        Return the list of all active game drivers.

        Attempts to import ``SatisfactoryServer`` at runtime so that:
          - The module is not required at import time (tests that don't have it
            will still work).
          - A monkeypatched ``_satisfactory_driver`` on the class is picked up.
        """
        drivers = [self.factorio_driver]
        try:
            from satisfactory_server import SatisfactoryServer  # type: ignore[import]
            if not hasattr(self.__class__, "_satisfactory_driver"):
                self.__class__._satisfactory_driver = SatisfactoryServer()
            drivers.append(self._satisfactory_driver)
        except ImportError:
            pass
        return drivers

    def compose(self):
        """
        Build the initial TUI layout with one tab per driver.

        Yields a ``TabbedContent`` containing one ``TabPane`` + ``GameTab``
        per active driver, then a ``Footer``.
        """
        with TabbedContent():
            for driver in self.all_drivers:
                with TabPane(driver.display_name):
                    yield GameTab(driver=driver, all_drivers=self.all_drivers)
        yield Footer()

    def on_mount(self):
        """Start the 5-second auto-refresh timer after the TUI mounts."""
        self.set_interval(5, self.refresh_all)

    def refresh_all(self):
        """Refresh every ``GameTab`` from its driver's live Podman state."""
        for tab in self.query(GameTab):
            tab.refresh_game_list()

    def action_refresh(self):
        """Manually refresh all tabs (bound to ``r``)."""
        self.refresh_all()

    def action_quit(self):
        """Quit the Textual application."""
        return super().action_quit()

    # ── Legacy compatibility ───────────────────────────────────────────────────

    def refresh_server_list(self):
        """
        Legacy method kept for backwards compatibility with existing tests.

        Delegates to the first ``GameTab``'s ``refresh_game_list()`` if one
        exists, otherwise falls back to directly refreshing via the driver.
        """
        tabs = list(self.query(GameTab))
        if tabs:
            tabs[0].refresh_game_list()
        else:
            # Last-resort: update the driver and do nothing to the UI
            self.factorio_driver.update_game_list()


def main():
    import sys
    if "--dev" in sys.argv:
        import subprocess
        sys.exit(subprocess.run(
            [sys.executable, "-m", "textual", "run", "--dev", "server_control:ControlServer"]
        ).returncode)
    ControlServer().run()


if __name__ == "__main__":
    main()
