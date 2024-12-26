import podman
from podman import PodmanClient

class FactorioServer():
    GAME_PREFIX = "factorio-"
    class Game():
        GAME_PREFIX = "factorio-"
        def __init__(self, container, container_id, game_name, game_port, game_path):
            self._container = container
            self.container_id = container_id
            self.game_name = game_name
            self.game_port = game_port
            self.savepath = game_path
        
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
                self._container.remove()
            except Exception as ex:
                print(ex)

    
    def __init__(self):
        self._client = PodmanClient(uri = "unix:///run/user/0/podman/podman.sock")
        self.games = []
        
    def start_game(self, game:Game):
        game.start()

    def stop_game(self, game:Game):
        game.stop()

    def delete_game(self, game:Game):
        game.delete()

    def _list_games(self) -> list[Game]:
        conts = [con for con in (self._client.containers.list(all=True)) if con.name.startswith(self.GAME_PREFIX)]
        games = []
        for con in conts:
            con_data = con.inspect()
            for prt in con_data["NetworkSettings"]["Ports"].keys():
                port = prt 
                break
            games.append(self.Game(container=con, container_id=con_data["Id"], game_name=con_data["Name"], game_port=port, game_path=con_data["Mounts"][0]["Source"]))
        return games
 
    def update_game_list(self):
        self.games = self._list_games()

if __name__ == "__main__":
    server_obj = FactorioServer()
    server_obj.update_game_list()
    print("Hello")