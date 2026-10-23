import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "src"))

from md_chunking.chunk_builder import Limits
from md_chunking.pipeline import run_chunking


def main():
    parser = argparse.ArgumentParser(description="Deterministic Markdown-preserving chunker")
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--document-id", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--target-chars", type=int, default=1000)
    parser.add_argument("--soft-max-chars", type=int, default=1400)
    parser.add_argument("--hard-max-chars", type=int, default=1800)
    parser.add_argument("--min-chars", type=int, default=300)
    parser.add_argument("--strict", action="store_true")
    args = parser.parse_args()
    limits = Limits(args.target_chars, args.soft_max_chars, args.hard_max_chars, args.min_chars)
    report, _, _, _ = run_chunking(args.input, args.document_id, args.output, limits, args.strict)
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
