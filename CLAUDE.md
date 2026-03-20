# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

This project runs multiple Factorio headless game server instances as Podman containers on a single host. It provides a TUI (Terminal User Interface) for managing those containers. Designed for home network / local play use cases on Fedora Linux with Podman.

## Development Commands

```bash
# Install dependencies (uses uv)
uv sync

# Run the TUI application
uv run factainer
# or directly:
uv run python src/server_control.py

# Build the container image (with cache)
make build
# or: bash build.sh

# Rebuild container image (no cache)
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
- `tests/test_tui.py` — Textual widget composition, reactive markup, button handlers, refresh logic, port picker
- `tests/integration/test_rcon_save.py` — End-to-end: starts a real container, issues `/server-save`, verifies the `.zip` mtime changes

## Architecture

The application has four layers:

1. **TUI Layer** (`src/server_control.py`) — Textual app. `ControlServer` is the main app class. Shows running/stopped containers as toggle switches with live player counts, lets users create new server instances by picking a save file and port. Includes a "Rebuild & Recreate All" button. CSS styles are in `src/control_server.css`. Keyboard shortcuts: `q` quit, `r` refresh. Auto-refreshes every 5 seconds; the rebuild runs in a background worker thread using `call_from_thread` for TUI safety.

2. **Game Manager** (`src/factorio_server.py`) — `FactorioServer` wraps the Podman API to list, start, stop, and delete containers. Tracks containers with a `factorio-` name prefix. Each container is represented as a `FactorioServer.Game` nested object. Connects to the Podman socket at `unix:///run/user/0/podman/podman.sock`. RCON credentials are stored as container labels at creation time and recovered at list time (label round-trip — no external database).

3. **Container Creator** (`src/factorio_container.py`) — `FactorioGame` handles container creation via the Podman API. Uses image `localhost/factorio-headless:latest`, binds saves from `/root/projects/factorio-container/saves/` into the container with `relabel="Z"` for SELinux, uses `network_mode="host"` (no port mapping needed), and chowns saves to UID 1001. RCON port = game port + 1000.

4. **Image Builder** (`src/image_build.py`) — `FactorioContainerImage` builds the container image from `Container/Containerfile`.

5. **RCON Client** (`src/leanrcon.py`) — Minimal Valve RCON implementation. Used by the game manager to issue `/server-save` before rebuilds and query player counts. Wire format: `[len][req_id][ptype][body\x00\x00]` (little-endian).

### Container Image

`Container/Containerfile` is a multi-stage Fedora-based image that downloads the Factorio headless binary, creates a non-root `factorio` user (UID 1001), and runs `Container/start_server.sh` at startup. The script expects `SAVEFILE` and `PORT` env vars injected by the container creator.

### Saves Directory

Save files must be `.zip` files. Each save gets its own subdirectory matching the zip stem:

```
saves/
├── myworld/
│   ├── myworld.zip
│   └── server-settings.json   ← auto-created from Container/server-settings.json template
└── oldworld/
    ├── oldworld.zip
    └── server-settings.json
```

Files are chowned to UID 1001 before container start so the non-root container user can write autosaves. The TUI uses a filtered directory tree picker (hides autosave zips that start with `_`).

## Key Constraints

- Requires Podman (not Docker). The Python API connects to the root Podman socket (`/run/user/0/podman/podman.sock`).
- Containers are named with a `factorio-` prefix; this is how the manager identifies managed instances.
- Currently assumes root-level execution for the Podman socket path.
- `network_mode="host"` is used — no port mapping; Factorio UDP ports are exposed directly on the host.
- SELinux bind-mount labels (`relabel="Z"`) are required on Fedora; removing this will cause permission errors.
- RCON port is always game port + 1000 (hardcoded convention).
- The 2-second sleep in `_rcon_save` is intentional — Factorio acknowledges `/server-save` before the file write completes.
