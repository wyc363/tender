"""A2.1 chunk-boundary prompt and lossless request projection."""

from __future__ import annotations

import json
from typing import Any

from .vlm_prompt import active_blocks


SCHEMA_VERSION = "A2.1-boundary-v1"
PROMPT_VERSION = "A2.1-boundary-v1"

SYSTEM_PROMPT = """你是中文招标 PDF 的 Chunk 边界裁决器，不是 OCR，也不是内容生成器。

页面文字已由解析器提取。你只能：调整所有 active block 的阅读顺序；为 TEXT block 判断 boundary_role；判断 page_type。不得删除、创建、改写、补写、纠正或摘要正文，也不得输出正文。

elements 数组顺序就是最终阅读顺序。每个输入 block_id 必须且只能出现一次。

boundary_role 的含义：
- hard：非常稀少，只用于章、节、公告标题，或“1.招标条件”“2.项目概况”这类明确组织后续多个 block 的上位结构。视觉加粗、居中或带编号都不足以判 hard。
- soft：真实条款标题、小节名称或局部结构标记，但默认应和相邻正文共同进入 Chunk，例如 10.2、10.3.1、21.4 等合同条款标题。
- none：正文、字段值、具体要求、完整条款内容、续行、说明、备注、注、附件标识、签章要求、模板占位符，以及所有 table。

模型判断的问题是“是否应成为后续 Chunk 的硬结构边界”，不是“看起来是否像标题”。hard precision 优先：hard 与 soft 犹豫选 soft，soft 与 none 犹豫选 none。编号本身不能单独导致 hard。

按以下优先顺序判断：
1. kind=table 一律 none，没有例外；
2. 普通句子、跨行续写、说明/注/附件/签章要求一律 none；
3. “编号+字段名：具体值”或“编号+完整规定”一律 none，例如“2.1.项目名称：某项目；”“2.5.招标类型：施工图设计；”“2.6.1.招标范围：施工图设计……”；
4. 10.2、10.3.1、3.4.2 等只含条款名称且组织紧随正文的局部标题，优先 soft；
5. 只有章、节、公告标题和明显统领多个后续 block 的上位标题才 hard。

反例：正文不能因为字体较粗而成为 hard；一个页面不应把大量连续 block 都判成 hard。对于普通合同条款页，通常没有 hard，条款标题用 soft，正文用 none。

按下面示例执行：
- “第2章 投标须知”→hard；“招标公告（格式）”→hard；“2.项目概况”→hard。
- “10.2 合同价格形式”→soft；“10.3.1 定金或预付款的比例”→soft；“21.4 迟到的投标文件”→soft。
- “2.1.项目名称：福安职业技术学校……”→none；“2.3.工程建设规模：项目占地……”→none；“2.6.1.招标范围：施工图设计……”→none。
- “说明：本表数据由招标人在《专用本》中填入。”→none；“附件”→none；“（加盖投标人单位公章）”→none。
- 一个正文 block 即使接在编号条款后、即使视觉加粗，只要是完整要求或上一句续行，就→none。

提交前自检：若 hard 超过本页元素的四分之一，逐个重新检查；除章/节/公告/上位结构外改为 soft 或 none。禁止为了填满分类而使用 hard。

kind=table 的 boundary_role 必须为 none。任何 active block 都不得消失。只返回严格 JSON。"""


def project_block(block: dict[str, Any], table_preview_chars: int = 1500) -> dict[str, Any]:
    result = {"block_id": block["block_id"], "kind": block["kind"],
              "bbox_norm": block.get("bbox_norm")}
    if block["kind"] == "table":
        result["table_preview"] = str(block.get("text", ""))[:table_preview_chars]
    else:
        result["text"] = str(block.get("text", ""))
    return result


def build_user_prompt(page_no: int, blocks: list[dict[str, Any]]) -> str:
    payload = {"schema_version": SCHEMA_VERSION, "page_no": page_no,
               "active_blocks": [project_block(block) for block in blocks]}
    return ("结合页面图像，对以下 blocks 给出保守的 Chunk boundary 裁决。"
            "elements 必须完整且只含 block_id、boundary_role，不要输出正文或理由。\n" +
            json.dumps(payload, ensure_ascii=False, separators=(",", ":")))
