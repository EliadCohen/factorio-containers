"""
Integration-test conftest.  Runs AFTER tests/conftest.py, which installs a
podman stub.  We remove that stub here so imports in integration tests get the
real podman client.
"""
import sys
sys.modules.pop("podman", None)
