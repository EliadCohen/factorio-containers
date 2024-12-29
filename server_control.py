from typing import Iterable
from pathlib import Path

from textual.widgets import Input, Switch, Label, Static, DirectoryTree, Button
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
    game_active = reactive(False)

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

    server = FactorioServer()

    def refresh_games(self):
        # self.server = FactorioServer()
        self.server.update_game_list()

    def compose(self):
        self.refresh_games()
        with ScrollableContainer(id="server_container"):
            for game in self.server.games:
                entry = ServerEntry(id=f"server-{game.game_name}")
                entry.game_name = game.game_name
                entry.game_port = game.game_port
                entry.game_active = game.active_status
                yield entry
                # server = self.query_one(f"#server-{game.game_name}")
                # server.update_server_fields(game.game_name, game.game_port, game.active_status)
        yield NewServer(id="newserver")

if __name__ == "__main__":
    ControlServer().run()