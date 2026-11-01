#!/bin/sh
# The scheduler uses an absolute path to this file, so no login shell is needed.
set -eu
project_dir=$(CDPATH='' cd -- "$(dirname -- "$0")/.." && pwd)
cd "$project_dir"
if [ -x "$project_dir/.venv/bin/python" ]; then
    python_bin="$project_dir/.venv/bin/python"
else
    python_bin=python3
fi
exec "$python_bin" -m internship_finder --config "$project_dir/config.toml"
