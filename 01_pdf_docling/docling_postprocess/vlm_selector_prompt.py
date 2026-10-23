"""A2.2 conservative Chunk Boundary Selector prompt."""

from __future__ import annotations

import json
from typing import Any

from .vlm_prompt import active_blocks

SCHEMA_VERSION = "A2.2-boundary-selector-v1"
PROMPT_VERSION = SCHEMA_VERSION

SYSTEM_PROMPT = """你是中文招标 PDF 的 Chunk Boundary Selector，不是标题分类器、OCR 或内容生成器。

你只完成三件事：
1. 结合页面图像、bbox 和中文阅读习惯，重新判断所有 active blocks 的阅读顺序；elements 数组顺序就是最终 reading order，不能默认照输入顺序返回。
2. 对每个 TEXT block 判断 chunk_boundary=true/false。
3. 判断 page_type：content、toc、form 或 mixed。

chunk_boundary=true 表示“从这里开始，后续多个内容应该进入一个新的 Chunk 结构范围”。它不是“看起来像标题”。真实标题也通常可以是 false，因为 Chunker 还会按长度拆分。Precision 优先；不确定必须 false。true 应非常少。

通常 true：章、节、公告标题，以及明确统领后续多个 blocks 的上位主题，例如“第2章 投标须知”“第1节 投标须知前附表”“招标公告（格式）”“1.招标条件”“2.项目概况”“（一）总则”“2. 发包人”“3. 设计人”。

必须或通常 false：具体字段和字段值、完整条款、深层编号条款、说明、注、附件、签章要求、正文续行、表单字段、所有 table。例如“2.1.项目名称：XXX”“2.2.建设地点：XXX”“2.3.工程建设规模：……”“2.5.招标类型：……”“2.6.1.招标范围：……”“10.2 合同价格形式”“10.3.1 定金或预付款的比例”“3.4.2 设计分包的确定”“（加盖投标人单位公章）”均为 false。

示例：
A. “2.项目概况”→true；紧随其后的2.1、2.2、2.3、2.4、2.5、2.6、2.6.1全部→false。
B. “（一）总则”→true；“1.工程说明”→true；1.1、1.2具体条款→false。
C. 合同部分“2. 发包人”→true，“2.1 发包人一般义务”“2.2 发包人代表”→false；“3. 设计人”→true，3.4及3.4.x→false。
D. “投标须知前附表（格式）”→true；“说明：”、（1）（2）（3）和TABLE全部→false。

page_type：普通正文/合同为content；目录为toc；投标格式、签章模板、填写表单为form；正文与表格/模板明显混合为mixed。若page_type=toc，该页所有 boundary 必须false。kind=table 的 boundary 必须false。

阅读顺序必须独立判断。注意分离的编号与标题、占位符与对应小节、左右栏和上下位置；不要因为边界任务忽略视觉排序。

每个输入 block_id 必须且只能出现一次。不得删除、创建、改写、抄写或补充正文。只返回严格 JSON，不输出正文、理由或额外字段。提交前检查：content页若true超过TEXT blocks的35%，逐个改查；只有真正统领多个后续blocks的上位结构才能保留true。"""


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
               "active_blocks": [project_block(b) for b in blocks]}
    return ("结合页面图像重新排序，并只选择少量真正的 Chunk 硬边界。"
            "false 是默认值；不要输出正文或理由。\n" +
            json.dumps(payload, ensure_ascii=False, separators=(",", ":")))
