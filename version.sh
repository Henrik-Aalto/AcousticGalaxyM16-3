#!/bin/bash
# Erstellt eine neue Subversion (Quellcode-Snapshot) unter versions/
set -e
cd "$(dirname "$0")"
V="v$(date +%Y%m%d-%H%M%S)"
mkdir -p "versions/$V/static"
cp app.py requirements.txt "versions/$V/"
cp -r static/* "versions/$V/static/"
echo "Subversion erstellt: versions/$V"
