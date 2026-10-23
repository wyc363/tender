#!/usr/bin/env bash
# Read-only Gate D1 inventory. This script installs and changes nothing.
set -euo pipefail

cat /etc/os-release
uname -a
nproc
free -h
df -h
python3 --version
locale
curl --fail --silent --show-error --head --max-time 15 https://www.baidu.com >/dev/null
echo "outbound_https=PASS"
if command -v nvidia-smi >/dev/null 2>&1; then
  nvidia-smi
else
  echo "gpu=NOT_DETECTED"
fi

