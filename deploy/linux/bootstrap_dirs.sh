#!/usr/bin/env bash
# Create writable runtime directories without replacing project source.
set -euo pipefail

install_root="${TENDER_REVIEW_INSTALL_ROOT:-/opt/tender_review}"
if [[ ! -d "$install_root" ]]; then
  sudo install -d -o "$USER" -g "$(id -gn)" "$install_root"
fi
install -d "$install_root/outputs/.staging" "$install_root/outputs/.state" \
  "$install_root/outputs/.tmp" "$install_root/logs" "$install_root/deploy"

