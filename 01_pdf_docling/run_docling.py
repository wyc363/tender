from __future__ import annotations

import argparse
import json
import traceback
from pathlib import Path

from docling.document_converter import DocumentConverter


def parse_args():
    p = argparse.ArgumentParser()
    p.add_argument("--input", type=Path, default=Path("input"))
    p.add_argument("--output", type=Path, default=Path("output/docling"))
    return p.parse_args()


def iter_pdfs(path: Path):
    if path.is_file():
        if path.suffix.lower() == ".pdf":
            yield path
        return
    yield from sorted(path.glob("*.pdf"))


def main():
    args = parse_args()
    args.output.mkdir(parents=True, exist_ok=True)

    pdfs = list(iter_pdfs(args.input))
    if not pdfs:
        raise SystemExit(f"未找到 PDF: {args.input.resolve()}")

    print(f"发现 {len(pdfs)} 个 PDF")
    converter = DocumentConverter()

    for i, pdf in enumerate(pdfs, 1):
        print(f"\n[{i}/{len(pdfs)}] Docling: {pdf.name}")
        out_dir = args.output / pdf.stem
        out_dir.mkdir(parents=True, exist_ok=True)

        try:
            result = converter.convert(str(pdf))
            doc = result.document

            md_path = out_dir / "document.md"
            json_path = out_dir / "document.json"

            md_path.write_text(doc.export_to_markdown(), encoding="utf-8")
            json_path.write_text(
                json.dumps(doc.export_to_dict(), ensure_ascii=False, indent=2),
                encoding="utf-8",
            )

            print(f"  MD   -> {md_path}")
            print(f"  JSON -> {json_path}")

        except Exception:
            err = traceback.format_exc()
            (out_dir / "ERROR.txt").write_text(err, encoding="utf-8")
            print("  失败，错误已写入 ERROR.txt")

    print("\nDocling 完成。")


if __name__ == "__main__":
    main()
