#!/usr/bin/env python
"""
server_control — Textual TUI for managing Factorio headless server containers.

Entry point for the ``factainer`` CLI command.  Renders a terminal UI with:
  - A scrollable list of running/stopped servers (``ServerEntry`` widgets).
  - A "Rebuild & Recreate All" button (``UpdateSection``) that rebuilds the
    container image in a background thread.
  - A directory-tree + port picker for launching new server instances
    (``NewServer``).

Consumed by:  ``pyproject.toml`` entry-point ``factainer = server_control:main``
Depends on:   ``factorio_server.FactorioServer`` for all Podman interactions.
"""
from typing import Iterable
from pathlib import Path
from textual.widgets import Input, Switch, Label, DirectoryTree, Button, Header, Footer, Rule
from textual.containers import ScrollableContainer, HorizontalGroup
from textual.app import App
from textual.containers import ItemGrid
from textual import on
from factorio_server import FactorioServer
from textual.reactive import reactive
from textual.css import query


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
    One row in the server list representing a single Factorio container.

    Displays: server name, port, online/offline status, player count,
    a toggle switch for start/stop, and a Delete button.

    Reactive attributes are used so that ``ControlServer.refresh_server_list``
    can update individual fields without remounting the widget:
      - ``game_name`` (str): Full container name including ``"factorio-"`` prefix.
      - ``game_port`` (str/int): UDP game port.
      - ``game_active`` (bool): Whether the container is running.
      - ``player_count`` (int): Connected player count; ``-1`` means unknown.
    """
    game_name = reactive("game_name")
    game_port = reactive("0")
    game_active: bool = reactive(False, init=False)
    player_count = reactive(-1)

    def __init__(self, game_name, game_port, game_active, player_count=-1, **kwargs):
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
            **kwargs: Forwarded to ``HorizontalGroup.__init__`` (e.g. ``id``).
        """
        super().__init__(**kwargs)
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

        Returns:
            str: Stringified count when ``>= 0``, or ``"-"`` when the count
                is ``-1`` (unknown / server offline).
        """
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

        Yields: name label, port label, status label, player count label,
        on/off Switch, Delete button — all in a single uniform grid row.
        """
        with ItemGrid(min_column_width=10, regular=True, id="server_grid"):
            yield Label(self.game_name.removeprefix("factorio-"), id="server_name")
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
            if event.value:
                self.app.server.games[self.game_name].start()
                self.game_active = event.value
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

        Calls ``server.rebuild_and_recreate()``, notifies the user of the
        outcome, and refreshes the server list.

        Uses ``call_from_thread`` to schedule ``refresh_server_list`` on the
        Textual event loop rather than calling it directly.  Directly
        manipulating TUI state from a non-event-loop thread is unsafe in
        Textual — ``call_from_thread`` queues the call and executes it on the
        next iteration of the event loop.
        """
        try:
            result = self.app.server.rebuild_and_recreate()
            self.app.notify(
                f"Done: {len(result['recreated'])} recreated, {len(result['restarted'])} started.",
                title="Update",
            )
            # call_from_thread schedules the TUI update on the event loop;
            # calling refresh_server_list() directly from this thread would
            # be a data race.
            self.app.call_from_thread(self.app.refresh_server_list)
        except Exception as e:
            self.app.notify(f"Rebuild failed: {e}", title="Update", severity="error")


class NewServer(HorizontalGroup):
    """
    Section for creating a new Factorio server instance.

    Contains a filtered directory tree to select a save file, a port input
    field (pre-populated with the next available port), and a "Run server"
    button.
    """
    BASE_PORT = 34197  # Default Factorio multiplayer port; we start scanning here

    def _next_available_port(self) -> int:
        """
        Find the lowest port >= ``BASE_PORT`` not already in use.

        Scans ``self.app.server.games`` for occupied ports and increments
        from ``BASE_PORT`` until a free slot is found.

        Returns:
            int: First unoccupied port >= ``BASE_PORT``.
        """
        in_use = {g.game_port for g in self.app.server.games.values()}
        port = self.BASE_PORT
        while port in in_use:
            port += 1
        return port

    def compose(self):
        """
        Build the new-server section widgets.

        Yields: directory tree, selected-file label, port input, run button.
        """
        yield FilteredDirectoryTree("./saves/", id="filetree")
        yield Label("Select file", id="file_selection")
        yield Input(placeholder="port number to serve on", id="port_selection")
        yield Button("Run server", id="run_button")
        return super().compose()

    def on_mount(self):
        """
        Populate the port input with the next available port after mounting.

        Refreshes ``server.games`` first so the port calculation reflects any
        servers started since the TUI launched.
        """
        self.app.server.update_game_list()
        self.query_one("#port_selection", Input).value = str(self._next_available_port())

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
        self.query_one("#port_selection", Input).value = str(self._next_available_port())

    @on(Button.Pressed, "#run_button")
    def new_server(self, event: Button.Pressed):
        """
        Handle the "Run server" button: validate port and create the container.

        Derives the server name from the selected filename (without ``.zip``),
        validates that the chosen port is not already occupied, then delegates
        to ``FactorioServer.create_game``.

        Args:
            event: The button-pressed event (unused beyond selector matching).
        """
        name = self.query_one("#file_selection").renderable.removesuffix(".zip")
        port = int(self.query_one("#port_selection").value)
        in_use = {g.game_port for g in self.app.server.games.values()}
        if port in in_use:
            self.app.notify(f"Port {port} is already in use", severity="error")
            return
        FactorioServer.create_game(self.app.server, name=name, port=port, savefile=name)
        self.app.refresh_server_list()


class ControlServer(App):
    """
    Root Textual application — the Factorio Server Manager TUI.

    Composes ``ServerEntry`` widgets for each known container, the
    ``UpdateSection``, and the ``NewServer`` section.  Registers a 5-second
    auto-refresh timer so the server list stays current without user input.

    Key bindings:
      q — quit the application.
      r — manually refresh the server list immediately.

    Class attributes:
        CSS_PATH (str): Path to the Textual CSS stylesheet.
        BINDINGS (list): Key binding declarations consumed by Textual.
        server (FactorioServer): Shared application-level Podman manager.
            Defined at class scope so all widgets can reach it via
            ``self.app.server``.
    """
    CSS_PATH = "./control_server.css"
    BINDINGS = [("q", "quit", "Quit"),
                ("r", "refresh", "Refresh server list"),
                ]

    def action_quit(self):
        """Quit the Textual application."""
        return super().action_quit()

    def action_refresh(self):
        """Manually refresh the server list (bound to ``r``)."""
        self.refresh_server_list()

    # Class-level shared FactorioServer instance.  All widgets reach Podman
    # through self.app.server — there is exactly one connection per process.
    server = FactorioServer()

    def on_mount(self):
        """
        Start the 5-second auto-refresh timer after the TUI mounts.

        The timer calls ``refresh_server_list`` every 5 seconds so container
        state changes (e.g. a crash, external start/stop) are reflected
        without user interaction.
        """
        # 5-second interval balances freshness against RCON query overhead.
        # Each refresh issues one inspect() + one RCON call per running server.
        self.set_interval(5, self.refresh_server_list)

    def refresh_server_list(self):
        """
        Synchronise the TUI server list with the live Podman state.

        Algorithm:
          1. Refresh ``server.games`` from Podman.
          2. Remove ``ServerEntry`` widgets whose container no longer exists.
          3. For each existing container, update the matching widget's reactive
             fields or mount a new ``ServerEntry`` if one doesn't exist yet.

        Player count is fetched only for running containers (calling RCON on a
        stopped container would always fail).
        """
        self.server.update_game_list()
        live_names = set(self.server.games.keys())
        # Remove widgets for containers that were deleted since the last refresh
        for entry in self.query(ServerEntry):
            if entry.game_name not in live_names:
                entry.remove()
        scrollable_container = self.query_one("#server_container")
        for game in self.server.games.values():
            try:
                entry = self.query_one(f"#server-{game.game_name}")
                if entry:
                    # Update existing widget's reactive fields in-place
                    entry.game_name = game.game_name
                    entry.game_port = game.game_port
                    entry.game_active = game.active_status
                    pc = game.player_count() if game.active_status else None
                    entry.player_count = pc if pc is not None else -1
            except query.NoMatches:
                # New container appeared since last refresh — create a widget
                pc = game.player_count() if game.active_status else None
                entry = ServerEntry(
                    game_name=game.game_name,
                    game_port=game.game_port,
                    game_active=game.active_status,
                    player_count=pc if pc is not None else -1,
                    id=f"server-{game.game_name}",
                )
                scrollable_container.mount(entry)

    def compose(self):
        """
        Build the initial TUI layout.

        Renders one ``ServerEntry`` per known container, then the update
        section, a divider, and the new-server section.  Header and Footer
        are yielded last so Textual layers them correctly.
        """
        self.server.update_game_list()
        with ScrollableContainer(id="server_container"):
            for game in self.server.games.values():
                pc = game.player_count() if game.active_status else None
                yield ServerEntry(
                    game_name=game.game_name,
                    game_port=game.game_port,
                    game_active=game.active_status,
                    player_count=pc if pc is not None else -1,
                    id=f"server-{game.game_name}",
                )
        yield Rule()
        yield UpdateSection(id="updatesection")
        yield Rule()
        yield NewServer(id="newserver")
        yield Header()
        yield Footer()


def main():
    ControlServer().run()


if __name__ == "__main__":
    main()
