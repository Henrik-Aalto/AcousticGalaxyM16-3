#!/bin/sh
# Startet den ExApp-Prozess. AppAPI setzt APP_HOST/APP_PORT; nc_py_api liest
# diese Variablen selbst aus.
set -e
exec "$@"
