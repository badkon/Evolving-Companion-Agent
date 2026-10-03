#!/usr/bin/env bash
# Native source install only. No daemon, account changes, clone or destructive reset.
set -euo pipefail
project=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
cd -- "$project"
python_cmd=${SI_INSTALL_PYTHON:-python3.12}
"$python_cmd" -c 'import sys; assert sys.version_info >= (3,12), "Python 3.12+ required"'
if [[ ! -x .venv/bin/python ]]; then
    "$python_cmd" -m venv .venv || { printf '%s\n' '请先安装 python3.12-venv，然后重新运行。'; exit 1; }
fi
.venv/bin/python -m pip install -e .
mkdir -p config runtime backups
if [[ ! -e config/si.env && ! -L config/si.env ]]; then
    (umask 077; cp config/si.env.example config/si.env)
fi
# A user-owned launcher avoids global ownership / systemd installation.
launcher_dir="${SI_BIN_DIR:-${HOME}/.local/bin}"
mkdir -p -- "$launcher_dir"
launcher="$launcher_dir/si"
if [[ -L "$launcher" ]] || { [[ -e "$launcher" ]] && ! grep -Fq '# SI source launcher' "$launcher"; }; then
    printf '%s\n' '已有非 SI 启动器，未覆盖。请使用 .venv/bin/si 或设置 SI_BIN_DIR。'
    exit 1
fi
temporary=$(mktemp "$launcher_dir/.si-launcher.XXXXXX")
trap 'rm -f -- "$temporary"' EXIT
{
    printf '%s\n' '#!/usr/bin/env bash' '# SI source launcher'
    printf 'cd -- %q\n' "$project"
    printf 'exec %q "$@"\n' "$project/.venv/bin/si"
} > "$temporary"
chmod 700 "$temporary"
if [[ -e "$launcher" ]] && ! cmp -s -- "$temporary" "$launcher"; then
    printf '%s\n' '启动器属于其他路径或版本，未覆盖。请设置独立 SI_BIN_DIR。'
    exit 1
fi
mv -- "$temporary" "$launcher"
printf '%s\n' '安装完成。配置 / runtime / backups / Character seed 均保留。'
printf '运行：%q\n将 %q 加入 PATH 后可直接运行 si。\n' "$launcher" "$launcher_dir"
