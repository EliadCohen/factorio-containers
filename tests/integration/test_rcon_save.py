"""
Integration test: verifies that a /server-save command issued via RCON
causes the save .zip on the host filesystem to have a newer mtime.

Requirements (not satisfied in CI without a podman socket + built image):
  - /run/user/0/podman/podman.sock must be accessible
  - localhost/factorio-headless:latest must be built  (make build)
  - saves/testo/testo.zip must exist

Run:   uv run pytest tests/integration/ -v
Skip:  uv run pytest tests/ --ignore=tests/integration/
"""
import time
from pathlib import Path

import pytest

from factorio_container import FactorioGame
from factorio_server import FactorioServer
from leanrcon import send_command

# ── constants ────────────────────────────────────────────────────────────────

_SAVE_NAME  = "testo"
_TEST_PORT  = 34599   # high port, unlikely to conflict with live servers
_SAVE_ZIP   = Path(f"/root/projects/factorio-container/saves/{_SAVE_NAME}/{_SAVE_NAME}.zip")
_CONTAINER  = f"factorio-{_SAVE_NAME}-itest"


# ── helpers ──────────────────────────────────────────────────────────────────

def _wait_for_rcon(host, port, password, timeout=90, interval=3):
    """Poll until RCON accepts a connection or *timeout* seconds elapse."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            send_command(host, port, password, "/c rcon.print('ok')", timeout=3)
            return True
        except Exception:
            time.sleep(interval)
    return False


# ── fixture ──────────────────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def running_game():
    """
    Start a Factorio container for the duration of the module, then delete it.
    Yields a dict: {rcon_port, rcon_password}.
    """
    game_obj = FactorioGame(name=f"{_SAVE_NAME}-itest", savefile=_SAVE_NAME, port=_TEST_PORT)
    rcon_port     = game_obj.rcon_port      # _TEST_PORT + 1000
    rcon_password = game_obj.rcon_password

    game_obj.create_game()

    ready = _wait_for_rcon("127.0.0.1", rcon_port, rcon_password, timeout=90)
    if not ready:
        # Clean up even if startup failed
        _cleanup()
        pytest.fail("Factorio server did not become ready within 90 seconds")

    yield {"rcon_port": rcon_port, "rcon_password": rcon_password}

    _cleanup()


def _cleanup():
    try:
        server = FactorioServer()
        server.update_game_list()
        if _CONTAINER in server.games:
            server.games[_CONTAINER].delete()
    except Exception:
        pass   # best-effort cleanup


# ── test ─────────────────────────────────────────────────────────────────────

def test_rcon_save_updates_zip_mtime(running_game):
    """After /server-save the save .zip mtime must be strictly newer than before."""
    mtime_before = _SAVE_ZIP.stat().st_mtime

    send_command(
        "127.0.0.1",
        running_game["rcon_port"],
        running_game["rcon_password"],
        "/server-save",
        timeout=15,
    )
    time.sleep(3)   # allow write to flush (mirrors the 2 s sleep in _rcon_save)

    mtime_after = _SAVE_ZIP.stat().st_mtime
    assert mtime_after > mtime_before, (
        f"Save zip mtime unchanged — before {mtime_before}, after {mtime_after}"
    )
