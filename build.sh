#!/bin/bash
# When need to update server code:
# podman build --no-cache -f Containerfile -t factorio-headless:latest

#When only building for changes
podman build -f Containerfile -t factorio-headless:latest
