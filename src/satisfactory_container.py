"""
satisfactory_container — Low-level container creation for a single Satisfactory server.

Wraps the Podman Python API to run one ``satisfactory-server`` container with
host networking and environment variables for port configuration.

Consumed by satisfactory_server.py via ``SatisfactoryGame(...).create_game()``.
Each ``SatisfactoryGame`` instance represents one server; call ``create_game()``
once to start it.

Unlike the Factorio driver, Satisfactory has no RCON and no host-mounted saves
directory — saves live inside the container volume.
"""
from podman import PodmanClient


class SatisfactoryGame:
    """
    Configures and launches a single Satisfactory dedicated server container.

    The Satisfactory dedicated server requires two ports:
      - ``game_port`` (UDP/TCP): primary game traffic port (default 7777).
      - ``beacon_port`` (TCP): server beacon / discovery port, always
        ``game_port + 1111`` (default 8888).

    Both ports are injected into the container as ``PORT`` and ``BEACON_PORT``
    environment variables; the container's ``start_server.sh`` reads them.
    No saves directory is bind-mounted — the server manages its own saves
    inside the container filesystem (or a named volume, at the operator's
    discretion).

    Class attributes:
        PREFIX (str): Container name prefix used by SatisfactoryServer to
            identify managed containers.
        IMAGE (str): Fully-qualified container image reference.
        SOCKET_URI (str): Podman socket URI — always the root socket on this host.

    Instance attributes:
        name (str): Human-readable server name.
        game_name (str): Container name, always ``PREFIX + name``.
        port (int): Primary game port.
        beacon_port (int): Beacon port (``port + 1111``).
    """

    PREFIX = "satisfactory-"
    IMAGE = "localhost/satisfactory-server:latest"
    SOCKET_URI = "unix:///run/user/0/podman/podman.sock"

    def __init__(self, name: str, port: int = 7777):
        """
        Initialise container configuration without touching Podman.

        Args:
            name: Server display name and container name suffix.
            port: Primary UDP/TCP game port.  Defaults to 7777 (the
                Satisfactory dedicated server default).
        """
        self.name = name
        # All managed containers carry the "satisfactory-" prefix so
        # SatisfactoryServer can identify them with a simple startswith() check.
        self.game_name = self.PREFIX + name
        self.port = port
        # Beacon port is always game_port + 1111.  This offset is a project
        # convention: keeps beacon ports out of the standard Satisfactory range
        # while remaining easy to compute deterministically from the game port.
        self.beacon_port = port + 1111

    def create_game(self) -> dict:
        """
        Create and start the Satisfactory server container.

        Calls ``podman run`` (via the Python API) with:
          - ``network_mode="host"`` — the container shares the host network
            stack; no port-mapping configuration is required.
          - Container labels carrying the game and beacon ports so
            ``SatisfactoryServer._list_games()`` can recover them when the
            container is later listed from Podman.
          - Environment variables ``PORT`` and ``BEACON_PORT`` consumed by
            ``start_server.sh`` inside the container.

        Returns:
            dict: ``{"name": str, "port": int, "beacon_port": int,
                "container_id": str}`` describing the newly started container.

        Raises:
            podman.errors.APIError: If Podman rejects the request (e.g. image
                not found, port already in use).
        """
        with PodmanClient(base_url=self.SOCKET_URI) as client:
            container = client.containers.run(
                image=self.IMAGE,
                # Host networking avoids the need to configure port-mapping.
                # Satisfactory uses both UDP and TCP ports; host mode exposes
                # them directly on the host without translation overhead.
                network_mode="host",
                labels={
                    # Store ports as container labels so SatisfactoryServer._list_games()
                    # can reconstruct Game objects purely from the Podman API,
                    # without any external database.
                    "satisfactory.port": str(self.port),
                    "satisfactory.beacon-port": str(self.beacon_port),
                },
                environment={
                    # The start_server.sh script reads these env vars to pass
                    # -Port and -BeaconPort flags to the Satisfactory binary.
                    "PORT": str(self.port),
                    "BEACON_PORT": str(self.beacon_port),
                },
                detach=True,
                name=self.game_name,
            )
            return {
                "name": self.game_name,
                "port": self.port,
                "beacon_port": self.beacon_port,
                "container_id": container.id,
            }
