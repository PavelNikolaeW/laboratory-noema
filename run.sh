#!/bin/sh
set -eu
cd "$(dirname "$0")"
if [ ! -x .venv/bin/python ]; then
  python3 -m venv .venv
  .venv/bin/pip install -r requirements.lock
fi
if [ ! -f credentials-local.json ]; then
  .venv/bin/python -m lab.admin create > credentials-local.json
  chmod 600 credentials-local.json
fi
printf 'Лаборатория: http://127.0.0.1:8765\nТокен: credentials-local.json (не публикуйте этот файл)\n'
exec .venv/bin/python -m lab.local
