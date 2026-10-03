#!/usr/bin/env bash
# Review locally before running. No service start, secrets input or runtime reset.
set -Eeuo pipefail
umask 077
STEP=preflight
trap 'printf "Install failed during %s (line %s). Fix the reported cause and rerun; service was not started.\n" "$STEP" "$LINENO" >&2' ERR
fail() { printf '%s\n' "$1" >&2; return 1; }

[[ "$(uname -s)" == Linux && "$(uname -m)" == x86_64 ]] || fail 'Requires Linux x86_64.'
[[ "$EUID" -eq 0 ]] || fail 'Run with sudo/root; Core runs as si.'
source /etc/os-release
case "$ID" in
  ubuntu) [[ "${VERSION_ID%%.*}" -ge 24 ]] || fail 'Requires Ubuntu 24.04+.' ;;
  debian) [[ "${VERSION_ID%%.*}" -ge 12 ]] || fail 'Requires Debian 12+.' ;;
  *) fail 'Requires Ubuntu 24.04+ or Debian 12+.' ;;
esac
[[ -d /run/systemd/system ]] || fail 'Requires a running systemd host.'
[[ $# -le 2 ]] || fail 'Usage: sudo bash deploy/install.sh [credential-free repository URL [ref]]'
SOURCE="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"
REPOSITORY="${1:-}"
REF="${2:-HEAD}"
if [[ -n "$REPOSITORY" ]]; then
  case "$REPOSITORY" in https://*|ssh://*|git@*) ;; *) fail 'Use a credential-free HTTPS/SSH URL.' ;; esac
  [[ "$REPOSITORY" != https://*@* && "$REPOSITORY" != ssh://*:*@* && "$REPOSITORY" != *\?* && "$REPOSITORY" != *\#* && "$REF" != -* ]] || fail 'Invalid URL/ref; never embed credentials.'
fi
for path in /opt/si /opt/si/app /opt/si/config /opt/si/runtime /opt/si/logs /opt/si/backups /opt/si/scripts /opt/si/data /opt/si/config/si.env /usr/local/bin/si /etc/systemd/system/si.service; do
  [[ ! -L "$path" ]] || fail 'Refusing symlinked deployment targets.'
done
# list-units succeeds for a fresh host with no si unit; bus failures still stop.
LOADED="$(systemctl list-units --all --no-legend --plain si.service)"
STATE=inactive
if [[ -n "$LOADED" ]]; then STATE="$(systemctl show si.service --property=ActiveState --value)"; fi
case "$STATE" in inactive|failed|'') ;; *) fail 'Stop si.service before installation.' ;; esac

STEP='system dependencies'
apt-get update
DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends ca-certificates curl git sqlite3

STEP='si account and directories'
getent group si >/dev/null || groupadd --system si
if ! getent passwd si >/dev/null; then
  useradd --system --gid si --home-dir /opt/si --shell /usr/sbin/nologin si
fi
[[ "$(id -u si)" -ne 0 && "$(id -g si)" == "$(getent group si | cut -d: -f3)" ]] || fail 'Existing si must be non-root with primary group si.'
install -d -o si -g si -m 0750 /opt/si /opt/si/app /opt/si/scripts /opt/si/data
install -d -o si -g si -m 0700 /opt/si/config /opt/si/runtime /opt/si/logs /opt/si/backups
for path in /opt/si/data/python /opt/si/data/uv-cache; do
  [[ ! -L "$path" ]] || fail 'Refusing symlinked uv storage.'
  install -d -o si -g si -m 0750 "$path"
done

STEP='uv'
UV="$(command -v uv || true)"
if [[ -z "$UV" ]]; then
  # Official prebuilt artifact, fixed version and published SHA256. No remote shell.
  [[ ! -L /usr/local/bin/uv ]] || fail 'Refusing symlinked uv install target.'
  DOWNLOAD="$(mktemp -d)"
  trap 'rm -f -- "$DOWNLOAD/uv.tar.gz" "$DOWNLOAD/uv-x86_64-unknown-linux-gnu/uv"; rmdir -- "$DOWNLOAD/uv-x86_64-unknown-linux-gnu" "$DOWNLOAD" 2>/dev/null || true' EXIT
  curl --proto '=https' --proto-redir '=https' --tlsv1.2 -fL \
    https://releases.astral.sh/github/uv/releases/download/0.12.22/uv-x86_64-unknown-linux-gnu.tar.gz \
    -o "$DOWNLOAD/uv.tar.gz"
  printf 'b9980552309f09c15172b8be828555e375097f16deb459795ce7bfd200380f0b  %s\n' "$DOWNLOAD/uv.tar.gz" | sha256sum -c -
  tar -xzf "$DOWNLOAD/uv.tar.gz" -C "$DOWNLOAD" uv-x86_64-unknown-linux-gnu/uv
  install -o root -g root -m 0755 "$DOWNLOAD/uv-x86_64-unknown-linux-gnu/uv" /usr/local/bin/uv
  UV=/usr/local/bin/uv
fi
runuser -u si -- "$UV" --version

STEP='application source'
if [[ -f /opt/si/app/pyproject.toml ]]; then
  echo 'Existing app preserved; installer does not pull/reset. Use the documented update flow.'
else
  [[ -z "$(find /opt/si/app -mindepth 1 -maxdepth 1 -print -quit)" ]] || fail 'App directory is nonempty/incomplete; inspect it before retrying.'
  if [[ -n "$REPOSITORY" ]]; then
    git clone -- "$REPOSITORY" /opt/si/app
    git -C /opt/si/app checkout --detach "$REF"
  elif [[ -d "$SOURCE/.git" ]]; then
    [[ -z "$(git -c safe.directory="$SOURCE" -C "$SOURCE" status --porcelain)" ]] || fail 'Source checkout is dirty; review and commit/release before deployment.'
    REVISION="$(git -c safe.directory="$SOURCE" -C "$SOURCE" rev-parse HEAD)"
    ORIGIN="$(git -c safe.directory="$SOURCE" -C "$SOURCE" remote get-url origin || true)"
    [[ "$ORIGIN" != https://*@* && "$ORIGIN" != *\?* && "$ORIGIN" != *\#* && "$ORIGIN" != ssh://*:*@* ]] || fail 'Origin must not contain credentials.'
    git -c safe.directory="$SOURCE" clone --no-local -- "$SOURCE" /opt/si/app
    git -C /opt/si/app checkout --detach "$REVISION"
    if [[ -n "$ORIGIN" ]]; then git -C /opt/si/app remote set-url origin "$ORIGIN"; fi
  else
    # Reviewed release archive: copy only application assets, never local runtime/secrets.
    for item in pyproject.toml uv.lock README.md src data deploy; do
      [[ -e "$SOURCE/$item" ]] || fail "Release source missing $item."
      cp -a -- "$SOURCE/$item" /opt/si/app/
    done
  fi
  chown -R si:si /opt/si/app
fi
[[ -f /opt/si/app/uv.lock ]] || fail 'App lockfile is missing.'

STEP='locked API-only environment'
runuser -u si -- env HOME=/opt/si UV_CACHE_DIR=/opt/si/data/uv-cache \
  UV_PYTHON_INSTALL_DIR=/opt/si/data/python UV_CONCURRENT_BUILDS=1 \
  UV_CONCURRENT_INSTALLS=1 UV_CONCURRENT_DOWNLOADS=2 \
  "$UV" sync --project /opt/si/app --locked --no-dev --no-extra local-memory --python 3.12

STEP='configuration and launchers'
if [[ ! -e /opt/si/config/si.env ]]; then
  install -o si -g si -m 0600 /opt/si/app/deploy/si.env.example /opt/si/config/si.env
fi
# Existing values remain untouched; no secrets are read/printed by shell.
chown si:si /opt/si/config/si.env
chmod 0600 /opt/si/config/si.env
for script in backup.sh restore.sh si-service.sh validate_linux.sh; do
  install -o si -g si -m 0750 "/opt/si/app/deploy/$script" "/opt/si/scripts/$script"
done
install -o root -g root -m 0755 /opt/si/app/deploy/si.sh /usr/local/bin/si

STEP='systemd unit'
systemd-analyze verify /opt/si/app/deploy/systemd/si.service
install -o root -g root -m 0644 /opt/si/app/deploy/systemd/si.service /etc/systemd/system/si.service
systemctl daemon-reload

STEP='offline deployment check'
runuser -u si -- /opt/si/app/.venv/bin/python -m evolving_companion.deploy_check --offline
STEP='installation permissions and temporary backup/restore validation'
runuser -u si -- /opt/si/app/.venv/bin/python -m evolving_companion.linux_validation
echo 'Installed; service NOT started/enabled. Missing keys/routing are configuration incomplete, not install failure.'
echo 'Next: si setup (root administration shell), then si. Offline validation: sudo bash /opt/si/scripts/validate_linux.sh'
