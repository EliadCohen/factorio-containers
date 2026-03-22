"""
factorio_server — High-level manager for all Factorio server containers.

``FactorioServer`` is the application's single source of truth for running
and stopped Factorio containers.  It wraps the Podman API (via
``FactorioGame`` / ``FactorioContainerImage``) and exposes game-lifecycle
operations used by the TUI layer (``server_control.py``).

Key responsibilities:
  - Enumerate containers whose name starts with ``"factorio-"`` and expose
    them as ``FactorioServer.Game`` objects (``_list_games``).
  - Start, stop, and delete individual games.
  - Gracefully save running games via RCON before destructive operations
    (``_rcon_save``).
  - Rebuild the container image and recreate all containers, preserving their
    run/stop state (``rebuild_and_recreate``).
"""
import time
import podman
from pathlib import Path
from podman import PodmanClient
from factorio_container import FactorioGame
from image_build import FactorioContainerImage
from leanrcon import send_command
from game_driver import GameDriver


class FactorioServer(GameDriver):
    """
    Application-level manager for all Factorio headless server containers.

    Maintains a ``games`` dict mapping container name → ``Game`` object.
    Call ``update_game_list()`` to refresh it from Podman before reading
    ``games``.

    Class attributes:
        GAME_PREFIX (str): Container name prefix used to distinguish managed
            Factorio containers from other Podman containers on the host.
    """
    GAME_PREFIX = "factorio-"

    class Game():
        """
        Thin wrapper around a single Podman container representing one
        Factorio server instance.

        RCON credentials are stored as container labels at creation time by
        ``FactorioGame.create_game()``.  ``_list_games()`` reads those labels
        back via ``container.inspect()["Config"]["Labels"]`` — the label
        round-trip is the only persistence mechanism; no external database is
        used.

        Class attributes:
            GAME_PREFIX (str): Mirrors the outer class prefix; used by
                ``display_name`` to strip the prefix without importing the
                outer class.

        Instance attributes:
            _container: Raw Podman container object (supports
                ``.start()``, ``.stop()``, ``.remove()``, ``.inspect()``).
            container_id (str): Full 64-char container ID.
            game_name (str): Full container name including ``"factorio-"``
                prefix.
            game_port (int): UDP port Factorio is listening on.
            savepath (str): Host-side path of the bind-mounted saves directory.
            rcon_port (int): RCON TCP port (game_port + 1000), or 0 if the
                label was absent.
            rcon_password (str): RCON password from the container label, or
                ``""`` if absent.
        """
        GAME_PREFIX = "factorio-"

        def __init__(self, container, container_id, game_name, game_port, game_path, labels=None):
            """
            Initialise a Game from Podman container data.

            Args:
                container: Podman container object.
                container_id (str): Container ID string.
                game_name (str): Full container name (includes prefix).
                game_port (int | str): UDP game port; coerced to int.
                game_path (str): Host path of the first bind mount (saves dir).
                labels (dict | None): Container label dict from
                    ``inspect()["Config"]["Labels"]``.  RCON credentials are
                    extracted from ``"factorio.rcon-port"`` and
                    ``"factorio.rcon-password"`` keys.  Defaults to ``{}`` if
                    None.
            """
            self._container = container
            self.container_id = container_id
            self.game_name = game_name
            self.game_port = int(game_port)
            self.savepath = game_path
            labels = labels or {}
            # RCON credentials are stored as container labels at create time.
            # If the labels are absent (e.g. container was created outside this
            # tool), rcon_port=0 and rcon_password="" signal "no RCON available".
            self.rcon_port = int(labels.get("factorio.rcon-port", 0))
            self.rcon_password = labels.get("factorio.rcon-password", "")

        @property
        def display_name(self) -> str:
            """
            Human-readable server name with the ``"factorio-"`` prefix removed.

            Returns:
                str: e.g. ``"myworld"`` for a container named
                    ``"factorio-myworld"``.
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

        def player_count(self) -> int | None:
            """
            Return the number of connected players via RCON, or None.

            Executes the Lua snippet
            ``/c rcon.print(#game.connected_players)`` against the server's
            RCON port.  Uses a 3-second timeout so the TUI refresh loop
            (which calls this for every running server) does not stall for
            more than a few seconds per game.

            Returns:
                int: Number of players (0 or more) on success.
                None: If RCON is not configured (port or password missing),
                    the server has not yet started, or any network/parse error
                    occurs.  The caller should display ``"-"`` in the UI.
            """
            if not self.rcon_port or not self.rcon_password:
                return None
            try:
                response = send_command(
                    "127.0.0.1", self.rcon_port, self.rcon_password,
                    "/silent-command rcon.print(#game.connected_players)",
                    timeout=3,  # short timeout: called on every TUI refresh
                )
                return int(response.strip())
            except Exception:
                # Any error (timeout, connection refused, parse failure) means
                # the count is unknown; return None rather than crashing.
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

    def __init__(self):
        """
        Initialise the server manager and open the Podman client connection.

        The Podman client is opened once and reused for the lifetime of the
        ``FactorioServer`` object.  ``games`` starts empty; call
        ``update_game_list()`` to populate it.
        """
        self._client = PodmanClient(uri = "unix:///run/user/0/podman/podman.sock")
        self._games = {}

    @property
    def games(self) -> dict:
        return self._games

    @games.setter
    def games(self, value: dict) -> None:
        self._games = value

    @property
    def game_prefix(self) -> str:
        return self.GAME_PREFIX

    @property
    def display_name(self) -> str:
        return "Factorio"

    @property
    def base_port(self) -> int:
        return 34197

    @property
    def image_tag(self) -> str:
        return "localhost/factorio-headless:latest"

    def get_all_ports(self) -> set[int]:
        """Return all ports used by Factorio containers (game + RCON)."""
        ports = set()
        for game in self.games.values():
            ports.add(game.game_port)
            if game.rcon_port:
                ports.add(game.rcon_port)
        return ports

    def start_game(self, game: "FactorioServer.Game"):
        """
        Start a stopped game container.

        Args:
            game: The ``Game`` object to start.
        """
        game.start()

    def stop_game(self, game: "FactorioServer.Game"):
        """
        Stop a running game container.

        Args:
            game: The ``Game`` object to stop.
        """
        game.stop()

    def delete_game(self, game: "FactorioServer.Game"):
        """
        Stop and remove a game container.

        Args:
            game: The ``Game`` object to delete.
        """
        game.delete()

    def _list_games(self) -> dict[str, "FactorioServer.Game"]:
        """
        Query Podman for all managed Factorio containers and return them.

        Filters ``podman ps --all`` to containers whose name starts with
        ``GAME_PREFIX``, then inspects each one to recover port and RCON
        credentials from the labels stored at creation time.

        The label round-trip (written by ``FactorioGame.create_game()``, read
        here) is the project's persistence strategy — no external config file
        or database is needed.

        Returns:
            dict: Mapping of full container name → ``Game`` object for every
                managed container (running or stopped).
        """
        conts = [con for con in (self._client.containers.list(all=True)) if con.name.startswith(self.GAME_PREFIX)]
        games = {}
        for con in conts:
            con_data = con.inspect()
            # Labels were written by FactorioGame.create_game() at container
            # creation time and are the only source of port/RCON data.
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
        """
        Refresh ``self.games`` from the live Podman state.

        Replaces ``self.games`` entirely; stale ``Game`` objects from a
        previous call are discarded.
        """
        self.games = self._list_games()

    def _rcon_save(self, game) -> bool:
        """
        Send ``/server-save`` via RCON and wait for the write to flush.

        Issues the save command and then sleeps for 2 seconds.  The sleep is
        necessary because ``/server-save`` is asynchronous — Factorio
        acknowledges the command immediately but writes the file on its own
        tick schedule.  Without the pause, a subsequent ``game.delete()``
        may remove the container before the write completes.

        Args:
            game: A ``Game`` object with valid ``rcon_port`` and
                ``rcon_password`` attributes.

        Returns:
            bool: ``True`` if the command was sent successfully and the sleep
                elapsed.  ``False`` if RCON is not configured on *game* or if
                any network/timeout error occurs.
        """
        if not game.rcon_port or not game.rcon_password:
            return False
        try:
            send_command("127.0.0.1", game.rcon_port, game.rcon_password, "/server-save")
            # Wait for Factorio to flush the save to disk.  The server
            # acknowledges the command before the file write completes,
            # so a brief pause is required to avoid a race with container
            # deletion.
            time.sleep(2)   # allow save to flush to disk
            return True
        except Exception:
            return False

    def create_game(self, name: str, port: int, savefile: str, force_name: bool = False):
        """
        Create and start a new Factorio server container.

        By default appends a numeric suffix (``-2``, ``-3``, …) to *name*
        until it is unique among existing containers, preventing Podman name
        collisions.  Pass ``force_name=True`` to skip this check — used by
        ``rebuild_and_recreate()`` where the old container has already been
        deleted and the original name must be reused exactly.

        Args:
            name: Desired server name (without the ``"factorio-"`` prefix).
            port: UDP game port.
            savefile: Save-file stem; must correspond to a subdirectory under
                ``saves/``.
            force_name (bool): If ``True``, use *name* as-is without checking
                for conflicts.  Defaults to ``False``.

        Returns:
            dict: The result dict from ``FactorioGame.create_game()``:
                ``{"name", "id", "running", "port"}``.
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
        new_game = FactorioGame(name=unique_name, savefile=savefile, port=port)
        return new_game.create_game()

    def rebuild_and_recreate(self) -> dict:
        """
        Rebuild the container image and recreate all managed containers.

        Performs a safe rolling update:
        1. **Snapshot** — capture name, port, savefile, and run-state for
           every container.
        2. **RCON save** — send ``/server-save`` to every running container
           and wait for it to flush (2 s sleep inside ``_rcon_save``).
        3. **Image rebuild** — call ``FactorioContainerImage().build()`` to
           produce a new ``factorio-headless:latest`` image.
        4. **Delete all** — stop and remove every container (``game.delete()``
           handles already-stopped containers gracefully).
        5. **Recreate all** — call ``create_game(force_name=True)`` for each
           snapshot.  ``containers.run()`` starts each container automatically.
        6. **Restore stop state** — for containers that were stopped before the
           rebuild, call ``stop()`` on the freshly created container so the
           final state mirrors the pre-rebuild state.

        Returns:
            dict: ``{"recreated": list[str], "restarted": list[str]}``
                where ``recreated`` contains every server that was recreated
                and ``restarted`` contains those that were running before and
                are now running again.
        """
        self.update_game_list()

        # Step 1: Snapshot all containers before touching anything.
        # This preserves the savefile name (derived from the bind-mount source
        # path) and the run-state so we can restore it after the rebuild.
        snapshots = [{
            "name": game.display_name,
            "port": game.game_port,
            "savefile": Path(game.savepath).name,
            "was_running": game.active_status,
            "rcon_saved": False,
        } for game in self.games.values()]

        # Step 2: Gracefully save running games via RCON before deleting them.
        for snap, game in zip(snapshots, list(self.games.values())):
            if snap["was_running"]:
                snap["rcon_saved"] = self._rcon_save(game)

        # Step 3: Rebuild the image (may take several minutes on first run).
        FactorioContainerImage().build()

        # Step 4: Stop and remove all containers.
        for game in list(self.games.values()):
            game.delete()

        # Step 5: Recreate all containers using the new image.
        # force_name=True so each container gets exactly its original name
        # (the old container was deleted in step 4, so there is no collision).
        # containers.run() auto-starts each container.
        recreated = []
        for snap in snapshots:
            self.create_game(name=snap["name"], port=snap["port"],
                             savefile=snap["savefile"], force_name=True)
            recreated.append(snap["name"])

        # Step 6: Stop containers that were stopped before the rebuild so the
        # final state matches the pre-rebuild state.
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
