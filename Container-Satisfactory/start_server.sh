#!/bin/bash
# Start the Satisfactory dedicated server.
# PORT and BEACON_PORT are injected as environment variables by the container creator.
exec /home/steam/SatisfactoryDedicatedServer/FactoryServer.sh \
    -Port="${PORT:-7777}" \
    -BeaconPort="${BEACON_PORT:-8888}" \
    -log \
    -unattended
