"""
satisfactory_api — HTTP API client for the Satisfactory Dedicated Server.

The Satisfactory Dedicated Server exposes an HTTPS API at
``https://<host>:<port>/api/v1/?function=<Name>``.  This module provides a
thin client that wraps the most useful management endpoints.

Authentication flow
-------------------
A fresh server has no admin password and must be *claimed* before it is usable:

1. ``passwordless_login()`` — obtain an initial ``InitialAdmin`` token.
2. ``claim_server(name, password, token)`` — set the server name and (optional)
   admin password.  Returns a new auth token.
3. ``generate_api_token(token)`` — mint a long-lived API token and write it to
   ``satisfactory.token``.

For an already-claimed server that has no local token file:

1. ``password_login(password)`` — authenticate with the admin password.
2. ``generate_api_token(token)`` — same as above.

The saved API token is then passed to ``SatisfactoryAPIClient`` on every
subsequent startup so the TUI can talk to the server directly.

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
        token: Bearer token for authentication.  ``None`` for unauthenticated
               access (used during the initial setup / claim flow).
    """

    _BASE = "https://{host}:{port}/api/v1/"

    def __init__(self, host: str = "localhost", port: int = 7777, token: str | None = None):
        self._base = self._BASE.format(host=host, port=port)
        self._token = token
        self._session = requests.Session()
        self._session.verify = False
        if token:
            self._session.headers["Authorization"] = f"Bearer {token}"

    def _post(self, function: str, data: dict | None = None, *,
              auth_token: str | None = None) -> dict:
        """
        POST to ``/api/v1/?function=<function>`` and return the parsed body.

        Args:
            function: API function name.
            data: Request payload (sent as ``{"data": <data>}``).
            auth_token: If provided, use this token for the ``Authorization``
                        header instead of the stored session token.  Pass an
                        empty string to send no ``Authorization`` header at all
                        (needed for ``PasswordlessLogin`` / ``PasswordLogin``
                        when the session already carries a stale token).

        Raises:
            SatisfactoryAPIError: On HTTP 4xx/5xx or an ``errorCode`` in the
                response body.
            requests.ConnectionError: If the server is unreachable.
        """
        per_request_headers: dict = {}
        if auth_token is not None:
            if auth_token:
                per_request_headers["Authorization"] = f"Bearer {auth_token}"
            else:
                # Explicitly suppress any session-level Authorization header.
                per_request_headers["Authorization"] = None  # type: ignore[assignment]

        resp = self._session.post(
            self._base,
            params={"function": function},
            headers=per_request_headers or None,
            json={"function": function, "data": data or {}},
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

    # ── Status (no auth required) ─────────────────────────────────────────────

    def health_check(self) -> bool:
        """Return ``True`` if the server API is reachable (no auth required)."""
        try:
            resp = self._session.post(
                self._base,
                params={"function": "HealthCheck"},
                json={"function": "HealthCheck", "data": {"clientCustomData": ""}},
                timeout=3,
            )
            return resp.status_code == 200
        except Exception:
            return False

    def get_server_name(self) -> str:
        """
        Return the server's custom name from ``HealthCheck``.

        Returns an empty string if the server is unreachable or unclaimed.
        No auth required.
        """
        try:
            resp = self._session.post(
                self._base,
                params={"function": "HealthCheck"},
                json={"function": "HealthCheck", "data": {"clientCustomData": ""}},
                timeout=3,
            )
            if resp.status_code == 200:
                return resp.json().get("data", {}).get("serverCustomName", "")
        except Exception:
            pass
        return ""

    # ── Game state (requires valid API token) ─────────────────────────────────

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

    # ── Setup / auth flow (unauthenticated or temp-token) ─────────────────────

    def passwordless_login(self) -> str:
        """
        Obtain an ``InitialAdmin`` token without a password.

        Works on unclaimed servers and on servers where no admin password has
        been set.  Raises ``SatisfactoryAPIError`` if the server requires a
        password.

        Returns:
            A temporary auth token suitable for ``claim_server()`` or
            ``generate_api_token()``.
        """
        data = self._post(
            "PasswordlessLogin",
            {"MinimumPrivilegeLevel": "InitialAdmin"},
            auth_token="",
        )
        return data["authenticationToken"]

    def password_login(self, password: str) -> str:
        """
        Authenticate with the server admin password.

        Returns:
            A temporary auth token suitable for ``generate_api_token()``.

        Raises:
            SatisfactoryAPIError: On wrong password or other API errors.
        """
        data = self._post(
            "PasswordLogin",
            {"MinimumPrivilegeLevel": "InitialAdmin", "Password": password},
            auth_token="",
        )
        return data["authenticationToken"]

    def claim_server(self, server_name: str, admin_password: str,
                     auth_token: str) -> str:
        """
        Claim an unclaimed server: set its display name and admin password.

        Args:
            server_name: The name shown in the Satisfactory server browser.
            admin_password: Admin password (empty string for no password).
            auth_token: The token returned by ``passwordless_login()``.

        Returns:
            A new auth token (the original ``auth_token`` is invalidated).

        Raises:
            SatisfactoryAPIError: If the server is already claimed.
        """
        data = self._post(
            "ClaimServer",
            {"serverName": server_name, "adminPassword": admin_password},
            auth_token=auth_token,
        )
        return data["authenticationToken"]

    def generate_api_token(self, auth_token: str) -> str:
        """
        Generate a long-lived API token.

        Args:
            auth_token: An ``InitialAdmin``-level token from ``passwordless_login()``,
                        ``password_login()``, or ``claim_server()``.

        Returns:
            The long-lived API token string (also write it with ``save_token()``).
        """
        data = self._post("GenerateAPIToken", {}, auth_token=auth_token)
        return data["token"]
