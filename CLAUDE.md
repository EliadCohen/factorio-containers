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

There is no test suite at this time.

## Architecture

The application has three layers:

1. **TUI Layer** (`src/server_control.py`) — Textual app. `ControlServer` is the main app class. Shows running/stopped containers as toggle switches, lets users create new server instances by picking a save file and port. CSS styles are in `src/control_server.css`. Keyboard shortcuts: `q` quit, `r` refresh.

2. **Game Manager** (`src/factorio_server.py`) — `FactorioServer` wraps the podman API to list, start, stop, and delete containers. Tracks containers with a `factorio-` name prefix. Each container is represented as a `FactorioServer.Game` nested object. Connects to the podman socket at `unix:///run/user/0/podman/podman.sock`.

3. **Container Creator** (`src/factorio_container.py`) — `FactorioGame` handles container creation via the podman API. Uses image `localhost/factorio-headless:latest`, binds saves from `/root/projects/factorio-container/saves/` into the container, and exposes a UDP port (default 34200).

4. **Image Builder** (`src/image_build.py`) — `FactorioContainerImage` builds the container image from `Container/Containerfile`.

### Container Image

`Container/Containerfile` is a multi-stage Fedora-based image that downloads the Factorio headless binary, creates a non-root `factorio` user (UID 1001), and runs `Container/start_server.sh` at startup. The script expects `SAVEFILE` and `PORT` env vars injected by the container creator.

### Saves Directory

Save files must be `.zip` files placed in `/root/projects/factorio-container/saves/`. The TUI uses a directory tree picker to select the save file when creating a new server instance.

## Key Constraints

- Requires Podman (not Docker). The Python API connects to the root podman socket (`/run/user/0/podman/podman.sock`).
- Containers are named with a `factorio-` prefix; this is how the manager identifies managed instances.
- Currently assumes root-level execution for the podman socket path.
