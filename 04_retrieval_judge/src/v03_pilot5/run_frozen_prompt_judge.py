from __future__ import annotations

import argparse
import ast
import hashlib
import json
import re
import time
from datetime import UTC, datetime
from pathlib import Path

import litellm

from .config import load_v03_settings


MODEL_INPUT_FIELDS = ("review_item", "rule_context", "document_text")
MIN_OUTPUT_TOKENS = 1024
SPECULATIVE_FAILURE_PATTERNS = (
    re.compile(r"潜在(?:的)?违规|潜在(?:的)?地域|潜在(?:的)?行业"),
    re.compile(r"无法排除|不能排除"),
    re.compile(r"可能(?:会)?构成|可能隐含|涉嫌违反"),
    re.compile(r"(?:若|如果).{0,80}(?:则|可能).{0,80}(?:违规|歧视|限制)", re.S),
    re.compile(r"(?:未直接出现|并未直接出现).{0,160}(?:但|然而).{0,160}(?:违规|不合规|不通过)", re.S),
)
EXPLICIT_NEGATION_PATTERNS = (
    re.compile(r"本项目无[^；。\n]{0,20}要求"),
    re.compile(r"本项目不应用"),
    re.compile(r"不作为[^；。\n]{0,20}(?:资格|加分|评分|中标)条件"),
)
REGION_INDUSTRY_REVIEW_ITEM = "地域/行业业绩奖项及外部荣誉作为招投标加分、中标条件"
REGION_INDUSTRY_HIGH_RISK_PATTERNS = (
    re.compile(r"行业协会(?:或组织)?.{0,30}(?:推荐|推荐名单).{0,30}(?:会员|资格|投标)", re.S),
    re.compile(r"(?:省级|市级|县级|行业).{0,30}(?:奖项|获奖|荣誉|业绩).{0,40}(?:得分|加分|评分|资格条件|投标条件|中标条件)", re.S),
    re.compile(r"(?:慈善|捐款|公益).{0,30}(?:荣誉|证书|证明).{0,40}(?:得分|加分|评分|资格条件|投标条件|中标条件)", re.S),
)


def _openai_compatible_model_name(model_name: str) -> str:
    cleaned = model_name.strip()
    return cleaned if cleaned.startswith("openai/") else f"openai/{cleaned}"


def _should_stream(settings) -> bool:
    return settings.force_stream or "api-inference.modelscope.cn" in (settings.api_base or "").lower()


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest().upper()


def _read_jsonl(path: Path) -> list[dict]:
    with path.open("r", encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def _append_jsonl(path: Path, row: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(row, ensure_ascii=False) + "\n")


def _upsert_jsonl(path: Path, row: dict) -> None:
    """Replace a failed candidate on retry instead of creating duplicate candidate IDs."""
    rows = _read_jsonl(path) if path.exists() else []
    replaced = False
    for index, existing in enumerate(rows):
        if existing.get("candidate_id") == row.get("candidate_id"):
            rows[index] = row
            replaced = True
            break
    if not replaced:
        rows.append(row)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.parent.mkdir(parents=True, exist_ok=True)
    with temp.open("w", encoding="utf-8", newline="\n") as handle:
        for item in rows:
            handle.write(json.dumps(item, ensure_ascii=False) + "\n")
    temp.replace(path)


def _write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def load_frozen_prompt(program_path: Path) -> str:
    program = json.loads(program_path.read_text(encoding="utf-8-sig"))
    instructions = program.get("decide", {}).get("signature", {}).get("instructions")
    if not isinstance(instructions, str) or not instructions.strip():
        raise ValueError("Frozen program has no decide.signature.instructions")
    return instructions.strip()


def validate_candidate_inputs(rows: list[dict]) -> dict:
    if not rows:
        raise ValueError("Judge input package is empty")
    candidate_ids = []
    for index, row in enumerate(rows, 1):
        candidate_id = row.get("candidate_id")
        if not isinstance(candidate_id, str) or not candidate_id.strip():
            raise ValueError(f"row {index}: candidate_id must be a non-empty string")
        candidate_ids.append(candidate_id)
        for field in MODEL_INPUT_FIELDS:
            if not isinstance(row.get(field), str) or not row[field].strip():
                raise ValueError(f"row {index}: {field} must be a non-empty string")
    if len(candidate_ids) != len(set(candidate_ids)):
        raise ValueError("candidate_id must be unique")
    review_items = {row["review_item"] for row in rows}
    rule_contexts = {row["rule_context"] for row in rows}
    if len(review_items) != 1:
        raise ValueError("One candidate run must contain exactly one review_item")
    if len(rule_contexts) != 1:
        raise ValueError("rule_context must be identical for one review_item")
    return {
        "rows": len(rows),
        "review_item": next(iter(review_items)),
        "model_input_fields": list(MODEL_INPUT_FIELDS),
        "candidate_ids_unique": True,
        "rule_context_consistent": True,
        "rows_with_page_numbers": sum(bool(row.get("page_numbers")) for row in rows),
    }


def build_messages(frozen_prompt: str, row: dict) -> list[dict]:
    user_payload = (
        f"Review Item: {row['review_item']}\n\n"
        f"Rule Context:\n{row['rule_context']}\n\n"
        f"Document Text:\n{row['document_text']}"
    )
    return [
        {"role": "system", "content": frozen_prompt},
        {"role": "user", "content": user_payload},
    ]


def _response_text(response: object) -> str:
    choices = getattr(response, "choices", None)
    if choices:
        message = getattr(choices[0], "message", None)
        content = getattr(message, "content", None)
        if content is not None:
            return str(content)
    if isinstance(response, dict):
        try:
            return str(response["choices"][0]["message"]["content"])
        except (KeyError, IndexError, TypeError):
            pass
    return str(response)


def call_model(settings, messages: list[dict]) -> tuple[str, dict]:
    kwargs = {
        "model": _openai_compatible_model_name(settings.model_name or ""),
        "messages": messages,
        "api_base": settings.api_base,
        "api_key": settings.api_key,
        "temperature": settings.temperature,
        "timeout": settings.timeout_seconds,
        "max_tokens": settings.max_tokens,
        "num_retries": settings.max_retries,
        "extra_body": {"enable_thinking": settings.enable_thinking},
    }
    if _should_stream(settings):
        stream = litellm.completion(**kwargs, stream=True)
        response = litellm.stream_chunk_builder(list(stream), messages=messages)
        if response is None:
            raise RuntimeError("Streaming endpoint returned no usable response")
    else:
        response = litellm.completion(**kwargs)
    usage = getattr(response, "usage", None)
    if hasattr(usage, "model_dump"):
        usage = usage.model_dump()
    return _response_text(response), usage if isinstance(usage, dict) else {}


def _json_candidate(raw: str) -> dict | None:
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", raw, re.S | re.I)
    text = fenced.group(1) if fenced else raw[raw.find("{"):raw.rfind("}") + 1]
    if not text:
        return None
    try:
        value = json.loads(text)
        return value if isinstance(value, dict) else None
    except json.JSONDecodeError:
        return None


def _parse_bool(value: object) -> bool | None:
    if type(value) is bool:
        return value
    token = str(value).strip().lower()
    if token in {"true", "pass", "通过", "合规", "不违规"}:
        return True
    if token in {"false", "fail", "不通过", "违规", "不合规"}:
        return False
    return None


def _parse_list(value: object) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if value is None:
        return []
    text = str(value).strip()
    if not text or text == "[]":
        return []
    try:
        parsed = ast.literal_eval(text)
        if isinstance(parsed, list):
            return [str(item).strip() for item in parsed if str(item).strip()]
    except (ValueError, SyntaxError):
        pass
    bullet_items = []
    for line in text.splitlines():
        match = re.match(r"^\s*[-*]\s+(.+?)\s*$", line)
        if match:
            item = match.group(1).strip().strip('"“”\'‘’')
            if item:
                bullet_items.append(item)
    if bullet_items:
        return bullet_items
    return [text]


def parse_frozen_output(raw: str) -> dict:
    obj = _json_candidate(raw)
    if obj is not None:
        is_pass = _parse_bool(obj.get("is_pass"))
        reason = str(obj.get("reason") or "").strip()
        evidence = _parse_list(obj.get("text"))
    else:
        pass_match = re.search(r"(?:^|\n)\s*(?:[-*]\s*)?(?:is_pass|Is Pass)\s*[:：]\s*([^\n]+)", raw, re.I)
        reason_match = re.search(
            r"(?:^|\n)\s*(?:[-*]\s*)?(?:reason|Reason)\s*[:：]\s*(.*?)(?=\n\s*(?:[-*]\s*)?(?:text|Text)\s*[:：]|\Z)",
            raw, re.I | re.S,
        )
        text_match = re.search(r"(?:^|\n)\s*(?:[-*]\s*)?(?:text|Text)\s*[:：]\s*(.*)\Z", raw, re.I | re.S)
        is_pass = _parse_bool(pass_match.group(1)) if pass_match else None
        reason = reason_match.group(1).strip() if reason_match else ""
        evidence = _parse_list(text_match.group(1)) if text_match else []
    status = "OK" if is_pass is not None else "INVALID"
    if is_pass is True:
        evidence = []
    if status == "OK" and not reason:
        status = "MISSING_REASON"
    return {"is_pass": is_pass, "reason": reason, "text": evidence, "parse_status": status}


def _compact_with_positions(text: str) -> tuple[str, list[int]]:
    compact = []
    positions = []
    for index, char in enumerate(text):
        if not char.isspace():
            compact.append(char)
            positions.append(index)
    return "".join(compact), positions


def ground_evidence_to_source(evidence: list[str], source_text: str) -> tuple[list[str], list[bool]]:
    """Return exact source substrings; never expose an ungrounded model paraphrase as evidence."""
    source_compact, source_positions = _compact_with_positions(source_text)
    grounded = []
    checks = []
    for proposed in evidence:
        cleaned = proposed.strip().strip('"“”\'‘’')
        if cleaned and cleaned in source_text:
            grounded.append(cleaned)
            checks.append(True)
            continue
        proposed_compact, _ = _compact_with_positions(cleaned)
        compact_start = source_compact.find(proposed_compact) if proposed_compact else -1
        if compact_start >= 0:
            compact_end = compact_start + len(proposed_compact) - 1
            exact = source_text[source_positions[compact_start]:source_positions[compact_end] + 1]
            grounded.append(exact)
            checks.append(True)
        else:
            checks.append(False)
    return grounded, checks


def decision_validation_flags(is_pass: bool | None, reason: str, evidence: list[str], *,
                              review_item: str = "", source_text: str = "") -> list[str]:
    """Reject unsupported FAIL decisions while preserving the frozen model response for audit."""
    flags = []
    if is_pass is False:
        if any(pattern.search(reason) for pattern in SPECULATIVE_FAILURE_PATTERNS):
            flags.append("SPECULATIVE_VIOLATION")
        evidence_text = "\n".join(evidence)
        if any(pattern.search(evidence_text) for pattern in EXPLICIT_NEGATION_PATTERNS):
            flags.append("EXPLICIT_NEGATION_CONFLICT")
    elif is_pass is True and review_item == REGION_INDUSTRY_REVIEW_ITEM:
        has_explicit_negation = any(pattern.search(source_text) for pattern in EXPLICIT_NEGATION_PATTERNS)
        if not has_explicit_negation and any(
            pattern.search(source_text) for pattern in REGION_INDUSTRY_HIGH_RISK_PATTERNS
        ):
            flags.append("HIGH_RISK_TRIGGER_MISSED")
    return flags


def build_business_output(source: dict, parsed: dict, raw_output: str, *,
                          latency: float, usage: dict, error: str | None) -> dict:
    is_pass = parsed["is_pass"]
    violation = is_pass is False
    proposed_evidence = parsed["text"] if violation else []
    evidence, exact_checks = ground_evidence_to_source(proposed_evidence, source["document_text"])
    evidence_valid = not violation or (bool(evidence) and all(exact_checks))
    reason = parsed["reason"].strip() or "模型未返回有效判断理由，需重新审查。"
    validation_flags = decision_validation_flags(
        is_pass, reason, evidence,
        review_item=source.get("review_item", ""), source_text=source["document_text"],
    )
    parse_status = parsed["parse_status"]
    if violation and not evidence_valid:
        parse_status = "EVIDENCE_NOT_GROUNDED"
    elif validation_flags:
        parse_status = validation_flags[0]
    output_valid = parsed["parse_status"] == "OK" and evidence_valid and not validation_flags
    review_result = (
        "不违规" if is_pass is True and output_valid
        else "违规" if violation and output_valid
        else "结果无效"
    )
    return {
        "candidate_id": source["candidate_id"],
        "片段原文": source["document_text"],
        "审查结果": review_result,
        "违规原文": evidence if violation and output_valid else [],
        "判断理由": reason,
        "原文行号": {
            "start": source.get("context_source_line_start"),
            "end": source.get("context_source_line_end"),
        },
        "anchor_chunk_id": source.get("anchor_chunk_id"),
        "context_chunk_ids": source.get("context_chunk_ids", []),
        "模型候选违规原文": proposed_evidence,
        "evidence_exact_checks": exact_checks,
        "evidence_all_exact": evidence_valid,
        "decision_validation_flags": validation_flags,
        "parse_status": parse_status,
        "raw_output": raw_output,
        "latency_seconds": round(latency, 3),
        "usage": usage,
        "technical_error": error,
    }


def check(input_path: Path, program_path: Path, output_dir: Path) -> dict:
    settings = load_v03_settings()
    rows = _read_jsonl(input_path)
    input_check = validate_candidate_inputs(rows)
    frozen_prompt = load_frozen_prompt(program_path)
    summary = {
        "status": "CHECK_OK",
        "execution_mode": "DIRECT_FROZEN_PROMPT",
        "api_base_configured": bool(settings.api_base),
        "api_key_configured": bool(settings.api_key),
        "model_name": settings.model_name,
        "temperature": settings.temperature,
        "max_tokens": settings.max_tokens,
        "recommended_min_output_tokens": MIN_OUTPUT_TOKENS,
        "output_token_budget_status": "PASS" if settings.max_tokens >= MIN_OUTPUT_TOKENS else "TRUNCATION_RISK",
        "enable_thinking": settings.enable_thinking,
        "input_path": str(input_path.resolve()),
        "input_sha256": _sha256(input_path),
        "program_path": str(program_path.resolve()),
        "program_sha256": _sha256(program_path),
        "frozen_instruction_sha256": hashlib.sha256(frozen_prompt.encode("utf-8")).hexdigest().upper(),
        "input_check": input_check,
        "model_output_contract": ["is_pass", "reason", "text"],
        "business_output_contract": ["片段原文", "审查结果", "违规原文", "判断理由"],
        "api_called": False,
    }
    _write_json(output_dir / "check_manifest.json", summary)
    return summary


def run(input_path: Path, program_path: Path, output_dir: Path, *,
        limit: int | None = None, resume: bool = True) -> dict:
    settings = load_v03_settings()
    if not settings.api_configured:
        raise RuntimeError("MODEL_API_BASE, MODEL_API_KEY and MODEL_NAME are required")
    if settings.temperature != 0:
        raise RuntimeError("Frozen Prompt Judge requires MODEL_TEMPERATURE=0")
    if settings.max_tokens < MIN_OUTPUT_TOKENS:
        raise RuntimeError(
            f"Frozen Prompt Judge requires MODEL_MAX_TOKENS >= {MIN_OUTPUT_TOKENS}; "
            f"current value {settings.max_tokens} has demonstrated truncation risk"
        )
    rows = _read_jsonl(input_path)
    input_check = validate_candidate_inputs(rows)
    frozen_prompt = load_frozen_prompt(program_path)
    if limit is not None:
        rows = rows[:limit]
    predictions_path = output_dir / "predictions.jsonl"
    completed = set()
    if resume and predictions_path.exists():
        completed = {
            row["candidate_id"] for row in _read_jsonl(predictions_path)
            if row.get("parse_status") == "OK" and row.get("审查结果") in {"违规", "不违规"}
        }
    started_at = datetime.now(UTC).isoformat()
    started = time.perf_counter()
    processed = failures = 0
    for index, source in enumerate(rows, 1):
        if source["candidate_id"] in completed:
            continue
        raw_output = ""
        usage = {}
        error = None
        call_started = time.perf_counter()
        try:
            raw_output, usage = call_model(settings, build_messages(frozen_prompt, source))
            parsed = parse_frozen_output(raw_output)
        except Exception as exc:
            failures += 1
            error = f"{type(exc).__name__}: {exc}"
            parsed = {"is_pass": None, "reason": "", "text": [], "parse_status": "INVALID"}
        output = build_business_output(
            source, parsed, raw_output,
            latency=time.perf_counter() - call_started, usage=usage, error=error,
        )
        _upsert_jsonl(predictions_path, output)
        processed += 1
        print(f"[{index}/{len(rows)}] {source['candidate_id']} {output['审查结果']} {output['parse_status']}", flush=True)
    manifest = {
        "status": "COMPLETE",
        "execution_mode": "DIRECT_FROZEN_PROMPT",
        "started_at": started_at,
        "finished_at": datetime.now(UTC).isoformat(),
        "input_path": str(input_path.resolve()),
        "input_sha256": _sha256(input_path),
        "program_path": str(program_path.resolve()),
        "program_sha256": _sha256(program_path),
        "predictions_path": str(predictions_path.resolve()),
        "predictions_sha256": _sha256(predictions_path),
        "rows_requested": len(rows),
        "rows_processed_this_run": processed,
        "technical_failures_this_run": failures,
        "model_name": settings.model_name,
        "temperature": settings.temperature,
        "max_tokens": settings.max_tokens,
        "enable_thinking": settings.enable_thinking,
        "input_check": input_check,
        "elapsed_seconds": round(time.perf_counter() - started, 3),
    }
    _write_json(output_dir / "run_manifest.json", manifest)
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description="Run retrieval candidates with a frozen prompt JSON directly")
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--program", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--check-only", action="store_true")
    mode.add_argument("--run", action="store_true")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--no-resume", action="store_true")
    args = parser.parse_args()
    result = check(args.input, args.program, args.output) if args.check_only else run(
        args.input, args.program, args.output, limit=args.limit, resume=not args.no_resume,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
