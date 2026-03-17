import os
from podman import PodmanClient

class FactorioContainerImage():
    def __init__(self, file, socket_uri = "unix:///run/user/0/podman/podman.sock") -> None:
        client = PodmanClient(uri = socket_uri)
        # fileobj = open(file)
        # os.chdir("/root/projects/factorio-container")
        image = client.images.build(path="/root/projects/factorio-container/Container/", dockerfile="/root/projects/factorio-container/Container/Containerfile", tag="tested:latest")

def main():
    result = FactorioContainerImage(file="Containerfile")

if __name__ == '__main__':
    main()