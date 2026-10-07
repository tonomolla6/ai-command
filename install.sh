#!/bin/sh
set -eu
task_source_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
for task_python in python3 python3.14 python3.13 python3.12 python3.11; do
    if command -v "$task_python" >/dev/null 2>&1 && "$task_python" -c 'import sys;sys.exit(sys.version_info < (3,11))'; then
        exec "$task_python" "$task_source_dir/install.py" "$@"
    fi
done
echo 'AI Command requiere Python 3.11 o posterior.' >&2
exit 1
