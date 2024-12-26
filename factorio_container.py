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
    IMAGE = "2f57cec80d67f7b80550faa2abff7ea44a72e5fe200d61e30fd186921975baa3"

    def __init__(self, name:str = SAVE, savefile:str = SAVE, port:int = PORT, adminlist:str = ADMINLIST, **kwargs):
        # Command is an array if you want to include args
        self.command = f"""./bin/x64/factorio --start-server ./saves/{self.SAVE}.zip --server-settings ./saves/server-settings.json --server-adminlist {self.ADMINLIST} --port {self.PORT}""".split()
        self.game_name = "factorio-" + name
        self.port = port
        self.adminlist = adminlist
        self.savefile = savefile

    def create_game(self):
        with PodmanClient(base_url=self.uri) as client:         
            self.game_container = client.containers.run(
                image=self.IMAGE,
                ports={f"{self.port}/udp": self.port},
                detach=True,
                mounts=[
                    {
                        "type": "bind",
                        "source": f"/root/projects/factorio-container/saves/{self.SAVE}/",
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
                "ports": [port for port in self.game_container.attrs["HostConfig"]["PortBindings"]]
            }
            return result
