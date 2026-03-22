"""
satisfactory_image_build — Builds the ``satisfactory-server`` Podman container image.

Two images are built in sequence:

1. ``localhost/steamcmd:latest`` — Fedora base with SteamCMD installed.
   Built from ``Container-Satisfactory/Containerfile.steamcmd``.  Only needs
   to be rebuilt when the OS or SteamCMD itself changes.

2. ``localhost/satisfactory-server:latest`` — the game server image, built
   ``FROM localhost/steamcmd:latest``.  Runs ``app_update 1690800`` to
   download/update the Satisfactory Dedicated Server.  The heavy ~7 GB Steam
   download is cached as a Podman layer after the first build.

Consumed by ``SatisfactoryServer.rebuild_and_recreate()`` and runnable
standalone via ``uv run python src/satisfactory_image_build.py``.
"""
from podman import PodmanClient


class SteamCmdImage:
    """
    Builder for the ``steamcmd`` intermediate base image.

    Installs 32-bit glibc/libstdc++ and SteamCMD on a Fedora base.  This
    layer is shared by all Steam-based game images so it only needs to be
    rebuilt when the OS packages or SteamCMD itself change.
    """

    IMAGE_TAG = "steamcmd:latest"
    CONTAINER_DIR = "/root/projects/factorio-container/Container-Satisfactory/"
    CONTAINERFILE = "/root/projects/factorio-container/Container-Satisfactory/Containerfile.steamcmd"
    SOCKET_URI = "unix:///run/user/0/podman/podman.sock"

    def __init__(self, socket_uri: str = SOCKET_URI) -> None:
        self._socket_uri = socket_uri

    def build(self) -> None:
        """Build ``localhost/steamcmd:latest`` from ``Containerfile.steamcmd``."""
        with PodmanClient(uri=self._socket_uri) as client:
            client.images.build(
                path=self.CONTAINER_DIR,
                dockerfile=self.CONTAINERFILE,
                tag=self.IMAGE_TAG,
            )


class SatisfactoryContainerImage:
    """
    Builder for the ``satisfactory-server`` container image.

    Builds ``FROM localhost/steamcmd:latest``, so ``SteamCmdImage`` must be
    built first.  Calls ``app_update 1690800`` via SteamCMD to install the
    Satisfactory Dedicated Server; the resulting layer is cached by Podman.
    """

    IMAGE_TAG = "satisfactory-server:latest"
    CONTAINER_DIR = "/root/projects/factorio-container/Container-Satisfactory/"
    CONTAINERFILE = "/root/projects/factorio-container/Container-Satisfactory/Containerfile"
    SOCKET_URI = "unix:///run/user/0/podman/podman.sock"

    def __init__(self, socket_uri: str = SOCKET_URI) -> None:
        self._socket_uri = socket_uri

    def build(self) -> None:
        """
        Build ``localhost/satisfactory-server:latest``.

        Builds the ``steamcmd`` base image first, then the satisfactory image
        on top of it.  Both steps use Podman's layer cache, so only changed
        layers are rebuilt.
        """
        SteamCmdImage(self._socket_uri).build()
        with PodmanClient(uri=self._socket_uri) as client:
            client.images.build(
                path=self.CONTAINER_DIR,
                dockerfile=self.CONTAINERFILE,
                tag=self.IMAGE_TAG,
            )


def main():
    print("Step 1/2: Building localhost/steamcmd:latest...")
    SteamCmdImage().build()
    print("Step 2/2: Building localhost/satisfactory-server:latest...")
    SatisfactoryContainerImage().build()
    print("Build complete: localhost/satisfactory-server:latest")


if __name__ == "__main__":
    main()
