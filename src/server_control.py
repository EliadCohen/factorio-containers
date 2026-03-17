#!/usr/bin/env python
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
    def filter_paths(self, paths: Iterable[Path]) -> Iterable[Path]:
        return [path for path in paths if path.is_dir() or (path.suffix == ".zip" and not path.name.startswith("_"))]
    
class ServerEntry(HorizontalGroup):
    game_name = reactive("game_name")
    game_port = reactive("0")
    game_active:bool = reactive(False, init=False)
    
    def __init__(self, game_name, game_port, game_active, **kwargs):
        super().__init__(**kwargs)
        self.set_reactive(ServerEntry.game_name, game_name)
        self.set_reactive(ServerEntry.game_port, game_port)
        self.set_reactive(ServerEntry.game_active, game_active)
        
    def _status_markup(self) -> str:
        if self.game_active:
            return "[bold orange1]Online[/bold orange1]"
        return "[bold red]Offline[/bold red]"

    def watch_game_active(self):
        self.query_one("#server_active", Switch).value = self.game_active
        self.query_one("#server_status", Label).update(self._status_markup())

    def update_server_fields(self, name, port, active):
        self.game_name = name
        self.game_port = port
        self.game_active = active

    def compose(self):
        with ItemGrid(min_column_width=10, regular=True, id="server_grid"):
            yield Label(self.game_name.removeprefix("factorio-"), id="server_name")
            yield Label(f"{self.game_port}", id="server_port")
            yield Label(self._status_markup(), id="server_status", markup=True)
            yield Switch(value=self.game_active, id="server_active")
            yield Button("Delete", id="delete_button", variant="error")

    @on(Button.Pressed, "#delete_button")
    def delete_game(self, event: Button.Pressed):
        self.app.server.games[self.game_name].delete()
        self.app.refresh_server_list()

    @on(Switch.Changed, "#server_active")
    def toggle_game_state(self, event):
        if event.value != self.game_active:
            if event.value:
                self.app.server.games[self.game_name].start()
                self.game_active = event.value
            else:
                self.app.server.games[self.game_name].stop()
                self.game_active = event.value

class NewServer(HorizontalGroup):
    BASE_PORT = 34197

    def _next_available_port(self) -> int:
        in_use = {g.game_port for g in self.app.server.games.values()}
        port = self.BASE_PORT
        while port in in_use:
            port += 1
        return port

    def compose(self):
        yield FilteredDirectoryTree("./saves/", id="filetree")
        yield Label("Select file", id="file_selection")
        yield Input(placeholder="port number to serve on", id="port_selection")
        yield Button("Run server", id="run_button")
        return super().compose()

    def on_mount(self):
        self.app.server.update_game_list()
        self.query_one("#port_selection", Input).value = str(self._next_available_port())

    @on(FilteredDirectoryTree.FileSelected, "#filetree")
    def update_file_label(self, event: FilteredDirectoryTree.FileSelected):
        lbl = self.query_one("#file_selection")
        lbl.update(event.path.name if event.path.is_file() else "select file")
        self.filepath = event.path
        self.query_one("#port_selection", Input).value = str(self._next_available_port())

    @on(Button.Pressed, "#run_button")
    def new_server(self, event: Button.Pressed):
        name = self.query_one("#file_selection").renderable.removesuffix(".zip")
        port = int(self.query_one("#port_selection").value)
        in_use = {g.game_port for g in self.app.server.games.values()}
        if port in in_use:
            self.app.notify(f"Port {port} is already in use", severity="error")
            return
        FactorioServer.create_game(self.app.server, name=name, port=port, savefile=name)
        self.app.refresh_server_list()

class ControlServer(App):
    CSS_PATH="./control_server.css"
    BINDINGS = [("q", "quit", "Quit"),
                ("r", "refresh", "Refresh server list"),
                ]
    
    def action_quit(self):
        return super().action_quit()
    
    def action_refresh(self):
        self.refresh_server_list()

    server = FactorioServer()

    def on_mount(self):
        self.set_interval(5, self.refresh_server_list)

    def refresh_server_list(self):
        self.server.update_game_list()
        live_names = set(self.server.games.keys())
        for entry in self.query(ServerEntry):
            if entry.game_name not in live_names:
                entry.remove()
        scrollable_container = self.query_one("#server_container")
        for game in self.server.games.values():
            try:
                entry = self.query_one(f"#server-{game.game_name}")
                if entry:
                    entry.game_name = game.game_name
                    entry.game_port = game.game_port
                    entry.game_active = game.active_status
            except query.NoMatches:
                entry = ServerEntry(game_name=game.game_name, game_port=game.game_port, game_active=game.active_status, id=f"server-{game.game_name}")
                scrollable_container.mount(entry)

    def compose(self):
        self.server.update_game_list()
        with ScrollableContainer(id="server_container"):
            for game in self.server.games.values():
                yield ServerEntry(game_name=game.game_name, game_port=game.game_port, game_active=game.active_status, id=f"server-{game.game_name}")
        yield Rule()
        yield NewServer(id="newserver")
        yield Header()
        yield Footer()

def main():
    ControlServer().run()

if __name__ == "__main__":
    main()