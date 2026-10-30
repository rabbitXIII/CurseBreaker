#!/bin/sh
# Runs CurseBreaker from source inside a WoW client directory: ./cb.sh [command] [args...]
set -eu

REPO=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)
WOW_DIR=${CURSEBREAKER_WOW_DIR:-/Applications/World of Warcraft/_forever_}
if [ ! -d "$WOW_DIR" ]; then
    echo "cb: WoW client directory not found: $WOW_DIR (set CURSEBREAKER_WOW_DIR)" >&2
    exit 1
fi

if command -v uv >/dev/null 2>&1; then
    cd "$WOW_DIR"
    exec uv run --project "$REPO" python "$REPO/CurseBreaker.py" "$@"
fi

is_supported() {
    "$1" -c 'import sys; sys.exit(sys.version_info < (3, 14))' >/dev/null 2>&1
}

VENV=$REPO/.venv
STAMP=$VENV/.cb-pyproject
if ! is_supported "$VENV/bin/python"; then
    PYTHON=
    for candidate in python3.14 python3; do
        if command -v "$candidate" >/dev/null 2>&1 && is_supported "$candidate"; then
            PYTHON=$candidate
            break
        fi
    done
    if [ -z "$PYTHON" ]; then
        echo 'cb: Python 3.14 or newer is required (python3.14 or python3 on PATH), or install uv.' >&2
        exit 1
    fi
    "$PYTHON" -m venv --clear "$VENV"
fi
if ! cmp -s "$REPO/pyproject.toml" "$STAMP"; then
    "$VENV/bin/python" -m pip --version >/dev/null 2>&1 || "$VENV/bin/python" -m ensurepip -q
    "$VENV/bin/python" -I -c 'import sys, tomllib
print("\n".join(tomllib.load(open(sys.argv[1], "rb"))["project"]["dependencies"]))' \
        "$REPO/pyproject.toml" > "$VENV/cb-requirements.txt"
    "$VENV/bin/python" -m pip install -q --disable-pip-version-check -r "$VENV/cb-requirements.txt"
    cp "$REPO/pyproject.toml" "$STAMP"
fi
cd "$WOW_DIR"
exec "$VENV/bin/python" "$REPO/CurseBreaker.py" "$@"
