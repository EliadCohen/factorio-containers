from podman import PodmanClient

class FactorioContainerImage():
    IMAGE_TAG = "factorio-headless:latest"
    CONTAINER_DIR = "/root/projects/factorio-container/Container/"
    CONTAINERFILE = "/root/projects/factorio-container/Container/Containerfile"
    SOCKET_URI = "unix:///run/user/0/podman/podman.sock"

    def __init__(self, socket_uri: str = SOCKET_URI) -> None:
        self._socket_uri = socket_uri

    def build(self) -> None:
        with PodmanClient(uri=self._socket_uri) as client:
            client.images.build(
                path=self.CONTAINER_DIR,
                dockerfile=self.CONTAINERFILE,
                tag=self.IMAGE_TAG,
            )


def main():
    FactorioContainerImage().build()

if __name__ == '__main__':
    main()
