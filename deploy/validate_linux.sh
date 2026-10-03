#!/usr/bin/env bash
# Safe offline checks: no Core start, API requests, QQ login or real DB mutation.
set -euo pipefail
[[ "$(uname -s)" == Linux && "$EUID" -eq 0 ]] || { echo 'Run on Linux with sudo/root.' >&2; exit 1; }
[[ -d /run/systemd/system ]] || { echo 'A running systemd host is required.' >&2; exit 1; }
source /etc/os-release
case "$ID" in
  ubuntu) [[ "${VERSION_ID%%.*}" -ge 24 ]] ;;
  debian) [[ "${VERSION_ID%%.*}" -ge 12 ]] ;;
  *) echo 'Unsupported OS.' >&2; exit 1 ;;
esac
[[ "$(uname -m)" == x86_64 ]]
uv --version
systemd-analyze verify /etc/systemd/system/si.service
systemctl show si.service --property=ActiveState --property=User --property=Group
runuser -u si -- /opt/si/app/.venv/bin/python -m evolving_companion.linux_validation
echo 'Offline structural validation passed. Manager interaction, restore/reboot acceptance remain manual; API/QQ were not tested.'
