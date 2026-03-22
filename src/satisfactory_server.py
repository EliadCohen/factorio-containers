"""
satisfactory_server — High-level manager for all Satisfactory server containers.

``SatisfactoryServer`` is the application's single source of truth for running
and stopped Satisfactory containers.  It wraps the Podman API (via
``SatisfactoryGame`` / ``SatisfactoryContainerImage``) and exposes game-lifecycle
operations used by the TUI layer (``server_control.py``).

Key responsibilities:
  - Enumerate containers whose name starts with ``"satisfactory-"`` and expose
    them as ``SatisfactoryServer.Game`` objects (``_list_games``).
  - Start, stop, and delete individual games.
  - Rebuild the container image and recreate all containers, preserving their
    run/stop state (``rebuild_and_recreate``).
  - No RCON support — Satisfactory does not expose a remote console compatible
    with the Valve RCON protocol used by Factorio.
"""
from podman import PodmanClient
from game_driver import GameDriver
from satisfactory_container import SatisfactoryGame
from satisfactory_image_build import SatisfactoryContainerImage


class SatisfactoryServer(GameDriver):
    """
    Application-level manager for all Satisfactory dedicated server containers.

    Maintains a ``games`` dict mapping container name → ``Game`` object.
    Call ``update_game_list()`` to refresh it from Podman before reading
    ``games``.

    Implements the ``GameDriver`` ABC.  Because Satisfactory has no RCON
    interface, ``supports_player_count()`` returns ``False`` and
    ``_rcon_save()`` is a no-op.

    Class attributes:
        GAME_PREFIX (str): Container name prefix used to distinguish managed
            Satisfactory containers from other Podman containers on the host.
    """

    GAME_PREFIX = "satisfactory-"

    class Game:
        """
        Thin wrapper around a single Podman container representing one
        Satisfactory server instance.

        Port information is stored as container labels at creation time by
        ``SatisfactoryGame.create_game()``.  ``_list_games()`` reads those
        labels back via ``container.inspect()["Config"]["Labels"]`` — the
        label round-trip is the only persistence mechanism; no external
        database is used.

        Class attributes:
            GAME_PREFIX (str): Mirrors the outer class prefix; used by
                ``display_name`` to strip the prefix without importing the
                outer class.

        Instance attributes:
            _container: Raw Podman container object.
            container_id (str): Full container ID.
            game_name (str): Full container name including ``"satisfactory-"``
                prefix.
            game_port (int): Primary UDP/TCP game port.
            beacon_port (int): Beacon/discovery port (game_port + 1111 by default).
        """

        GAME_PREFIX = "satisfactory-"

        def __init__(self, container, container_id: str, game_name: str,
                     game_port: int, beacon_port: int):
            """
            Initialise a Game from Podman container data.

            Args:
                container: Podman container object.
                container_id (str): Container ID string.
                game_name (str): Full container name (includes prefix).
                game_port (int | str): Primary game port; coerced to int.
                beacon_port (int | str): Beacon port; coerced to int.
            """
            self._container = container
            self.container_id = container_id
            self.game_name = game_name
            self.game_port = int(game_port)
            self.beacon_port = int(beacon_port)

        @property
        def display_name(self) -> str:
            """
            Human-readable server name with the ``"satisfactory-"`` prefix removed.

            Returns:
                str: e.g. ``"myserver"`` for a container named
                    ``"satisfactory-myserver"``.
            """
            return self.game_name.removeprefix(self.GAME_PREFIX)

        def __str__(self):
            return self.game_name

        @property
        def active_status(self) -> bool:
            """
            Whether the container is currently running.

            Calls ``container.inspect()`` on every access, so it always
            reflects the live Podman state.

            Returns:
                bool: ``True`` if the container's ``State.Running`` is true.
            """
            inspection = self._container.inspect()
            return inspection["State"]["Running"]

        def player_count(self) -> None:
            """
            Always returns ``None`` — Satisfactory has no RCON player count API.

            Returns:
                None: Always.  The TUI should display ``"-"`` for player count.
            """
            return None

        def start(self):
            """Start the container via Podman."""
            self._container.start()

        def stop(self):
            """Stop the container via Podman (sends SIGTERM, then SIGKILL)."""
            self._container.stop()

        def delete(self):
            """
            Stop and remove the container, ignoring errors.

            Attempts stop first (container may already be stopped), then
            remove.  Errors during stop are silently swallowed; errors during
            remove are printed to stdout for debugging.
            """
            try:
                self._container.stop()
            except Exception:
                pass   # already stopped — that's fine
            try:
                self._container.remove()
            except Exception as ex:
                print(ex)

    # ── GameDriver properties ──────────────────────────────────────────────────

    @property
    def game_prefix(self) -> str:
        """Container name prefix for Satisfactory managed containers."""
        return "satisfactory-"

    @property
    def display_name(self) -> str:
        """Human-readable game name shown in the TUI tab header."""
        return "Satisfactory"

    @property
    def base_port(self) -> int:
        """Default game port for Satisfactory dedicated servers."""
        return 7777

    @property
    def image_tag(self) -> str:
        """Container image reference for the Satisfactory server image."""
        return "localhost/satisfactory-server:latest"

    def supports_player_count(self) -> bool:
        """
        Returns ``False`` — Satisfactory has no RCON for player count queries.

        Returns:
            bool: Always ``False``.
        """
        return False

    def supports_save_picker(self) -> bool:
        """
        Returns ``False`` — Satisfactory manages its own saves inside the
        container; no host-side save file selection is needed.

        Returns:
            bool: Always ``False``.
        """
        return False

    # ── constructor ────────────────────────────────────────────────────────────

    def __init__(self):
        """
        Initialise the server manager and open the Podman client connection.

        The Podman client is opened once and reused for the lifetime of the
        ``SatisfactoryServer`` object.  ``_games`` starts empty; call
        ``update_game_list()`` to populate it.
        """
        self._client = PodmanClient(uri="unix:///run/user/0/podman/podman.sock")
        self._games = {}

    @property
    def games(self) -> dict:
        """Current mapping of container name → Game object."""
        return self._games

    @games.setter
    def games(self, value: dict):
        """Replace the games mapping."""
        self._games = value

    # ── lifecycle ──────────────────────────────────────────────────────────────

    def start_game(self, game: "SatisfactoryServer.Game"):
        """
        Start a stopped game container.

        Args:
            game: The ``Game`` object to start.
        """
        game.start()

    def stop_game(self, game: "SatisfactoryServer.Game"):
        """
        Stop a running game container.

        Args:
            game: The ``Game`` object to stop.
        """
        game.stop()

    def delete_game(self, game: "SatisfactoryServer.Game"):
        """
        Stop and remove a game container.

        Args:
            game: The ``Game`` object to delete.
        """
        game.delete()

    def _list_games(self) -> dict[str, "SatisfactoryServer.Game"]:
        """
        Query Podman for all managed Satisfactory containers and return them.

        Filters ``podman ps --all`` to containers whose name starts with
        ``GAME_PREFIX``, then inspects each one to recover port information
        from the labels stored at creation time.

        The label round-trip (written by ``SatisfactoryGame.create_game()``,
        read here) is the project's persistence strategy — no external config
        file or database is needed.

        Returns:
            dict: Mapping of full container name → ``Game`` object for every
                managed container (running or stopped).
        """
        conts = [
            con for con in self._client.containers.list(all=True)
            if con.name.startswith(self.GAME_PREFIX)
        ]
        games = {}
        for con in conts:
            con_data = con.inspect()
            labels = con_data["Config"]["Labels"]
            game_port = int(labels.get("satisfactory.port", 0))
            # Fall back to game_port + 1111 if beacon-port label is absent
            # (e.g. container was created outside this tool).
            beacon_port = int(
                labels.get("satisfactory.beacon-port", game_port + 1111)
            )
            games[con_data["Name"]] = self.Game(
                container=con,
                container_id=con_data["Id"],
                game_name=con_data["Name"],
                game_port=game_port,
                beacon_port=beacon_port,
            )
        return games

    def update_game_list(self):
        """
        Refresh ``self.games`` from the live Podman state.

        Replaces ``self.games`` entirely; stale ``Game`` objects from a
        previous call are discarded.
        """
        self.games = self._list_games()

    def _rcon_save(self, game) -> bool:
        """
        No-op — Satisfactory has no RCON interface.

        Args:
            game: Ignored.

        Returns:
            bool: Always ``False``.
        """
        return False

    def create_game(self, name: str, port: int, force_name: bool = False, **kwargs) -> dict:
        """
        Create and start a new Satisfactory server container.

        By default appends a numeric suffix (``-2``, ``-3``, …) to *name*
        until it is unique among existing containers, preventing Podman name
        collisions.  Pass ``force_name=True`` to skip this check — used by
        ``rebuild_and_recreate()`` where the old container has already been
        deleted and the original name must be reused exactly.

        Args:
            name: Desired server name (without the ``"satisfactory-"`` prefix).
            port: Primary UDP/TCP game port.
            force_name (bool): If ``True``, use *name* as-is without checking
                for conflicts.  Defaults to ``False``.
            **kwargs: Accepted but ignored (for interface compatibility with
                save-file drivers like FactorioServer).

        Returns:
            dict: The result dict from ``SatisfactoryGame.create_game()``:
                ``{"name", "port", "beacon_port", "container_id"}``.
        """
        if force_name:
            # Rebuild path: old container is gone, reuse the original name.
            unique_name = name
        else:
            # Ensure uniqueness by appending -2, -3, … until no collision.
            existing = set(self.games.keys())
            unique_name = name
            suffix = 2
            while (self.GAME_PREFIX + unique_name) in existing:
                unique_name = f"{name}-{suffix}"
                suffix += 1
        new_game = SatisfactoryGame(name=unique_name, port=port)
        return new_game.create_game()

    def rebuild_and_recreate(self) -> dict:
        """
        Rebuild the container image and recreate all managed containers.

        Performs a safe rolling update:
        1. **Snapshot** — capture name, port, and run-state for every container.
        2. **RCON save** — no-op for Satisfactory (``_rcon_save`` returns False
           immediately).
        3. **Image rebuild** — call ``SatisfactoryContainerImage().build()`` to
           produce a new ``satisfactory-server:latest`` image.
        4. **Delete all** — stop and remove every container.
        5. **Recreate all** — call ``create_game(force_name=True)`` for each
           snapshot.
        6. **Restore stop state** — for containers that were stopped before the
           rebuild, call ``stop()`` on the freshly created container.

        Returns:
            dict: ``{"recreated": list[str], "restarted": list[str]}``
                where ``recreated`` contains every server that was recreated
                and ``restarted`` contains those that were running before and
                are now running again.
        """
        self.update_game_list()

        # Step 1: Snapshot all containers before touching anything.
        snapshots = [{
            "name": game.display_name,
            "port": game.game_port,
            "was_running": game.active_status,
        } for game in self.games.values()]

        # Step 2: RCON save — no-op for Satisfactory.
        # _rcon_save always returns False; no sleep or RCON call is made.
        for snap, game in zip(snapshots, list(self.games.values())):
            if snap["was_running"]:
                self._rcon_save(game)

        # Step 3: Rebuild the image.
        SatisfactoryContainerImage().build()

        # Step 4: Stop and remove all containers.
        for game in list(self.games.values()):
            game.delete()

        # Step 5: Recreate all containers using the new image.
        # force_name=True so each container gets exactly its original name.
        recreated = []
        for snap in snapshots:
            self.create_game(name=snap["name"], port=snap["port"], force_name=True)
            recreated.append(snap["name"])

        # Step 6: Stop containers that were stopped before the rebuild.
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

    def get_all_ports(self) -> set:
        """
        Return the set of all ports currently in use by managed containers.

        Includes both game ports and beacon ports for every managed container.
        Used by the TUI port picker to avoid suggesting already-occupied ports.

        Returns:
            set[int]: Union of all game_port and beacon_port values.
        """
        games = self.games
        return (
            {g.game_port for g in games.values()} |
            {g.beacon_port for g in games.values()}
        )


if __name__ == "__main__":
    server_obj = SatisfactoryServer()
    server_obj.update_game_list()
    print(server_obj.games)
