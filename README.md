# factorio-container

Run multiple Factorio headless server instances as Podman containers on a single Fedora host, managed through a terminal UI.

## Features

- **Multi-server management** — run any number of Factorio servers simultaneously on different ports
- **Terminal UI (TUI)** — toggle servers on/off, view real-time player counts, create new instances, all from the terminal
- **Live player count** — queries each running server via RCON every 5 seconds
- **One-click rebuild** — rebuild the container image and recreate all servers while preserving run/stop state and gracefully saving games first
- **Save file picker** — browse the `saves/` directory tree to select a save when launching a new server
- **Auto port selection** — suggests the next available port when creating a new server
- **RCON integration** — ephemeral per-container RCON passwords, used for saves and player queries
- **SELinux compatible** — bind mounts use `relabel="Z"` for Fedora/SELinux hosts

## Requirements

- **Fedora Linux** (tested; other SELinux-enabled distros may work)
- **Podman** — must be running as root; the app connects to `/run/user/0/podman/podman.sock`
- **Python 3.11+** with [uv](https://github.com/astral-sh/uv) for dependency management
- **Built container image** — `localhost/factorio-headless:latest` (see Quick Start)
- **Save files** — `.zip` save files placed under `saves/<name>/`

## Quick Start

```bash
# 1. Clone the repository
git clone <repo-url>
cd factorio-container

# 2. Install Python dependencies
uv sync

# 3. Build the container image (downloads Factorio headless binary)
make build

# 4. Place your save files
mkdir -p saves/myworld
cp /path/to/myworld.zip saves/myworld/

# 5. Launch the TUI
uv run factainer
```

## TUI Overview

```
╔══════════════════════════════════════════════════════════════╗
║  factainer — Factorio Server Manager       q quit  r refresh ║
╠══════════════════════════════════════════════════════════════╣
║                                                              ║
║  myworld    :34197  ● Online    2 players  ◉ ON  [Delete]   ║
║  oldworld   :34198  ○ Offline   -          ○ OFF [Delete]   ║
║                                                              ║
╠══════════════════════════════════════════════════════════════╣
║  [Rebuild & Recreate All]                                    ║
╠══════════════════════════════════════════════════════════════╣
║  ▾ saves/                        Selected: myworld.zip       ║
║    ▾ myworld/                    Port: [34199              ] ║
║        myworld.zip                                           ║
║    ▾ oldworld/                   [Run server]                ║
║        oldworld.zip                                          ║
╚══════════════════════════════════════════════════════════════╝
```

### Server list (top section)

Each row represents one managed container:

| Column | Description |
|---|---|
| Name | Server name (container name without `factorio-` prefix) |
| Port | UDP game port clients connect to |
| Status | `Online` (orange) or `Offline` (red) |
| Players | Number of connected players, or `-` if server is stopped |
| Switch | Toggle to start (`ON`) or stop (`OFF`) the container |
| Delete | Stop and remove the container permanently |

### Keyboard shortcuts

| Key | Action |
|---|---|
| `q` | Quit the application |
| `r` | Manually refresh the server list |

The list also refreshes automatically every 5 seconds.

### Rebuild & Recreate All

Clicking this button:
1. Sends `/server-save` via RCON to every running server and waits 2 seconds for the file to flush
2. Rebuilds the `factorio-headless` container image (picks up new Factorio versions)
3. Deletes all containers
4. Recreates them with the new image, using the same names, ports, and save files
5. Stops any containers that were stopped before the rebuild

The operation runs in a background thread so the TUI stays responsive during the build.

### New server (bottom section)

1. Click a `.zip` file in the directory tree to select a save
2. Adjust the port if needed (defaults to the next free port)
3. Click **Run server**

## Architecture

```
┌─────────────────────────────────────────────┐
│  server_control.py  (TUI layer)             │
│  ControlServer / ServerEntry / NewServer    │
│  UpdateSection / FilteredDirectoryTree      │
└──────────────┬──────────────────────────────┘
               │ calls
┌──────────────▼──────────────────────────────┐
│  factorio_server.py  (Game Manager)         │
│  FactorioServer — list, start, stop, delete │
│  FactorioServer.Game — single container     │
└──────────┬───────────────────┬──────────────┘
           │ create            │ RCON queries
┌──────────▼──────────┐  ┌────▼─────────────┐
│ factorio_container  │  │  leanrcon.py      │
│ FactorioGame        │  │  RCONClient       │
│ podman run + mounts │  │  send_command     │
└─────────────────────┘  └──────────────────┘
           │ rebuild
┌──────────▼──────────┐
│  image_build.py     │
│  FactorioContainer  │
│  Image — podman     │
│  images.build()     │
└─────────────────────┘
```

### Layer descriptions

**TUI Layer** (`src/server_control.py`)
Textual application. `ControlServer` is the root `App` class. Renders server
entries as `ServerEntry` (toggle switch + player count), houses the rebuild
button in `UpdateSection`, and the save picker + port input in `NewServer`.
CSS styles live in `src/control_server.css`. Auto-refreshes every 5 seconds;
the rebuild runs in a worker thread with `call_from_thread` for TUI safety.

**Game Manager** (`src/factorio_server.py`)
`FactorioServer` wraps the Podman Python API to list, start, stop, and delete
containers. Identifies managed containers by the `factorio-` name prefix.
Each container is represented as a `FactorioServer.Game` object. RCON
credentials are round-tripped through container labels — stored at creation
time by `FactorioGame`, recovered at list time by `_list_games()`.

**Container Creator** (`src/factorio_container.py`)
`FactorioGame` handles container creation via `podman run`. Uses
`localhost/factorio-headless:latest`, binds saves from the host's `saves/`
directory, uses `network_mode="host"` (no port mapping needed), and applies
`relabel="Z"` for SELinux compatibility. RCON port = game port + 1000.

**RCON Client** (`src/leanrcon.py`)
Minimal Valve RCON implementation. Supports connect/auth/send/recv. Used to
issue `/server-save` before rebuilds and `/c rcon.print(#game.connected_players)`
for player counts.

**Image Builder** (`src/image_build.py`)
`FactorioContainerImage` builds the image from `Container/Containerfile`.
Called by `FactorioServer.rebuild_and_recreate()`.

## Saves Directory Layout

```
saves/
├── myworld/
│   ├── myworld.zip          ← main save file
│   └── server-settings.json ← auto-created from template on first run
└── oldworld/
    ├── oldworld.zip
    └── server-settings.json
```

Each server gets its own subdirectory. The subdirectory name must match the
`.zip` filename stem. `server-settings.json` is created automatically from
`Container/server-settings.json` (the template) the first time a server is
started; the `"name"` field is updated to match the server name.

Files are `chown`-ed to UID 1001 (the `factorio` user inside the container)
before the container starts so the non-root process can write autosaves.

## Container Image

`Container/Containerfile` is a multi-stage Fedora-based image that:
1. Downloads the Factorio headless binary from factorio.com
2. Creates a non-root `factorio` user (UID 1001)
3. Runs `Container/start_server.sh` at startup, which expects `SAVEFILE` and
   `PORT` environment variables (injected by the container creator)

## Development

```bash
# Install dependencies
uv sync

# Run the TUI
uv run factainer
# or:
uv run python src/server_control.py

# Build the container image (with layer cache)
make build
# or: bash build.sh

# Rebuild container image (no cache — picks up new Factorio release)
make update

# Remove dangling Podman images after rebuilds
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

### Test structure

| Path | What it tests |
|---|---|
| `tests/test_logic.py` | RCON packet encoding, `FactorioGame` init, server label parsing, `_rcon_save`, name uniqueness, `rebuild_and_recreate`, player count |
| `tests/test_tui.py` | Textual widget composition, reactive markup, button handlers, refresh logic, port picker |
| `tests/integration/test_rcon_save.py` | End-to-end: starts a real container, issues `/server-save`, verifies the `.zip` mtime changes |

The `podman` package is globally stubbed in `tests/conftest.py` so unit and
TUI tests run without a live Podman socket.
