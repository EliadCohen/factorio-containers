import json
import os
import shutil
from podman import PodmanClient

# uri = "unix:///run/user/0/podman/podman.sock"
# PORT=34200
# SAVE="monomono"
# ADMINLIST = "./data/server-adminlist.json"

class FactorioGame():
    uri = "unix:///run/user/0/podman/podman.sock"
    PORT=34200
    SAVE="testo"
    ADMINLIST = "./data/server-adminlist.json"
    IMAGE = "localhost/factorio-headless:latest"

    TEMPLATE_PATH = os.path.join(os.path.dirname(__file__), "../Container/server-settings.json")

    def __init__(self, name:str = SAVE, savefile:str = SAVE, port:int = PORT, adminlist:str = ADMINLIST, **kwargs):
        # Command is an array if you want to include args
        self.command = f"""./bin/x64/factorio --start-server ./saves/{savefile}.zip --server-settings ./saves/server-settings.json --server-adminlist {adminlist} --port {port}""".split()
        self.name = name
        self.game_name = "factorio-" + name
        self.port = port
        self.adminlist = adminlist
        self.savefile = savefile

    FACTORIO_UID = 1001

    def _prepare_server_settings(self, saves_path: str):
        settings_path = os.path.join(saves_path, "server-settings.json")
        if not os.path.exists(settings_path):
            shutil.copy(os.path.abspath(self.TEMPLATE_PATH), settings_path)
        with open(settings_path, "r") as f:
            settings = json.load(f)
        settings["name"] = self.name
        with open(settings_path, "w") as f:
            json.dump(settings, f, indent=2)

    def create_game(self):
        saves_path = f"/root/projects/factorio-container/saves/{self.savefile}/"
        self._prepare_server_settings(saves_path)
        for dirpath, dirnames, filenames in os.walk(saves_path):
            os.chown(dirpath, self.FACTORIO_UID, self.FACTORIO_UID)
            for filename in filenames:
                os.chown(os.path.join(dirpath, filename), self.FACTORIO_UID, self.FACTORIO_UID)
        with PodmanClient(base_url=self.uri) as client:         
            self.game_container = client.containers.run(
                image=self.IMAGE,
                network_mode="host",
                labels={"factorio.port": str(self.port)},
                detach=True,
                mounts=[
                    {
                        "type": "bind",
                        "source": f"/root/projects/factorio-container/saves/{self.savefile}/",
                        "target": "/home/factorio/factorio/saves/",
                        "read_only": False,
                        "relabel": "Z",
                    }
                ],
                command=self.command,
                name=self.game_name
            )
            result = {
                "name": self.game_container.attrs["Name"],
                "id": self.game_container.attrs["Id"],
                "running": self.game_container.attrs["State"]["Running"],
                "port": self.port,
            }
            return result
