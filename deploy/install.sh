#!/usr/bin/env bash
# Foundation installer. No automatic start, update, secret input or data reset.
set -euo pipefail
umask 077

[[ "$(uname -s)" == Linux && "$(uname -m)" == x86_64 ]] || { echo 'Requires Linux x86_64.'; exit 1; }
[[ "$EUID" -eq 0 ]] || { echo 'Run installer with sudo/root; Core runs as si.'; exit 1; }
# Trusted operating-system metadata, not user-controlled configuration.
source /etc/os-release
case "$ID" in
  debian) [[ "${VERSION_ID%%.*}" -ge 12 ]] || exit 1 ;;
  ubuntu) [[ "${VERSION_ID%%.*}" -ge 24 ]] || exit 1 ;;
  *) echo 'Requires Debian 12+ or Ubuntu Server 24.04+.'; exit 1 ;;
esac
for command in git runuser systemctl install getent useradd; do
  command -v "$command" >/dev/null || { echo "Missing required command: $command"; exit 1; }
done
UV="$(command -v uv || true)"
[[ -n "$UV" ]] || { echo 'Install uv from https://docs.astral.sh/uv/getting-started/installation/ in a system-accessible location, then rerun. No remote installer is executed.'; exit 1; }
[[ $# -ge 1 && $# -le 2 ]] || { echo 'Usage: bash deploy/install.sh <credential-free repository URL> [ref]'; exit 1; }
REPOSITORY="$1"
REF="${2:-HEAD}"
case "$REPOSITORY" in
  https://*|ssh://*|git@*) ;;
  *) echo 'Use a credential-free HTTPS or SSH repository URL.'; exit 1 ;;
esac
[[ "$REPOSITORY" != https://*@* && "$REPOSITORY" != ssh://*:*@* && "$REPOSITORY" != *\?* && "$REPOSITORY" != *\#* && "$REF" != -* ]] || { echo 'Invalid repository URL/ref; do not embed credentials.'; exit 1; }
if systemctl is-active --quiet si.service; then
  echo 'Stop si.service before installing/updating its environment.'; exit 1
fi
for path in /opt/si /opt/si/app /opt/si/config /opt/si/runtime /opt/si/logs /opt/si/backups /opt/si/scripts /opt/si/data /opt/si/config/si.env; do
  [[ ! -L "$path" ]] || { echo 'Refusing symlinked deployment targets.'; exit 1; }
done
if ! getent passwd si >/dev/null; then
  useradd --system --user-group --home-dir /opt/si --shell /usr/sbin/nologin si
fi
getent group si >/dev/null || { echo 'Existing si user must have a si group.'; exit 1; }
[[ "$(id -u si)" -ne 0 ]] || { echo 'si must not be root.'; exit 1; }
install -d -o si -g si -m 0750 /opt/si /opt/si/app /opt/si/scripts /opt/si/data
install -d -o si -g si -m 0700 /opt/si/config /opt/si/runtime /opt/si/logs /opt/si/backups
if [[ ! -e /opt/si/app/.git ]]; then
  runuser -u si -- git clone -- "$REPOSITORY" /opt/si/app
  runuser -u si -- git -C /opt/si/app checkout --detach "$REF"
else
  echo 'Existing app checkout preserved; use documented manual update procedure.'
fi
runuser -u si -- env UV_CACHE_DIR=/opt/si/.cache/uv "$UV" sync --project /opt/si/app --locked --no-dev --no-extra local-memory --python 3.12
if [[ ! -e /opt/si/config/si.env ]]; then
  install -o si -g si -m 0600 /opt/si/app/deploy/si.env.example /opt/si/config/si.env
fi
# Never read or print the existing env file; normalize only required permissions.
chown si:si /opt/si/config/si.env
chmod 0600 /opt/si/config/si.env
for script in backup.sh restore.sh si-service.sh; do
  install -o si -g si -m 0750 "/opt/si/app/deploy/$script" "/opt/si/scripts/$script"
done
install -o root -g root -m 0644 /opt/si/app/deploy/systemd/si.service /etc/systemd/system/si.service
systemctl daemon-reload
echo 'Installed; service NOT started/enabled. Configure /opt/si/config/si.env using an editor, then check/start.'
runuser -u si -- /opt/si/app/.venv/bin/python -m evolving_companion.deploy_check
