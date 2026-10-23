from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path
from typing import Any

import litellm
from dotenv import load_dotenv


class ClientEnrichmentError(RuntimeError):
    pass


def _model_name(value: str) -> str:
    value = value.strip()
    return value if value.startswith("openai/") else f"openai/{value}"


def _response_text(response: object) -> str:
    choices = getattr(response, "choices", None)
    if choices:
        message = getattr(choices[0], "message", None)
        content = getattr(message, "content", None)
        if content is not None:
            return str(content)
    if isinstance(response, dict):
        return str(response["choices"][0]["message"]["content"])
    return str(response)


def _json_object(raw: str) -> dict[str, Any]:
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", raw, re.S | re.I)
    text = fenced.group(1) if fenced else raw[raw.find("{"):raw.rfind("}") + 1]
    try:
        value = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ClientEnrichmentError(f"Enrichment response is not valid JSON: {exc}") from exc
    if not isinstance(value, dict):
        raise ClientEnrichmentError("Enrichment response must be a JSON object")
    return value


def enrich_confirmed_failure(
    review_point: dict[str, Any],
    reason: str,
    evidence_texts: list[str],
    env_file: Path,
    validation_state: str = "FAIL",
    model_environment: dict[str, str] | None = None,
    enrichment_settings: dict[str, Any] | None = None,
) -> tuple[list[str], str, dict[str, Any]]:
    if model_environment is None:
        load_dotenv(env_file, override=False)
        model_environment = dict(os.environ)
    values = {**os.environ, **model_environment}
    settings = enrichment_settings or {}
    api_base = values.get("MODEL_API_BASE", "").strip()
    api_key = values.get("MODEL_API_KEY", "").strip()
    model = values.get("MODEL_NAME", "").strip()
    if not api_base or not api_key or not model:
        raise ClientEnrichmentError("MODEL_API_BASE, MODEL_API_KEY and MODEL_NAME are required")

    allowed = [
        {"rule_id": rule_id, "term": term}
        for rule_id, term in zip(review_point.get("rule_ids", []), review_point.get("violation_terms", []))
    ]
    payload = {
        "review_item": review_point["name"],
        "rule": review_point["rule"],
        "allowed_violation_terms": allowed,
        "confirmed_failure_reason": reason,
        "grounded_violation_text": evidence_texts,
        "validation_state": validation_state,
    }
    system = (
        "你是招标文件审查结果整理器。上游审查模型已经给出违规判断，你不得重新改变该判断。"
        "请从 allowed_violation_terms 中选择与违规原文及原因直接对应的一个或多个 rule_id，"
        "并给出可执行、针对原文的修改建议。修改建议必须消除当前审查规则命中的投标准入、"
        "评分或中标条件，不得提出仍然保留同类违规条件的替代方案；例如当前规则禁止将外部"
        "奖项作为加分条件时，不得仅把地方奖项扩大为全国奖项，而应删除该奖项加分或改用与"
        "项目履约能力直接相关的客观指标。不得自创新规则编号，不得补充原文没有的事实。"
        "只返回JSON对象，格式为："
        '{"rule_ids":["1202.0"],"suggestion":"具体修改建议"}。'
    )
    temperature = float(settings.get("enrichment_temperature", 0))
    max_tokens = int(settings.get("enrichment_max_tokens", 512))
    enable_thinking = bool(settings.get("enrichment_enable_thinking", False))
    kwargs = {
        "model": _model_name(model),
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
        ],
        "api_base": api_base,
        "api_key": api_key,
        "temperature": temperature,
        "timeout": float(values.get("MODEL_TIMEOUT_SECONDS", "120")),
        "max_tokens": max_tokens,
        "num_retries": int(values.get("MODEL_MAX_RETRIES", "2")),
        "extra_body": {"enable_thinking": enable_thinking},
    }
    started = time.perf_counter()
    if values.get("MODEL_FORCE_STREAM", "auto").strip().lower() in {"1", "true", "yes", "on"} or "api-inference.modelscope.cn" in api_base.lower():
        stream = litellm.completion(**kwargs, stream=True)
        response = litellm.stream_chunk_builder(list(stream), messages=kwargs["messages"])
        if response is None:
            raise ClientEnrichmentError("Streaming endpoint returned no usable response")
    else:
        response = litellm.completion(**kwargs)
    latency = round(time.perf_counter() - started, 3)
    raw = _response_text(response)
    value = _json_object(raw)

    selected_ids = value.get("rule_ids")
    if not isinstance(selected_ids, list):
        raise ClientEnrichmentError("rule_ids must be a JSON array")
    allowed_map = {row["rule_id"]: row["term"] for row in allowed}
    selected_ids = [str(item) for item in selected_ids]
    if not selected_ids or any(item not in allowed_map for item in selected_ids):
        raise ClientEnrichmentError("rule_ids must contain only allowed rule IDs and cannot be empty")
    selected_ids = list(dict.fromkeys(selected_ids))
    suggestion = str(value.get("suggestion") or "").strip()
    if not suggestion:
        raise ClientEnrichmentError("suggestion must be a non-empty string")

    usage = getattr(response, "usage", None)
    if hasattr(usage, "model_dump"):
        usage = usage.model_dump()
    usage = usage if isinstance(usage, dict) else {}
    audit = {
        "status": "PASS",
        "model": model,
        "temperature": temperature,
        "max_tokens": max_tokens,
        "enable_thinking": enable_thinking,
        "validation_state": validation_state,
        "selected_rule_ids": selected_ids,
        "prompt_tokens": int(usage.get("prompt_tokens") or 0),
        "completion_tokens": int(usage.get("completion_tokens") or 0),
        "total_tokens": int(usage.get("total_tokens") or 0),
        "latency_seconds": latency,
        "api_key": "CONFIGURED",
        "raw_response": raw,
    }
    return [allowed_map[item] for item in selected_ids], suggestion, audit
