#!/usr/bin/env bash
set -euo pipefail
[[ $# -eq 1 ]] || { echo 'Usage: si-service.sh start|stop|restart|status|logs'; exit 1; }
case "$1" in
  start|stop|restart|status) exec systemctl "$1" si.service ;;
  logs) exec journalctl -u si.service -f ;;
  *) echo 'Unknown service command.'; exit 1 ;;
esac
