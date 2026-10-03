#!/usr/bin/env bash
# Stable shell entry. Manager/setup run with the administrator's permissions.
set -euo pipefail
PYTHON=/opt/si/app/.venv/bin/python
[[ -x "$PYTHON" ]] || { echo 'SI environment missing; rerun the reviewed installer.' >&2; exit 1; }
case "${1:-}" in
  deploy-check|health|backup|restore)
    module="${1//-/_}"
    shift
    if [[ "$EUID" -eq 0 ]]; then
      exec runuser -u si -- "$PYTHON" -m "evolving_companion.$module" "$@"
    fi
    exec "$PYTHON" -m "evolving_companion.$module" "$@"
    ;;
  *) exec /opt/si/app/.venv/bin/si "$@" ;;
esac
