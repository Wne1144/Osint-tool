#!/bin/sh
set -eu
cd "$(dirname "$0")"
if ! command -v python3 >/dev/null 2>&1; then
  echo "Python 3 is not installed. This source starter does not bundle a Raspberry Pi runtime." >&2
  exit 1
fi
exec python3 app.py
