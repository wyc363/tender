import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "src"))

from retrieval.pipeline import run_retrieval


def main():
    parser = argparse.ArgumentParser(description="Keyword + multi-query BM25 candidate retrieval")
    parser.add_argument("--chunks", type=Path, required=True)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--rule-store", type=Path)
    parser.add_argument("--review-item")
    parser.add_argument("--prompt-program", type=Path)
    parser.add_argument("--strict", action="store_true")
    args = parser.parse_args()
    report, _ = run_retrieval(
        args.chunks, args.config, args.output, args.strict,
        rule_store_path=args.rule_store,
        review_item=args.review_item,
        prompt_program_path=args.prompt_program,
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
