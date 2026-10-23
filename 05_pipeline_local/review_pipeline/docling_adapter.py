"""Stable full-PDF Docling adapter with the default RapidOCR ONNX backend pinned."""
from __future__ import annotations

import argparse
import json
from pathlib import Path


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--input", type=Path, required=True)
    p.add_argument("--output", type=Path, required=True)
    args = p.parse_args(); args.output.mkdir(parents=True, exist_ok=True)
    from docling.datamodel.accelerator_options import AcceleratorOptions
    from docling.datamodel.base_models import InputFormat
    from docling.datamodel.pipeline_options import PdfPipelineOptions, RapidOcrOptions
    from docling.document_converter import DocumentConverter, PdfFormatOption
    options = PdfPipelineOptions(do_ocr=True, do_table_structure=True)
    options.ocr_options = RapidOcrOptions(backend="onnxruntime", lang=["chinese"])
    options.accelerator_options = AcceleratorOptions(num_threads=4)
    converter = DocumentConverter(format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=options)})
    result = converter.convert(str(args.input))
    result.document.save_as_json(args.output / "document.json")
    result.document.save_as_markdown(args.output / "document.md")
    print(json.dumps({"status": str(result.status), "do_ocr": True, "ocr_backend": "onnxruntime",
                      "document_json": str(args.output / "document.json")}, ensure_ascii=False))


if __name__ == "__main__":
    main()
