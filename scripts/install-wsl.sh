#!/usr/bin/env bash
set -euo pipefail

# Run this script from WSL2, even when the checked-out repository currently
# lives under /mnt/c or /mnt/d. It copies runtime files into the Linux ext4
# filesystem before creating the virtual environment and SQLite database.
source_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
target_dir="${1:-${HOME}/.local/share/activity-timeline}"

if [[ "$(realpath -m "${source_dir}")" != "$(realpath -m "${target_dir}")" ]]; then
  mkdir -p "${target_dir}"
  tar \
    --exclude='.venv' \
    --exclude='collector/build' \
    --exclude='backend/data/*.db' \
    --exclude='backend/data/*.db-*' \
    --exclude='__pycache__' \
    --exclude='.pytest_cache' \
    -C "${source_dir}" -cf - . | tar --warning=no-timestamp -C "${target_dir}" -xf -
fi

cd "${target_dir}"
if [[ -x .venv/bin/python ]] && .venv/bin/python -m pip --version >/dev/null 2>&1; then
  : # 已有完整环境时保持原样，重复运行安装器也是安全的。
elif python3 -c 'import ensurepip' >/dev/null 2>&1; then
  python3 -m venv .venv
else
  printf '系统未安装 python3-venv，改用用户目录中的 virtualenv 引导程序。\n'
  command -v curl >/dev/null 2>&1 || {
    printf '缺少 curl。请先安装 python3-venv，或安装 curl 后重试。\n' >&2
    exit 1
  }
  mkdir -p .tools
  if [[ ! -s .tools/virtualenv.pyz ]]; then
    curl -fsSL --retry 2 https://bootstrap.pypa.io/virtualenv.pyz -o .tools/virtualenv.pyz
  fi
  python3 .tools/virtualenv.pyz .venv
fi
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -r backend/requirements.txt
mkdir -p backend/data

printf '\n行迹后端已安装到：%s\n' "${target_dir}"
printf '启动命令：\n  cd %q && .venv/bin/uvicorn backend.app.main:app --host 0.0.0.0 --port 8765\n' "${target_dir}"
