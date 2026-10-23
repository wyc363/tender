from __future__ import annotations

import copy
import hashlib
import json
import re
from pathlib import Path

from .tokenizer import normalize


NUMBERED_ITEM_RE = re.compile(r"\*\s*\d+(?:\.\d+)?[\.、]?\s*")
PLACEHOLDER_RE = re.compile(r"(?i)xx")
SCOPE_VOCABULARY = (
    "投标须知前附表", "评标办法", "标准前附表", "数据表",
    "商务", "资信", "资格审查", "评分",
)


DECISION_GATE_LINES = (
    "1. 只依据【待审原文】中实际出现的文字判断，不得补入相邻片段、其他章节或常见范本中的内容。",
    "2. 判为违规必须同时满足：争议内容实际作用于投标准入、评分或中标；原文明示了当前审查项禁止的对象或限制；违规原文可逐字引用。任一项不满足即不得判违规。",
    "3. ‘可能、若、涉嫌、无法排除、未明确说明’只表示信息不足，不能作为违规事实；不得因原文没有说明全国平台核验方式而反推存在地域限制。",
    "4. 边界R2中的条件是并列选择：业绩类型合理且未限制来源地域，或者允许全国性公开平台核验，均不构成地域限制；全国平台并非普通类似业绩合法的必要条件。",
    "5. 若条款明确写有‘本项目无业绩要求’、‘不应用’、‘不作为资格/加分/中标条件’等否定或停用表述，应按其实际生效状态判断，不得引用被停用的模板文字判违规。",
    "6. 评标专家的专业组成、项目本身所属行业、一般执业资格或专业能力要求，不等于把投标人的特定地域/行业业绩或奖项作为条件。",
)


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def _split_numbered_items(value: str | None) -> list[str]:
    if not value or not value.strip():
        return []
    matches = list(NUMBERED_ITEM_RE.finditer(value))
    if not matches:
        return [value.strip()]
    items = []
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(value)
        text = value[match.end():end].strip()
        if text:
            items.append(text)
    return items


def _deduplicate_texts(values: list[str]) -> list[str]:
    unique = []
    seen = set()
    for value in values:
        key = normalize(value)
        if key and key not in seen:
            seen.add(key)
            unique.append(value.strip())
    return unique


def _query_text(value: str) -> str:
    value = PLACEHOLDER_RE.sub(" ", value)
    value = re.sub(r"[（）()【】\[\]，,。；;：:\n\r/]+", " ", value)
    return re.sub(r"\s+", " ", value).strip()


def compile_rule_store_config(
    rule_store_path: str | Path,
    review_item: str,
    base_config: dict,
) -> tuple[dict, dict]:
    """Compile one, and only one, Rule Store review item into retrieval config."""
    path = Path(rule_store_path)
    store = _load_json(path)
    if review_item not in store:
        available = "、".join(store.keys())
        raise KeyError(f"Review item not found: {review_item}. Available: {available}")

    selected = store[review_item]
    leaves = selected.get("leaves", [])
    boundaries = selected.get("boundary_guidance", [])
    if not leaves:
        raise ValueError(f"Selected review item has no leaves: {review_item}")
    if any(leaf.get("审查点名称") != review_item for leaf in leaves):
        raise ValueError("Selected node contains a leaf from another review item")
    if any(item.get("review_item") != review_item for item in boundaries):
        raise ValueError("Selected node contains boundary guidance from another review item")

    config = copy.deepcopy(base_config)
    config["review_item"] = review_item
    config.pop("expected_chunk_count", None)
    config.pop("smoke_concepts", None)
    config.pop("hard_negative_smoke_phrase", None)
    config["rule_store_mode"] = True
    config["rule_store_path"] = str(path.resolve())
    config["rule_store_sha256"] = hashlib.sha256(path.read_bytes()).hexdigest().upper()
    config["rule_context_v2"] = selected.get("rule_context_v2") or selected.get("rule_context") or ""
    config["context_neighbors"] = config.get("context_neighbors", 1)
    config["audit_top_k"] = config.get("audit_top_k", 100)

    queries = []
    rule_ids = []
    violation_examples = []
    legal_examples = []
    scan_scopes = []
    rule_descriptions = []
    policy_sources = []
    review_types = []
    for leaf in leaves:
        rule_id = str(leaf.get("规则序号", "")).removesuffix(".0")
        if not rule_id:
            raise ValueError("Rule leaf is missing 规则序号")
        rule_ids.append(str(leaf["规则序号"]))
        examples = _split_numbered_items(leaf.get("违规条目"))
        violation_examples.extend(examples)
        legal_examples.extend(_split_numbered_items(leaf.get("合法条目")))
        if leaf.get("审查规则说明"):
            rule_descriptions.append(leaf["审查规则说明"])
        if leaf.get("政策来源"):
            policy_sources.append(leaf["政策来源"])
        if leaf.get("审查类型"):
            review_types.append(leaf["审查类型"])
        if leaf.get("扫描范围"):
            scan_scopes.append(leaf["扫描范围"])
        query = _query_text(" ".join(examples) or leaf.get("审查规则说明", ""))
        if query:
            queries.append({
                "id": f"RISK_{rule_id}",
                "text": query,
                "channel": "risk",
                "weight": 1.0,
                "rule_ids": [str(leaf["规则序号"])],
                "boundary_ids": [],
            })

    boundary_ids = []
    for boundary in boundaries:
        boundary_id = str(boundary.get("boundary_id", "")).strip()
        text = str(boundary.get("text", "")).strip()
        if not boundary_id or not text:
            continue
        boundary_ids.append(boundary_id)
        queries.append({
            "id": f"BOUNDARY_{boundary_id}",
            "text": _query_text(text),
            "channel": "boundary",
            "weight": float(config.get("boundary_query_weight", 0.5)),
            "rule_ids": [],
            "boundary_ids": [boundary_id],
        })

    if not queries:
        raise ValueError("Rule Store compilation produced no BM25 query")
    config["queries"] = queries
    config["selected_rule_ids"] = rule_ids
    config["selected_boundary_ids"] = boundary_ids
    config["violation_examples"] = _deduplicate_texts(violation_examples)
    config["legal_examples"] = _deduplicate_texts(legal_examples)
    config["boundary_guidance"] = boundaries
    config["scan_scopes"] = _deduplicate_texts(scan_scopes)
    unique_descriptions = _deduplicate_texts(rule_descriptions)
    unique_policies = _deduplicate_texts(policy_sources)
    unique_types = _deduplicate_texts(review_types)
    compact_lines = [f"【当前审查项】{review_item}"]
    if unique_types:
        compact_lines.append("【审查类型】" + "；".join(unique_types))
    compact_lines += ["", "【严格判定门槛】"] + [f"- {text}" for text in DECISION_GATE_LINES]
    compact_lines += ["", "【审查规则说明】"] + [f"- {text}" for text in unique_descriptions]
    compact_lines += ["", "【违规规则与示例】"]
    for leaf in leaves:
        for example in _split_numbered_items(leaf.get("违规条目")):
            compact_lines.append(f"- 规则{leaf['规则序号']}：{example}")
    compact_lines += ["", "【合法示例（已去重）】"] + [
        f"- {text}" for text in config["legal_examples"]
    ]
    compact_lines += ["", "【边界指引】"] + [
        f"- {item['boundary_id']}：{item['text']}" for item in boundaries
    ]
    compact_lines += ["", "【扫描范围】"] + [f"- {text}" for text in config["scan_scopes"]]
    compact_lines += ["", "【政策来源】"] + [f"- {text}" for text in unique_policies]
    config["judge_rule_context"] = "\n".join(compact_lines).strip()
    config["table_context_neighbors"] = config["context_neighbors"]
    config["context_expand_max_anchor_chars"] = None
    scope_text = "\n".join(scan_scopes)
    derived_scope_terms = [term for term in SCOPE_VOCABULARY if term in scope_text]
    config["scan_scope_terms"] = _deduplicate_texts(
        list(config.get("scan_scope_terms", [])) + derived_scope_terms
    )

    manifest = {
        "rule_store_path": str(path.resolve()),
        "rule_store_sha256": config["rule_store_sha256"],
        "available_review_item_count": len(store),
        "selected_review_item_count": 1,
        "selected_review_item": review_item,
        "selected_rule_ids": rule_ids,
        "selected_boundary_ids": boundary_ids,
        "risk_query_count": sum(q["channel"] == "risk" for q in queries),
        "boundary_query_count": sum(q["channel"] == "boundary" for q in queries),
        "unique_violation_example_count": len(config["violation_examples"]),
        "unique_legal_example_count": len(config["legal_examples"]),
        "raw_rule_context_chars": len(config["rule_context_v2"]),
        "judge_rule_context_chars": len(config["judge_rule_context"]),
    }
    return config, manifest
