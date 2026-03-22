# factorio-container

Run multiple game server instances (Factorio, Satisfactory, …) as Podman containers on a single Fedora host, managed through a tabbed terminal UI. Each game type is a self-contained **driver** — adding a new game means adding a driver, not touching the shared infrastructure.

## Features

- **Multi-game, tabbed UI** — one tab per game type; each tab manages its own servers independently
- **Multi-server management** — run any number of servers per game type simultaneously on different ports
- **Live player count** — queries each running server every 5 seconds (Factorio via RCON; other games show `-` where not supported)
- **Per-tab rebuild** — rebuild a game's container image and recreate only its servers, preserving run/stop state
- **Save file picker** — browse the `saves/` directory tree to select a save when launching a new Factorio server
- **Name + port form** — for games without save files (e.g. Satisfactory), enter a name and port directly
- **Cross-driver port safety** — the port suggestion and validation check all running containers across all game types
- **RCON integration** — ephemeral per-container RCON passwords for Factorio (saves and player queries)
- **SELinux compatible** — bind mounts use `relabel="Z"` for Fedora/SELinux hosts

## Requirements

- **Fedora Linux** (tested; other SELinux-enabled distros may work)
- **Podman** — must be running as root; the app connects to `/run/user/0/podman/podman.sock`
- **Python 3.12+** with [uv](https://github.com/astral-sh/uv) for dependency management
- **Built container image(s)** — `localhost/factorio-headless:latest` for Factorio; `localhost/satisfactory-server:latest` for Satisfactory

## Quick Start

```bash
# 1. Clone the repository
git clone <repo-url>
cd factorio-container

# 2. Install Python dependencies
uv sync

# 3. Build the Factorio container image
make build

# 4. Place your save files
mkdir -p saves/myworld
cp /path/to/myworld.zip saves/myworld/

# 5. Launch the TUI
uv run factainer
```

## TUI Overview

The TUI opens with one tab per game type. Switch between tabs with the mouse or arrow keys.

```
┌─[Factorio]──[Satisfactory]──────────────────────────────────────┐
│                                                                  │
│  myworld    :34197  ● Online    2 players  ◉ ON  [Delete]       │
│  oldworld   :34198  ○ Offline   -          ○ OFF [Delete]       │
│                                                                  │
│  [Rebuild & Recreate All]                                        │
│                                                                  │
│  ▾ saves/                        Selected: myworld.zip           │
│    ▾ myworld/                    Port: [34199              ]     │
│        myworld.zip                                               │
│    ▾ oldworld/                   [Run server]                    │
│        oldworld.zip                                              │
└──────────────────────────────────────────────────────────────────┘
```

```
┌─[Factorio]──[Satisfactory]──────────────────────────────────────┐
│                                                                  │
│  myfactory  :7777   ● Online    -          ◉ ON  [Delete]       │
│                                                                  │
│  [Rebuild & Recreate All]                                        │
│                                                                  │
│  Name: [my-factory          ]                                    │
│  Port: [7778               ]                                     │
│  [Run server]                                                    │
└──────────────────────────────────────────────────────────────────┘
```

### Server list

Each row represents one managed container:

| Column | Description |
|---|---|
| Name | Server name (without game prefix) |
| Port | Primary UDP/TCP port |
| Status | `Online` (orange) or `Offline` (red) |
| Players | Connected players, or `-` if server is stopped or game doesn't support RCON |
| Switch | Toggle to start (`ON`) or stop (`OFF`) |
| Delete | Stop and remove the container permanently |

### Keyboard shortcuts

| Key | Action |
|---|---|
| `q` | Quit |
| `r` | Manually refresh all tabs |

The list also refreshes automatically every 5 seconds.

### Rebuild & Recreate All (per tab)

Each game tab has its own rebuild button. Clicking it:
1. Saves any running servers gracefully (Factorio: via RCON `/server-save`; Satisfactory: no-op)
2. Rebuilds only that game's container image
3. Deletes and recreates all of that game's containers with the new image
4. Restores the previous run/stop state

The operation runs in a background thread so the TUI stays responsive.

### New server form

**Factorio tab** — browse the `saves/` directory tree to pick a `.zip` save file, adjust the port if needed, then click **Run server**.

**Satisfactory tab** — enter a server name and port, then click **Run server**. Satisfactory manages its own save files internally.

The port field always shows the next free port, conflict-checked across all game types.

## Architecture

```
┌──────────────────────────────────────────────────────────┐
│  server_control.py  (TUI)                                │
│  ControlServer — TabbedContent, one GameTab per driver   │
│  GameTab — server list + UpdateSection + NewServer       │
│  ServerEntry — one row (name/port/status/players/toggle) │
└────────────────────┬─────────────────────────────────────┘
                     │ driver.games, driver.create_game(), …
┌────────────────────▼─────────────────────────────────────┐
│  game_driver.py  (GameDriver ABC)                        │
│  _all_ports_in_use / _next_available_port utilities      │
└──────┬──────────────────────────────────────┬────────────┘
       │                                      │
┌──────▼──────────────┐            ┌──────────▼────────────┐
│  factorio_server.py │            │ satisfactory_server.py│
│  FactorioServer     │            │ SatisfactoryServer    │
│  (GameDriver impl)  │            │ (GameDriver impl)     │
└──────┬──────────────┘            └──────────┬────────────┘
       │ create                               │ create
┌──────▼──────────────┐            ┌──────────▼────────────┐
│ factorio_container  │            │satisfactory_container │
│ FactorioGame        │            │SatisfactoryGame       │
│ podman run + mounts │            │podman run (no mounts) │
└──────┬──────────────┘            └──────────┬────────────┘
       │ rebuild                              │ rebuild
┌──────▼──────────────┐            ┌──────────▼────────────┐
│  image_build.py     │            │satisfactory_image_    │
│  FactorioContainer  │            │build.py               │
│  Image              │            │SatisfactoryContainer  │
└──────────────────┬──┘            │Image                  │
                   │               └───────────────────────┘
┌──────────────────▼──┐
│  leanrcon.py        │  (Factorio only)
│  RCONClient         │
└─────────────────────┘
```

### GameDriver ABC

Every game type implements `GameDriver` (`src/game_driver.py`):

| Member | Description |
|---|---|
| `game_prefix` | Container name prefix, e.g. `"factorio-"` |
| `display_name` | Tab label, e.g. `"Factorio"` |
| `base_port` | Default starting port for suggestions |
| `image_tag` | Podman image name |
| `games` | Dict of `container_name → Game` |
| `update_game_list()` | Refresh `games` from Podman |
| `create_game(name, port, **kwargs)` | Create and start a new container |
| `rebuild_and_recreate()` | Rebuild image and recreate containers |
| `get_all_ports()` | All ports occupied by this driver's containers |
| `supports_player_count()` | `True` if live player count is available (default `True`) |
| `supports_save_picker()` | `True` if creation form needs a save file (default `True`) |

### Port safety

`get_all_ports()` returns every port a driver's containers use — game port plus any auxiliary ports (RCON, beacon, query). The TUI unions these across all drivers before suggesting or validating a port, so two different game types can never accidentally share a port.

## Game-specific notes

### Factorio

- Container image: `localhost/factorio-headless:latest` built from `Container/Containerfile`
- Saves: host directory `saves/<name>/` bind-mounted into the container with SELinux relabeling
- RCON port = game port + 1000; random password stored as a container label
- `server-settings.json` auto-created from `Container/server-settings.json` template on first run

```
saves/
├── myworld/
│   ├── myworld.zip          ← main save
│   └── server-settings.json ← auto-created
└── oldworld/
    ├── oldworld.zip
    └── server-settings.json
```

### Satisfactory

- Container image: `localhost/satisfactory-server:latest` built from `Container-Satisfactory/Containerfile`
- Installation: SteamCMD downloads App ID 1690800 (~7 GB, cached as a Podman image layer)
- Ports: game port (default 7777, TCP/UDP) + beacon port = game port + 1111
- Saves: managed internally by the game at `~/.config/Epic/FactoryGame/Saved/SaveGames/server` inside the container
- No RCON; player count always shows `-`

## Saves Directory (Factorio)

```
saves/
├── myworld/
│   ├── myworld.zip
│   └── server-settings.json
└── oldworld/
    ├── oldworld.zip
    └── server-settings.json
```

Autosave files (`_autosave*.zip`) are hidden from the TUI picker.

## Development

```bash
uv sync                          # install dependencies
uv run factainer                 # run the TUI
make build                       # build Factorio image (cached)
make update                      # rebuild Factorio image (no cache)
make clean                       # remove dangling Podman images
```

## Testing

```bash
# Unit tests (no Podman socket required)
uv run pytest tests/ --ignore=tests/integration/ -v

# Integration tests (requires live Podman + built image + saves/testo/testo.zip)
uv run pytest tests/integration/ -v
```

| Test file | Covers |
|---|---|
| `tests/test_logic.py` | Factorio RCON, container creator, server manager, rebuild |
| `tests/test_satisfactory_logic.py` | Satisfactory container creator, server manager, driver properties |
| `tests/test_tui.py` | Textual widgets, reactive markup, button handlers, refresh, port picker |
| `tests/test_tui_tabs.py` | Tab composition, `GameTab`, cross-driver port utilities, per-tab rebuild |
| `tests/integration/test_rcon_save.py` | End-to-end Factorio save via RCON |

## Adding a New Game Server

See the **Adding a New Game Server Driver** section in [CLAUDE.md](CLAUDE.md) for the full step-by-step guide. The short version:

1. **`src/<game>_container.py`** — container creator (`<game>_container.py`); store ports as container labels
2. **`src/<game>_server.py`** — server manager implementing `GameDriver`; read ports back from labels in `_list_games()`
3. **`src/<game>_image_build.py`** — image builder
4. **`Container-<Game>/Containerfile`** + **`start_server.sh`** — container image definition
5. **Register** the driver in `ControlServer.all_drivers` in `src/server_control.py`
6. **Tests** in `tests/test_<game>_logic.py` modeled on `tests/test_satisfactory_logic.py`
