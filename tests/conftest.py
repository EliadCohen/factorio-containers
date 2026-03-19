"""
Pytest configuration shared by all test modules.

Sets up sys.path and stubs out the `podman` package so tests can import
application modules without a live podman socket.
"""
import sys
from pathlib import Path
from unittest.mock import MagicMock

# Make src/ importable
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

# Stub the podman package before any test module imports it.
# FactorioServer and FactorioGame both call PodmanClient() at class/init time;
# this prevents failures when the podman socket is unavailable.
_mock_podman = MagicMock()
sys.modules.setdefault("podman", _mock_podman)
