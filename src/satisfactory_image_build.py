"""
satisfactory_image_build — Builds the ``satisfactory-server`` Podman container image.

Wraps the Podman Python API's ``images.build()`` call to produce the image
tagged ``localhost/satisfactory-server:latest`` from
``Container-Satisfactory/Containerfile``.

Consumed by ``SatisfactoryServer.rebuild_and_recreate()`` when the user
requests an in-place image update (e.g. to pick up a new Satisfactory release
via SteamCMD).  Can also be run standalone via
``uv run python src/satisfactory_image_build.py``.
"""
from podman import PodmanClient


class SatisfactoryContainerImage:
    """
    Builder for the ``satisfactory-server`` container image.

    Calls ``podman build`` against the ``Container-Satisfactory/`` directory
    using the Podman Python SDK.  No Docker compatibility layer is required.

    Class attributes:
        IMAGE_TAG (str): Tag applied to the built image
            (``"satisfactory-server:latest"``).
        CONTAINER_DIR (str): Build context directory passed to Podman.
        CONTAINERFILE (str): Absolute path to the Containerfile used as the
            build recipe (equivalent to ``--file`` on the CLI).
        SOCKET_URI (str): Podman socket URI — always the root socket on this
            host.
    """

    IMAGE_TAG = "satisfactory-server:latest"
    CONTAINER_DIR = "/root/projects/factorio-container/Container-Satisfactory/"
    CONTAINERFILE = "/root/projects/factorio-container/Container-Satisfactory/Containerfile"
    SOCKET_URI = "unix:///run/user/0/podman/podman.sock"

    def __init__(self, socket_uri: str = SOCKET_URI) -> None:
        """
        Initialise the builder.

        Args:
            socket_uri: Podman socket URI to connect to.  Defaults to the
                root socket at ``unix:///run/user/0/podman/podman.sock``.
                Override for testing or non-root usage.
        """
        self._socket_uri = socket_uri

    def build(self) -> None:
        """
        Build the ``satisfactory-server`` image from
        ``Container-Satisfactory/Containerfile``.

        Connects to the Podman socket, runs a build using the full
        ``Container-Satisfactory/`` directory as the build context, and tags
        the resulting image as ``satisfactory-server:latest``.

        The first build downloads the Satisfactory Dedicated Server via
        SteamCMD (~7 GB); subsequent builds reuse cached layers unless the
        Containerfile changes.

        Side effects:
            Produces or replaces the ``localhost/satisfactory-server:latest``
            image in the local Podman image store.  Any previous image with
            that tag is superseded (old layers become dangling; run
            ``make clean`` to prune them).

        Raises:
            podman.errors.APIError: If the Podman daemon rejects the request or
                the build fails (e.g. network error during ``RUN`` steps).
        """
        with PodmanClient(uri=self._socket_uri) as client:
            client.images.build(
                path=self.CONTAINER_DIR,
                dockerfile=self.CONTAINERFILE,
                tag=self.IMAGE_TAG,
            )


def main():
    SatisfactoryContainerImage().build()


if __name__ == "__main__":
    main()
