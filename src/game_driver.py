"""Abstract base class for game server drivers."""
from abc import ABC, abstractmethod
from typing import Any


def _all_ports_in_use(drivers: list) -> set[int]:
    """Return the union of all ports used by all drivers."""
    ports: set[int] = set()
    for driver in drivers:
        ports |= driver.get_all_ports()
    return ports


def _next_available_port(base: int, drivers: list) -> int:
    """Return the lowest port >= base not used by any driver."""
    in_use = _all_ports_in_use(drivers)
    port = base
    while port in in_use:
        port += 1
    return port


class GameDriver(ABC):
    """Abstract base class that all game server drivers must implement."""

    @property
    @abstractmethod
    def game_prefix(self) -> str:
        """Container name prefix, e.g. 'factorio-'."""

    @property
    @abstractmethod
    def display_name(self) -> str:
        """Human-readable name for the tab label, e.g. 'Factorio'."""

    @property
    @abstractmethod
    def base_port(self) -> int:
        """Default starting port for new server suggestions."""

    @property
    @abstractmethod
    def image_tag(self) -> str:
        """Podman image tag this driver builds and uses."""

    @property
    @abstractmethod
    def games(self) -> dict[str, Any]:
        """Current dict of full_container_name -> Game-like object."""

    @games.setter
    @abstractmethod
    def games(self, value: dict[str, Any]) -> None:
        """Allow setting the games dict (needed for tests and internal updates)."""

    @abstractmethod
    def update_game_list(self) -> None:
        """Refresh self.games from Podman."""

    @abstractmethod
    def create_game(self, name: str, port: int, **kwargs) -> dict:
        """
        Create and start a new server container.
        Returns dict with at minimum: {"name": str, "port": int}.
        Extra kwargs are driver-specific (e.g. savefile= for Factorio).
        """

    @abstractmethod
    def rebuild_and_recreate(self) -> dict:
        """
        Rebuild the driver's image and recreate all containers.
        Returns {"recreated": list[str], "restarted": list[str]}.
        """

    @abstractmethod
    def get_all_ports(self) -> set[int]:
        """
        Return ALL ports occupied by this driver's containers.
        Used by the cross-driver port conflict detector.
        """

    def supports_player_count(self) -> bool:
        """Whether this driver supports live player count queries (via RCON etc)."""
        return True

    def supports_save_picker(self) -> bool:
        """Whether the creation form should show a save file picker."""
        return True

    def create_tab(self, all_drivers: list) -> Any:
        """
        Return the Textual widget to use as this driver's tab content.

        The default returns a generic ``GameTab``.  Override to return a
        custom widget (e.g. ``SatisfactoryTab`` for the Satisfactory driver).
        """
        from server_control import GameTab
        return GameTab(driver=self, all_drivers=all_drivers)
