#!/usr/bin/env bash
# Import-only by default. With one local PDF argument, runs the isolated Docling adapter.
set -euo pipefail

root="${TENDER_REVIEW_INSTALL_ROOT:-/opt/tender_review}"
python="$root/venv_docling/bin/python"
project="$root/tender_review_system"

test -x "$python"
cd "$project/05_pipeline_local"
"$python" --version
"$python" -c "from docling.document_converter import DocumentConverter; import onnxruntime; print('docling imports=PASS')"

if [[ $# -eq 0 ]]; then
  exit 0
fi
if [[ $# -ne 1 || ! -f "$1" ]]; then
  echo "Usage: $0 [local.pdf]" >&2
  exit 2
fi

output="$(mktemp -d /tmp/tender-docling-smoke.XXXXXX)"
"$python" -m review_pipeline.docling_adapter --input "$1" --output "$output"
test -s "$output/document.json"
test -s "$output/document.md"
echo "docling_conversion=PASS output=$output"

