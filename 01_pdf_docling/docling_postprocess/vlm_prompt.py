"""Prompt and lossless block projection for the page-structure judge."""

from __future__ import annotations

import json
from typing import Any


SYSTEM_PROMPT = """你是中文招标 PDF 的页面结构裁决器，不是 OCR，也不是内容生成器。

页面文字已经由解析器提取，你只能根据页面视觉布局和给定 block 判断阅读顺序和结构类型；不得创建新 block，不得重新抄写、修改、纠正、补全、摘要任何正文，不得修改数字、否定词、checkbox 或条款号。所有输出只能引用输入中存在的 block_id。不确定时保留为 paragraph，不要猜标题。

你只判断：
1. 当前页 active blocks 的正确 reading_order；
2. 每个 block 的 type：heading、paragraph、table 或 ignore；
3. heading 的 heading_level 只能是 1 或 2，其他类型必须为 null；
4. page_type；
5. 页面结构判断的整体 confidence，范围 0 到 1。

每个 active block 必须且只能在 reading_order 和 classifications 中各出现一次。即使判断为 ignore，也不能省略。kind=table 必须分类为 table。不要输出正文内容。"""


def active_blocks(page_record: dict[str, Any]) -> list[dict[str, Any]]:
    return [block for block in page_record.get("blocks", [])
            if "COVERED_BY_TABLE" not in block.get("flags", [])]


def project_block(block: dict[str, Any], table_preview_chars: int = 1500) -> dict[str, Any]:
    """Project only fields allowed in the model request; never expose audit flags/HTML."""
    result = {
        "block_id": block["block_id"],
        "kind": block["kind"],
        "bbox_norm": block.get("bbox_norm"),
        "parser_label": block.get("parser_label"),
    }
    if block["kind"] == "table":
        text = str(block.get("text", ""))
        result["table_preview"] = text[:table_preview_chars]
        result["table_preview_truncated"] = len(text) > table_preview_chars
    else:
        result["text"] = str(block.get("text", ""))
    return result


def build_user_prompt(page_no: int, blocks: list[dict[str, Any]]) -> str:
    payload = {"page_no": page_no, "active_blocks": [project_block(block) for block in blocks]}
    return ("结合页面图像，仅对以下解析器 blocks 做结构裁决。返回严格 JSON，不能返回正文。\n"
            + json.dumps(payload, ensure_ascii=False, separators=(",", ":")))

