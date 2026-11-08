#!/bin/sh
set -eu
root=$(CDPATH= cd -- "$(dirname "$0")" && pwd)
"$root/install-dependencies.sh"
exec python3 "$root/build.py"
