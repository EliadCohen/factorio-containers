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
# Use a dedicated container name to avoid interfering with live servers
_CONTAINER  = f"factorio-{_SAVE_NAME}-itest"


# ── helpers ──────────────────────────────────────────────────────────────────

def _wait_for_rcon(host, port, password, timeout=90, interval=3):
    """
    Poll until RCON accepts a connection or *timeout* seconds elapse.

    Factorio takes 10–60 seconds to load a save and begin accepting RCON
    connections, depending on the save size.  This helper retries with a
    3-second interval so startup time is not wasted on tight polling.

    Args:
        host (str): RCON host (always ``"127.0.0.1"`` for container with
            ``network_mode="host"``).
        port (int): RCON TCP port (game port + 1000).
        password (str): RCON password set at container creation time.
        timeout (float): Maximum seconds to wait before giving up.
        interval (float): Seconds between connection attempts.

    Returns:
        bool: ``True`` if RCON became reachable within *timeout*, ``False``
            otherwise.
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            # A simple echo command confirms the RCON connection is live
            send_command(host, port, password, "/c rcon.print('ok')", timeout=3)
            return True
        except Exception:
            # Connection refused or timeout — server not yet ready
            time.sleep(interval)
    return False


# ── fixture ──────────────────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def running_game():
    """
    Start a Factorio container for the duration of the module, then delete it.

    Scope is ``"module"`` so the (slow) container startup is shared across all
    tests in this file rather than repeated per-test.

    Steps:
      1. Create a ``FactorioGame`` instance to generate a unique RCON password
         and derive the RCON port (TEST_PORT + 1000).
      2. Call ``create_game()`` — this calls ``podman run`` and returns
         immediately; the server continues starting in the background.
      3. Poll RCON with ``_wait_for_rcon`` for up to 90 seconds.
      4. Yield the RCON credentials dict to the test.
      5. On teardown, call ``_cleanup()`` to stop and remove the container.

    Yields:
        dict: ``{"rcon_port": int, "rcon_password": str}`` for use in tests.

    Fails the test suite (``pytest.fail``) if the server does not become ready
    within 90 seconds, and still cleans up the container before failing.
    """
    game_obj = FactorioGame(name=f"{_SAVE_NAME}-itest", savefile=_SAVE_NAME, port=_TEST_PORT)
    # Capture credentials before create_game() — the RCON port is game port + 1000
    rcon_port     = game_obj.rcon_port      # _TEST_PORT + 1000
    rcon_password = game_obj.rcon_password

    game_obj.create_game()

    # Wait for Factorio to load the save and open the RCON port
    ready = _wait_for_rcon("127.0.0.1", rcon_port, rcon_password, timeout=90)
    if not ready:
        # Clean up even if startup failed to avoid leaving orphaned containers
        _cleanup()
        pytest.fail("Factorio server did not become ready within 90 seconds")

    yield {"rcon_port": rcon_port, "rcon_password": rcon_password}

    # Teardown: always clean up after the module's tests are done
    _cleanup()


def _cleanup():
    """
    Remove the integration-test container if it exists.

    Looks up the container by the known name ``_CONTAINER`` in the live
    Podman state and calls ``delete()`` on it.  All errors are swallowed
    so a failed cleanup never masks a test failure.
    """
    try:
        server = FactorioServer()
        server.update_game_list()
        if _CONTAINER in server.games:
            server.games[_CONTAINER].delete()
    except Exception:
        pass   # best-effort cleanup — do not mask test failures


# ── test ─────────────────────────────────────────────────────────────────────

def test_rcon_save_updates_zip_mtime(running_game):
    """
    After ``/server-save`` the save ``.zip`` mtime must be strictly newer
    than before the command was issued.

    Verification strategy:
      1. Record the current mtime of the save zip.
      2. Send ``/server-save`` via RCON.
      3. Sleep 3 seconds — mirrors the 2-second flush sleep in
         ``FactorioServer._rcon_save()`` with an extra second of margin.
      4. Assert the zip's mtime has increased, proving Factorio wrote the file.

    This test validates the end-to-end RCON → filesystem path that the
    unit test for ``_rcon_save`` cannot cover (unit tests mock the socket).
    """
    mtime_before = _SAVE_ZIP.stat().st_mtime

    send_command(
        "127.0.0.1",
        running_game["rcon_port"],
        running_game["rcon_password"],
        "/server-save",
        timeout=15,
    )
    # Allow Factorio time to flush the save to disk.
    # The 3-second wait matches the flush allowance used in _rcon_save (2s)
    # with an extra second of margin for slower hosts.
    time.sleep(3)   # allow write to flush (mirrors the 2 s sleep in _rcon_save)

    mtime_after = _SAVE_ZIP.stat().st_mtime
    assert mtime_after > mtime_before, (
        f"Save zip mtime unchanged — before {mtime_before}, after {mtime_after}"
    )
