#!/usr/bin/env bash
set -euo pipefail
exec /opt/si/app/.venv/bin/python -m evolving_companion.restore "$@"
