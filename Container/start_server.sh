#!/bin/bash
SVFL="${SAVEFILE:-save.zip}"
SVDIR="${SVFL%/*.zip}"
PRT="${PORT:-34197}"
SETTINGS=./data/server-settings.json
ADMINLIST=./data/server-adminlist.json

# TODO use sed to edit a templated (jinja2) settings files for server names and ports etc 
ls -lita ./
ls -lita ./saves/

./bin/x64/factorio --start-server ./saves/$SVFL \
                    --server-settings ./saves/server-settings.json \
                    --server-adminlist $ADMINLIST \
                    --port $PRT
