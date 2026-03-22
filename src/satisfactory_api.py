"""
satisfactory_api — HTTP API client for the Satisfactory Dedicated Server.

The Satisfactory Dedicated Server exposes an HTTPS API at
``https://<host>:<port>/api/v1/?function=<Name>``.  This module provides a
thin client that wraps the most useful management endpoints.

Authentication is via a long-lived Bearer token generated once in the server
console with ``server.GenerateAPIToken``.  The token is stored in
``satisfactory.token`` in the project root and read at startup.

The server uses a self-signed TLS certificate, so all requests are made with
``verify=False``.  ``urllib3`` warnings are suppressed for cleanliness.
"""
from __future__ import annotations

import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

import requests

TOKEN_FILE = "/root/projects/factorio-container/satisfactory.token"


def load_token() -> str | None:
    """Read the API token from ``satisfactory.token``.

    Returns ``None`` if the file does not exist or is empty.
    """
    try:
        token = open(TOKEN_FILE).read().strip()
        return token or None
    except FileNotFoundError:
        return None


def save_token(token: str) -> None:
    """Write *token* to ``satisfactory.token``."""
    with open(TOKEN_FILE, "w") as f:
        f.write(token.strip())


class SatisfactoryAPIError(Exception):
    """Raised when the Satisfactory API returns an error response."""


class SatisfactoryAPIClient:
    """
    Minimal client for the Satisfactory Dedicated Server HTTPS API.

    Args:
        host: Hostname or IP of the server. Defaults to ``"localhost"``.
        port: API port (same as game port). Defaults to ``7777``.
        token: Bearer token for authentication.
    """

    _BASE = "https://{host}:{port}/api/v1/"

    def __init__(self, host: str = "localhost", port: int = 7777, token: str | None = None):
        self._base = self._BASE.format(host=host, port=port)
        self._token = token
        self._session = requests.Session()
        self._session.verify = False
        if token:
            self._session.headers["Authorization"] = f"Bearer {token}"

    def _post(self, function: str, data: dict | None = None) -> dict:
        """
        POST to ``/api/v1/?function=<function>`` and return the parsed body.

        Raises:
            SatisfactoryAPIError: On HTTP 4xx/5xx or an ``errorCode`` in the
                response body.
            requests.ConnectionError: If the server is unreachable.
        """
        body = {"function": function, "data": data or {}}
        resp = self._session.post(
            self._base,
            params={"function": function},
            json=body,
            timeout=5,
        )
        if not resp.ok:
            raise SatisfactoryAPIError(
                f"{function} failed: HTTP {resp.status_code} — {resp.text[:200]}"
            )
        if resp.status_code == 204:
            return {}
        payload = resp.json()
        if "errorCode" in payload:
            raise SatisfactoryAPIError(
                f"{function} error: {payload.get('errorMessage', payload['errorCode'])}"
            )
        return payload.get("data", {})

    # ── Public API ────────────────────────────────────────────────────────────

    def health_check(self) -> bool:
        """Return ``True`` if the server API is reachable (no auth required)."""
        try:
            self._session.post(
                self._base,
                params={"function": "HealthCheck"},
                json={"function": "HealthCheck", "data": {"clientCustomData": ""}},
                timeout=3,
            )
            return True
        except Exception:
            return False

    def query_server_state(self) -> dict:
        """
        Return the current server game state.

        Returns a dict with keys: ``activeSessionName``, ``numConnectedPlayers``,
        ``playerLimit``, ``isGameRunning``, ``isGamePaused``, ``totalGameDuration``.
        """
        data = self._post("QueryServerState")
        return data.get("serverGameState", {})

    def enumerate_sessions(self) -> list[dict]:
        """
        Return all available saves as a flat list, newest first.

        Each entry is a dict with keys: ``saveName``, ``sessionName``,
        ``saveDateTime``, ``playDurationSeconds``.
        """
        data = self._post("EnumerateSessions")
        saves = []
        for session in data.get("sessions", []):
            session_name = session.get("sessionName", "")
            for header in session.get("saveHeaders", []):
                saves.append({
                    "saveName": header.get("saveName", ""),
                    "sessionName": session_name,
                    "saveDateTime": header.get("saveDateTime", ""),
                    "playDurationSeconds": header.get("playDurationSeconds", 0),
                })
        # Sort newest first
        saves.sort(key=lambda s: s["saveDateTime"], reverse=True)
        return saves

    def load_game(self, save_name: str) -> None:
        """Load the save file named *save_name* on the server."""
        self._post("LoadGame", {"saveName": save_name, "enableAdvancedGameSettings": False})

    def save_game(self, save_name: str) -> None:
        """Save the current game session as *save_name*."""
        self._post("SaveGame", {"saveName": save_name})

    def create_new_game(self, session_name: str) -> None:
        """Start a new game session named *session_name*."""
        self._post("CreateNewGame", {
            "newGameData": {
                "sessionName": session_name,
                "mapName": "",
                "startingLocation": "",
                "bSkipOnboarding": False,
                "advancedGameSettings": {"appliedAdvancedGameSettings": {}},
            }
        })
