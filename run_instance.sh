#!/bin/bash
SVDR="${SAVEDIR:-./saves/monomono}"
PRT="${PORT:-34197}"
# SETTINGS=./data/server-settings.json
ADMINLIST=./data/server-adminlist.json
SVNM=$(basename "$SVDR").zip
# Use it like the following example:
# SAVEFILE=./saves/monomono.zip PORT=34200 ./run_instance.sh

podman run -d -p $PRT:$PRT/udp  --name factorio-$(basename "$SVDR") -v $SVDR/:/home/factorio/factorio/saves/:Z -e SAVEFILE="$SVNM" -e PORT=$PRT --replace factorio-headless:latest