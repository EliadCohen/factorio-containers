import time
import podman
from pathlib import Path
from podman import PodmanClient
from factorio_container import FactorioGame
from image_build import FactorioContainerImage
from leanrcon import send_command

class FactorioServer():
    GAME_PREFIX = "factorio-"
    class Game():
        GAME_PREFIX = "factorio-"
        def __init__(self, container, container_id, game_name, game_port, game_path, labels=None):
            self._container = container
            self.container_id = container_id
            self.game_name = game_name
            self.game_port = int(game_port)
            self.savepath = game_path
            labels = labels or {}
            self.rcon_port = int(labels.get("factorio.rcon-port", 0))
            self.rcon_password = labels.get("factorio.rcon-password", "")

        @property
        def display_name(self) -> str:
            return self.game_name.removeprefix(self.GAME_PREFIX)

        def __str__(self):
            return self.game_name

        @property
        def active_status(self) -> bool:
            inspection = self._container.inspect()
            return inspection["State"]["Running"]

        def start(self):
            self._container.start()

        def stop(self):
            self._container.stop()

        def delete(self):
            try:
                self._container.stop()
            except Exception:
                pass
            try:
                self._container.remove()
            except Exception as ex:
                print(ex)

    def __init__(self):
        self._client = PodmanClient(uri = "unix:///run/user/0/podman/podman.sock")
        self.games = {}

    def start_game(self, game:Game):
        game.start()

    def stop_game(self, game:Game):
        game.stop()

    def delete_game(self, game:Game):
        game.delete()

    def _list_games(self) -> list[Game]:
        conts = [con for con in (self._client.containers.list(all=True)) if con.name.startswith(self.GAME_PREFIX)]
        games = {}
        for con in conts:
            con_data = con.inspect()
            labels = con_data["Config"]["Labels"]
            port = int(labels.get("factorio.port", 0))
            games[con_data["Name"]] = self.Game(
                container=con,
                container_id=con_data["Id"],
                game_name=con_data["Name"],
                game_port=port,
                game_path=con_data["Mounts"][0]["Source"],
                labels=labels,
            )
        return games

    def update_game_list(self):
        self.games = self._list_games()

    def _rcon_save(self, game) -> bool:
        """Send /server-save via RCON. Returns True on success, False if not configured or failed."""
        if not game.rcon_port or not game.rcon_password:
            return False
        try:
            send_command("127.0.0.1", game.rcon_port, game.rcon_password, "/server-save")
            time.sleep(2)   # allow save to flush to disk
            return True
        except Exception:
            return False

    def create_game(self, name: str, port: int, savefile: str, force_name: bool = False):
        if force_name:
            unique_name = name
        else:
            existing = set(self.games.keys())
            unique_name = name
            suffix = 2
            while (self.GAME_PREFIX + unique_name) in existing:
                unique_name = f"{name}-{suffix}"
                suffix += 1
        new_game = FactorioGame(name=unique_name, savefile=savefile, port=port)
        return new_game.create_game()

    def rebuild_and_recreate(self) -> dict:
        self.update_game_list()

        # Snapshot all containers
        snapshots = [{
            "name": game.display_name,
            "port": game.game_port,
            "savefile": Path(game.savepath).name,
            "was_running": game.active_status,
            "rcon_saved": False,
        } for game in self.games.values()]

        # Gracefully save running games via RCON
        for snap, game in zip(snapshots, list(self.games.values())):
            if snap["was_running"]:
                snap["rcon_saved"] = self._rcon_save(game)

        # Rebuild the image
        FactorioContainerImage().build()

        # Stop + delete all containers
        for game in list(self.games.values()):
            game.delete()

        # Recreate all containers (containers.run() auto-starts them)
        recreated = []
        for snap in snapshots:
            self.create_game(name=snap["name"], port=snap["port"],
                             savefile=snap["savefile"], force_name=True)
            recreated.append(snap["name"])

        # Stop those that were not running before
        self.update_game_list()
        restarted = []
        for snap in snapshots:
            full_name = self.GAME_PREFIX + snap["name"]
            if full_name not in self.games:
                continue
            if snap["was_running"]:
                restarted.append(snap["name"])
            else:
                self.games[full_name].stop()

        return {"recreated": recreated, "restarted": restarted}


if __name__ == "__main__":
    server_obj = FactorioServer()
    server_obj.update_game_list()
    print("Hello")
