"""
factorio_container — Low-level container creation for a single Factorio server.

Wraps the Podman Python API to run one ``factorio-headless`` container with the
correct mounts, network mode, environment, and RCON credentials.

Consumed by factorio_server.py via ``FactorioGame(...).create_game()``.
Each ``FactorioGame`` instance represents one server; call ``create_game()``
once to start it.
"""
import json
import os
import secrets
import shutil
from podman import PodmanClient

# uri = "unix:///run/user/0/podman/podman.sock"
# PORT=34200
# SAVE="monomono"
# ADMINLIST = "./data/server-adminlist.json"

class FactorioGame():
    """
    Configuration and launcher for a single Factorio headless server container.

    Builds the Factorio command line, generates ephemeral RCON credentials,
    prepares the saves directory, and calls ``podman run`` via the Python API.

    Class attributes (defaults used when parameters are omitted):
        uri (str): Podman socket URI — always the root socket on this host.
        PORT (int): Default UDP game port.
        SAVE (str): Default save-file stem.
        ADMINLIST (str): Path to the server admin list JSON inside the repo.
        IMAGE (str): Fully-qualified container image reference.
        TEMPLATE_PATH (str): Absolute path to the bundled server-settings
            template (resolved relative to this source file).
        FACTORIO_UID (int): UID of the ``factorio`` user inside the container
            (1001); files in the saves bind-mount must be owned by this UID.

    Instance attributes set by ``__init__``:
        rcon_port (int): Game port + 1000.  The 1000 offset is an arbitrary
            convention that keeps RCON ports out of the standard Factorio range
            while remaining easy to derive from the game port.
        rcon_password (str): Cryptographically random 16-char hex token
            generated fresh for every instance so each container has a unique
            credential.
        command (list[str]): Factorio binary command split into argv form,
            ready for ``containers.run(command=...)``.
        name (str): Human-readable server name (also used as the Factorio
            in-game server name via server-settings.json).
        game_name (str): Container name, always ``"factorio-" + name``.
        port (int): UDP game port.
        savefile (str): Save-file stem (without ``.zip``); controls which
            sub-directory under ``saves/`` is mounted.
    """
    uri = "unix:///run/user/0/podman/podman.sock"
    PORT=34200
    SAVE="testo"
    ADMINLIST = "./data/server-adminlist.json"
    IMAGE = "localhost/factorio-headless:latest"

    TEMPLATE_PATH = os.path.join(os.path.dirname(__file__), "../Container/server-settings.json")

    def __init__(self, name:str = SAVE, savefile:str = SAVE, port:int = PORT, adminlist:str = ADMINLIST, **kwargs):
        """
        Initialise container configuration without touching Podman.

        Args:
            name: Server display name and container name suffix.
            savefile: Save-file stem; the container will load
                ``saves/<savefile>/<savefile>.zip``.
            port: UDP port Factorio listens on for game traffic.
            adminlist: Path to the server-adminlist.json file passed to
                Factorio at startup.
            **kwargs: Ignored; allows callers to forward extra keyword args
                without breaking the interface.
        """
        # RCON port is game port + 1000.  This offset is a project convention:
        # high enough to be outside the normal Factorio port range (34197) yet
        # easy to compute deterministically from the game port.
        self.rcon_port = port + 1000
        # Generate a fresh secret for every instance so no two containers
        # share credentials, even if they use the same save or port.
        self.rcon_password = secrets.token_hex(8)
        self.command = (
            f"./bin/x64/factorio --start-server ./saves/{savefile}.zip"
            f" --server-settings ./saves/server-settings.json"
            f" --server-adminlist {adminlist}"
            f" --port {port}"
            f" --rcon-port {self.rcon_port}"
            f" --rcon-password {self.rcon_password}"
        ).split()
        self.name = name
        # All managed containers carry the "factorio-" prefix so FactorioServer
        # can identify them with a simple name.startswith() check.
        self.game_name = "factorio-" + name
        self.port = port
        self.adminlist = adminlist
        self.savefile = savefile

    FACTORIO_UID = 1001  # UID of the non-root "factorio" user inside the image

    def _prepare_server_settings(self, saves_path: str):
        """
        Ensure a valid ``server-settings.json`` exists in *saves_path*.

        If the file is absent, copies the bundled template from
        ``Container/server-settings.json`` into the saves directory.
        In both cases, overwrites the ``"name"`` key with ``self.name`` so the
        in-game server listing shows the correct name.

        Args:
            saves_path: Absolute path to the save subdirectory (e.g.
                ``/root/projects/factorio-container/saves/myworld/``).

        Side effects:
            May create ``server-settings.json`` in *saves_path*.
            Always rewrites the ``"name"`` field and writes the file back.
        """
        settings_path = os.path.join(saves_path, "server-settings.json")
        if not os.path.exists(settings_path):
            shutil.copy(os.path.abspath(self.TEMPLATE_PATH), settings_path)
        with open(settings_path, "r") as f:
            settings = json.load(f)
        settings["name"] = self.name
        with open(settings_path, "w") as f:
            json.dump(settings, f, indent=2)

    def create_game(self):
        """
        Prepare the saves directory and start the Factorio container.

        Steps:
        1. Write / update ``server-settings.json`` in the saves directory.
        2. Recursively ``chown`` the saves directory to UID/GID 1001 so the
           non-root ``factorio`` user inside the container can write autosaves.
        3. Call ``podman run`` (via the Python API) with:
           - ``network_mode="host"`` — the container shares the host network
             stack, which means the Factorio UDP port is exposed without any
             port-mapping configuration.  This is intentional for a home-LAN
             server that always runs as root.
           - A bind-mount of the saves subdirectory into the container, with
             ``relabel="Z"`` to apply a private SELinux label so Fedora's
             SELinux policy permits container access to the host directory.
           - Container labels carrying the port and RCON credentials so
             ``FactorioServer._list_games()`` can recover them when the
             container is later listed from Podman.

        Returns:
            dict: ``{"name": str, "id": str, "running": bool, "port": int}``
                describing the newly started container.

        Raises:
            podman.errors.APIError: If Podman rejects the request (e.g.
                image not found, port already in use).
            PermissionError: If the process lacks permission to chown files
                (requires running as root on the host).
        """
        saves_path = f"/root/projects/factorio-container/saves/{self.savefile}/"
        self._prepare_server_settings(saves_path)
        # Recursively chown the saves directory to FACTORIO_UID (1001) so
        # the non-root container user can write saves and autosave files.
        for dirpath, dirnames, filenames in os.walk(saves_path):
            os.chown(dirpath, self.FACTORIO_UID, self.FACTORIO_UID)
            for filename in filenames:
                os.chown(os.path.join(dirpath, filename), self.FACTORIO_UID, self.FACTORIO_UID)
        with PodmanClient(base_url=self.uri) as client:
            self.game_container = client.containers.run(
                image=self.IMAGE,
                # Host networking avoids the need to configure UDP port-mapping.
                # Factorio uses UDP; Docker/Podman port-mapping adds complexity
                # and is unnecessary on a single-host home server running as root.
                network_mode="host",
                labels={
                    # Store port and RCON credentials as container labels so
                    # FactorioServer._list_games() can reconstruct Game objects
                    # purely from the Podman API, without any external database.
                    "factorio.port": str(self.port),
                    "factorio.rcon-port": str(self.rcon_port),
                    "factorio.rcon-password": self.rcon_password,
                },
                detach=True,
                mounts=[
                    {
                        "type": "bind",
                        "source": f"/root/projects/factorio-container/saves/{self.savefile}/",
                        "target": "/home/factorio/factorio/saves/",
                        "read_only": False,
                        # relabel="Z" applies a private SELinux context to the
                        # bind-mount so Fedora's SELinux policy allows the
                        # container process to read and write the host directory.
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
