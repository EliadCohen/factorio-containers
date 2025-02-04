from typing import Iterable
from pathlib import Path

from textual.widgets import Input, Switch, Label, Static, DirectoryTree, Button, Header, Footer, Rule
from textual.containers import ScrollableContainer, HorizontalGroup
from textual.app import App
from textual.containers import ItemGrid
from textual import on
from factorio_server import FactorioServer
from textual.reactive import reactive

class FilteredDirectoryTree(DirectoryTree):
    def filter_paths(self, paths: Iterable[Path]) -> Iterable[Path]:
        return [path for path in paths if not path.name.startswith("_")]
    

class ServerEntry(Static):
    game_name = reactive("game_name")
    game_port = reactive("0")
    game_active:bool = reactive(False, init=False)
    
    def __init__(self, game_name, game_port, game_active, **kwargs):
        super().__init__(**kwargs)
        self.set_reactive(ServerEntry.game_name, game_name)
        self.set_reactive(ServerEntry.game_port, game_port)
        self.set_reactive(ServerEntry.game_active, game_active)
        
    def watch_game_active(self):
        switch = self.query_one("#server_active")
        switch.value = self.game_active

    def update_server_fields(self, name, port, active):
        self.game_name = name
        self.game_port = port
        self.game_active = active

    def compose(self):
        with ItemGrid(min_column_width=20, regular=True, id="server_grid"):
            yield Label(f"{self.game_name}", id="server_name")
            yield Label(f"{self.game_port}", id="server_port")
            yield Label(f"{self.game_active}", id="server_status")
            yield Switch(value=self.game_active, id="server_active")
        return super().compose()
    
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
    def compose(self):
        # add filter to DirectoryTree, remove anything not a zip file and zip files starting with an underscore
        yield FilteredDirectoryTree("./saves/", id="filetree")
        yield Label("Select file", id="file_selection")
        yield Input(placeholder="port number to serve on", id="port_selection")
        yield Button("Run server", id="run_button")
        return super().compose()
    
    @on(FilteredDirectoryTree.FileSelected, "#filetree")
    def update_file_label(self, event: FilteredDirectoryTree.FileSelected):
        # print("HI")
        lbl = self.query_one("#file_selection")
        lbl.update(event.path.name if event.path.is_file() else "select file")
        self.filepath = event.path
        # print("Hello")

    @on(Button.Pressed, "#run_button")
    def new_server(self, event: Button.Pressed):
        # Use the factorio_server.FactorioServer.create()
        name = self.query_one("#file_selection").renderable.rstrip(".zip")
        port = int(self.query_one("#port_selection").value)
        # savefile = self.filepath.name
        result = FactorioServer.create_game(self.app.server, name=name, port=port, savefile=name)



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

    def refresh_games(self):
        # self.server = FactorioServer()
        self.server.update_game_list()

    def refresh_server_list(self):
        scrollable_container = self.query_one("#server_container")
        self.refresh_games()
        for game in self.server.games.values():
            entry = self.query_one(f"#server-{game.game_name}")
            if entry:
                entry.game_name = game.game_name
                entry.game_port = game.game_port
                entry.game_active = game.active_status
            else:
                entry = ServerEntry(game_name=game.game_name, game_port=game.game_port, game_active=game.active_status, id=f"server-{game.game_name}")
                # entry.game_name = game.game_name
                # entry.game_port = game.game_port
                # entry.game_active = game.active_status
                scrollable_container.mount(entry)

    def compose(self):
        self.refresh_games()
        with ScrollableContainer(id="server_container"):
            for game in self.server.games.values():
                # entry = ServerEntry(id=f"server-{game.game_name}")
                # entry.game_name = game.game_name
                # entry.game_port = game.game_port
                # entry.game_active = game.active_status
                yield ServerEntry(game_name=game.game_name, game_port=game.game_port, game_active=game.active_status, id=f"server-{game.game_name}")

                # server = self.query_one(f"#server-{game.game_name}")
                # server.update_server_fields(game.game_name, game.game_port, game.active_status)
        yield Rule()
        yield NewServer(id="newserver")
        yield Header()
        yield Footer()

if __name__ == "__main__":
    ControlServer().run()