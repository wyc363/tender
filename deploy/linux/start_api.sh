#!/usr/bin/env bash
# Start only the FastAPI process. Worker remains a separate process.
set -euo pipefail

root="${TENDER_REVIEW_INSTALL_ROOT:-/opt/tender_review}"
project="$root/tender_review_system"
env_file="$project/05_pipeline_local/.env"
test -r "$env_file"
set -a
# shellcheck disable=SC1090
source "$env_file"
set +a
cd "$project/05_pipeline_local"
exec "$root/venv_main/bin/python" -m review_pipeline.http.server \
  --config "$project/deploy/linux/pipeline.linux.yaml"

