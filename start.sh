#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"

if [ -x ".venv/bin/python" ]; then
  exec .venv/bin/python app/server.py --open "$@"
else
  exec python3 app/server.py --open "$@"
fi
