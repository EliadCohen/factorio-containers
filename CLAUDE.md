# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

This project runs multiple game server instances (Factorio, Satisfactory, …) as Podman containers on a single Fedora host. It provides a tabbed TUI (Terminal User Interface) for managing those containers. Each game type is implemented as a **driver** that plugs into shared container management and TUI infrastructure. Designed for home network / local play use cases on Fedora Linux with Podman.

## Development Commands

```bash
# Install dependencies (uses uv)
uv sync

# Run the TUI application
uv run factainer
# or directly:
uv run python src/server_control.py

# Build the Factorio container image (with cache)
make build
# or: bash build.sh

# Rebuild Factorio container image (no cache)
make update

# Remove dangling podman images
make clean
# or: bash clean_images.sh
```

## Testing

```bash
# Unit tests (no Podman socket required)
uv run pytest tests/ --ignore=tests/integration/ -v

# Integration tests (requires Podman socket + built image + saves/testo/testo.zip)
uv run pytest tests/integration/ -v
```

The `podman` package is globally stubbed in `tests/conftest.py` so unit and TUI tests run without a live Podman socket.

Test files:
- `tests/test_logic.py` — RCON packet encoding, `FactorioGame` init, server label parsing, `_rcon_save`, name uniqueness, `rebuild_and_recreate`, player count
- `tests/test_satisfactory_logic.py` — `SatisfactoryGame` init, label parsing, no-op `_rcon_save`, `get_all_ports`, driver properties
- `tests/test_tui.py` — Textual widget composition, reactive markup, button handlers, refresh logic, port picker
- `tests/test_tui_tabs.py` — Tab composition, `GameTab` widget, cross-driver port utilities, `NewServer` mode switching, per-tab rebuild
- `tests/integration/test_rcon_save.py` — End-to-end: starts a real container, issues `/server-save`, verifies the `.zip` mtime changes

## Architecture

The application has five layers:

1. **Driver ABC** (`src/game_driver.py`) — `GameDriver` abstract base class. All game types implement this interface. Also contains module-level port utilities: `_all_ports_in_use(drivers)` and `_next_available_port(base, drivers)`.

2. **TUI Layer** (`src/server_control.py`) — Textual app. `ControlServer` is the main app class. Uses `TabbedContent` with one `TabPane` + `GameTab` per registered driver. `GameTab` contains a server list, per-tab rebuild button (`UpdateSection`), and a creation form (`NewServer`). `NewServer` shows a save file picker (`FilteredDirectoryTree`) for drivers that support it, or a plain name input for drivers that don't. CSS in `src/control_server.css`. Keyboard shortcuts: `q` quit, `r` refresh. Auto-refreshes every 5 seconds via `ControlServer.refresh_all()`.

3. **Game Managers** — each driver has its own server manager that wraps the Podman API:
   - `src/factorio_server.py` — `FactorioServer(GameDriver)`. Tracks containers with `factorio-` prefix. RCON credentials stored as container labels.
   - `src/satisfactory_server.py` — `SatisfactoryServer(GameDriver)`. Tracks `satisfactory-` prefixed containers. No RCON; `player_count()` always returns `None`.

4. **Container Creators** — each driver has its own creator that configures and launches containers:
   - `src/factorio_container.py` — `FactorioGame`. Uses `localhost/factorio-headless:latest`, binds host saves with `relabel="Z"` for SELinux, RCON port = game port + 1000.
   - `src/satisfactory_container.py` — `SatisfactoryGame`. Uses `localhost/satisfactory-server:latest`, no host saves mount, beacon port = game port + 1111.

5. **Image Builders** — each driver has its own image builder:
   - `src/image_build.py` — `FactorioContainerImage` builds from `Container/Containerfile`.
   - `src/satisfactory_image_build.py` — `SatisfactoryContainerImage` builds from `Container-Satisfactory/Containerfile`.

6. **RCON Client** (`src/leanrcon.py`) — Minimal Valve RCON implementation. Used by Factorio driver only. Wire format: `[len][req_id][ptype][body\x00\x00]` (little-endian).

### TUI wiring

`ControlServer.all_drivers` returns `[FactorioServer(), SatisfactoryServer()]`. SatisfactoryServer is imported lazily so TUI tests that only mock Factorio don't need the satisfactory module to exist. Adding a new driver means instantiating it in `all_drivers`.

`ServerEntry` strips `driver.game_prefix` from the container name for display. Delete/toggle actions call `driver.games[game_name].delete/start/stop()`. After any mutation, `self.ancestor(GameTab).refresh_game_list()` scopes the refresh to the correct tab.

### Port conflict detection

Every driver implements `get_all_ports()` returning the full set of ports its containers occupy (game port + any auxiliary ports like RCON or beacon). `_all_ports_in_use(all_drivers)` unions these sets and is called by `NewServer.on_mount()` (to suggest the next free port) and `NewServer.new_server()` (to validate before creation).

### Factorio saves layout

```
saves/
├── myworld/
│   ├── myworld.zip
│   └── server-settings.json   ← auto-created from Container/server-settings.json template
└── oldworld/
    ├── oldworld.zip
    └── server-settings.json
```

Files are chowned to UID 1001 before container start. The TUI hides autosave zips (names starting with `_`).

### Satisfactory data

Satisfactory saves live inside the container at `~/.config/Epic/FactoryGame/Saved/SaveGames/server`. No host-side save directory is mounted. The container image is built from `Container-Satisfactory/Containerfile` using SteamCMD to install App ID 1690800 (~7 GB download, cached as a Podman layer).

## Key Constraints

- Requires Podman (not Docker). The Python API connects to the root Podman socket (`/run/user/0/podman/podman.sock`).
- Each driver identifies its containers by a unique name prefix (e.g. `factorio-`, `satisfactory-`).
- Currently assumes root-level execution for the Podman socket path.
- `network_mode="host"` is used for all game containers — no port mapping; ports are exposed directly on the host.
- SELinux bind-mount labels (`relabel="Z"`) are required on Fedora for any host-directory mounts.
- The 2-second sleep in Factorio's `_rcon_save` is intentional — Factorio acknowledges `/server-save` before the file write completes.

## Adding a New Game Server Driver

To add support for a new game (e.g. Minecraft), follow these steps:

### 1. Create the container creator (`src/<game>_container.py`)

Model it on `src/satisfactory_container.py` (no RCON) or `src/factorio_container.py` (with RCON). The creator must:

- Define `PREFIX = "<game>-"` and `IMAGE = "localhost/<game>-server:latest"`
- Set up all ports the game uses (game port + any aux ports like RCON, beacon, query port)
- Store port values as container labels so they can be recovered later without a database
- Call `client.containers.run()` with `network_mode="host"` and `detach=True`
- Return a dict with at minimum `{"name": str, "port": int}`

### 2. Create the server manager (`src/<game>_server.py`)

Model it on `src/satisfactory_server.py`. The manager must:

- Define a `GAME_PREFIX = "<game>-"` class constant
- Define a nested `Game` class with:
  - `game_name: str` — full container name including prefix
  - `game_port: int` — primary port
  - `active_status: bool` — reads `container.inspect()["State"]["Running"]`
  - `player_count() -> int | None` — RCON query or `return None` if unsupported
  - `start()`, `stop()`, `delete()` — delegate to `self._container`
- Inherit `GameDriver` and implement all abstract properties and methods:

```python
class MyGameServer(GameDriver):
    GAME_PREFIX = "mygame-"

    @property
    def game_prefix(self) -> str: return self.GAME_PREFIX
    @property
    def display_name(self) -> str: return "My Game"     # shown as tab label
    @property
    def base_port(self) -> int: return 25565             # default port suggestion
    @property
    def image_tag(self) -> str: return "localhost/mygame-server:latest"

    @property
    def games(self) -> dict: return self._games
    @games.setter
    def games(self, value): self._games = value

    def update_game_list(self): self.games = self._list_games()
    def get_all_ports(self) -> set[int]: ...  # return ALL ports your containers use
    def create_game(self, name, port, **kwargs): ...
    def rebuild_and_recreate(self) -> dict: ...  # returns {"recreated": [...], "restarted": [...]}

    # Optional overrides (defaults are True):
    def supports_player_count(self) -> bool: return False  # set False if no RCON/API
    def supports_save_picker(self) -> bool: return False   # set False if no save file needed
```

- `_list_games()` should query Podman with `containers.list(all=True)`, filter by `GAME_PREFIX`, and read port values from container labels (the same labels written by the container creator)
- `_games` backing store + `games` property/setter is required so that `self.games = ...` works in `update_game_list()` and test mocks can patch `driver.games = {}`

### 3. Create the image builder (`src/<game>_image_build.py`)

Model it on `src/satisfactory_image_build.py`:

```python
class MyGameContainerImage:
    IMAGE_TAG = "mygame-server:latest"
    CONTAINERFILE = "/root/projects/factorio-container/Container-MyGame/Containerfile"
    SOCKET_URI = "unix:///run/user/0/podman/podman.sock"

    def build(self) -> None:
        with PodmanClient(base_url=self.SOCKET_URI) as client:
            client.images.build(path=..., dockerfile=self.CONTAINERFILE, tag=self.IMAGE_TAG)
```

### 4. Write the Containerfile (`Container-MyGame/Containerfile`)

Follow the Fedora-based pattern used by `Container/Containerfile` (Factorio) or `Container-Satisfactory/Containerfile` (Satisfactory). Key conventions:
- Create a non-root user to run the server
- Use `EXPOSE` to document ports
- Use `CMD` to point to a startup script
- Pass runtime config (port, name, etc.) via environment variables

### 5. Register the driver in the TUI (`src/server_control.py`)

Add it to `ControlServer.all_drivers`:

```python
@property
def all_drivers(self) -> list:
    drivers = [self.factorio_driver]
    try:
        from satisfactory_server import SatisfactoryServer
        ...
        drivers.append(self._satisfactory_driver)
    except ImportError:
        pass
    try:
        from mygame_server import MyGameServer          # ← add this block
        if not hasattr(self.__class__, "_mygame_driver"):
            self.__class__._mygame_driver = MyGameServer()
        drivers.append(self._mygame_driver)
    except ImportError:
        pass
    return drivers
```

The lazy import pattern means adding the driver never breaks existing tests that don't mock the new module.

### 6. Write tests

Model them on `tests/test_satisfactory_logic.py`. Cover at minimum:
- Container creator: prefix, port convention, label storage, network mode
- Server manager: label parsing, `player_count()` return value, `supports_*()` flags, `get_all_ports()`, name uniqueness, `_rcon_save()` behaviour
- Driver properties: `game_prefix`, `display_name`, `base_port`, `image_tag`

Run the full suite to verify nothing regressed:

```bash
uv run pytest tests/ --ignore=tests/integration/ -v
```
