#!/usr/bin/env bash
# Offline import/config smoke; does not download files, call models, or run Pipeline.
set -euo pipefail

root="${TENDER_REVIEW_INSTALL_ROOT:-/opt/tender_review}"
python="$root/venv_main/bin/python"
project="$root/tender_review_system"
config="$project/deploy/linux/pipeline.linux.yaml"

test -x "$python"
cd "$project/05_pipeline_local"
"$python" --version
"$python" -c "import fastapi,pydantic,litellm,jieba,rank_bm25,httpx,yaml,pymupdf; print('main imports=PASS')"
"$python" -c "from review_pipeline.config import load_config; c=load_config('$config'); assert c.http_host=='0.0.0.0'; assert c.client_sync_wait_timeout_seconds==1800; assert c.enabled_review_points==('RP_REGION_INDUSTRY_AWARD',); print('config=PASS')"

