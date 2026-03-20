"""
Pytest configuration shared by all test modules.

Sets up sys.path and stubs out the ``podman`` package so tests can import
application modules without a live podman socket.

Why the podman stub is necessary
---------------------------------
``FactorioServer.__init__`` calls ``PodmanClient(uri=...)`` at construction
time, and ``FactorioGame`` imports ``PodmanClient`` at module load time.  If
the ``podman`` package is importable but the socket is absent (normal in CI
and on developer machines that have not started the Podman service), the
import itself succeeds but any ``PodmanClient()`` call raises a connection
error.

We replace the entire ``podman`` module in ``sys.modules`` with a
``MagicMock`` *before* any test module imports application code.  This means:

  - ``from podman import PodmanClient`` in application modules resolves to
    ``MagicMock().PodmanClient``, which is itself a ``MagicMock``.
  - All calls to ``PodmanClient(...)`` and its methods return further
    ``MagicMock`` objects silently.
  - Individual tests can refine this behaviour with ``patch("...PodmanClient")``
    when they need to assert specific call arguments or return values.

How it interacts with integration tests
----------------------------------------
Tests in ``tests/integration/`` are intentionally *not* covered by this stub.
They have their own ``sys.path`` setup (via the same conftest, which also
applies to integration tests) but do **not** mock podman — they require a real
Podman socket and a running Factorio container.  Integration tests should be
run separately::

    uv run pytest tests/integration/ -v

and excluded from the regular test run::

    uv run pytest tests/ --ignore=tests/integration/
"""
import sys
from pathlib import Path
from unittest.mock import MagicMock

# Make src/ importable so test modules can do ``from factorio_server import ...``
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

# Stub the podman package before any test module imports it.
# ``setdefault`` is used (rather than direct assignment) so that if podman is
# already stubbed by a prior conftest or plugin it is not replaced again.
_mock_podman = MagicMock()
sys.modules.setdefault("podman", _mock_podman)
