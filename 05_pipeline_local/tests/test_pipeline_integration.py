from __future__ import annotations

import csv
import json
import re
import sys
from dataclasses import asdict
from pathlib import Path

import pytest

from review_pipeline.batch import write_batch_outputs
from review_pipeline.client_api_adapter import _interface_is_pass, build_client_api_outputs
from review_pipeline.config import PipelineConfig, load_config
from review_pipeline.gates import chunk_generic_gate, doc011_regression, judge_generic_gate, retrieval_generic_gate
from review_pipeline.orchestrator import STAGES, PipelineOrchestrator, StageResult
from review_pipeline.report import build_combined_report
from review_pipeline.retry_review_cli import _merge_predictions, _review_candidate_ids
from review_pipeline.utils import redact, sha256_file, write_json


def jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8")


def test_orchestrator_stage_order():
    assert STAGES == ["input", "docling", "parser", "chunk", "retrieval", "judge", "finalize"]


def test_stage_result_contract():
    row = StageResult("parser", "PASS", "a", "b", 1.2)
    assert set(asdict(row)) >= {"stage", "status", "started_at", "finished_at", "duration_seconds", "artifacts", "metrics", "gate", "error"}


def test_api_key_redaction():
    assert "secret" not in redact("MODEL_API_KEY=secret")
    assert "***" in redact("api_key: secret")


def test_client_boolean_review_follows_initial_model_result():
    assert _interface_is_pass("REVIEW", [{"raw_result": "PASS"}]) is True
    assert _interface_is_pass("REVIEW", [{"raw_result": "FAIL"}]) is False
    assert _interface_is_pass("REVIEW", [{"raw_result": "PASS"}, {"raw_result": "FAIL"}]) is False
    assert _interface_is_pass("REVIEW", []) is False


def test_client_output_emits_one_result_per_review_point(tmp_path, monkeypatch):
    root = tmp_path / "DOC_X"
    judge = root / "05_judge"
    retrieval = root / "04_retrieval"
    write_json(judge / "document_review_result.json", {
        "document_id": "DOC_X", "rule_id": "RULE", "review_item": "审查点", "result": "REVIEW"
    })
    write_json(judge / "final_review_result.json", {"审查结果": "REVIEW"})
    candidates = [{
        "candidate_id": "K1", "rule_id": "RULE", "rank": 1,
        "anchor_chunk": {"chunk_id": "C1", "source_order": 1, "text": "片段一", "page_numbers": [3]},
        "context_before": [], "context_after": [],
    }, {
        "candidate_id": "K2", "rule_id": "RULE", "rank": 2,
        "anchor_chunk": {"chunk_id": "C2", "source_order": 2, "text": "片段二", "page_numbers": [7]},
        "context_before": [], "context_after": [],
    }]
    predictions = [
        {"candidate_id": "K1", "审查结果": "不违规", "判断理由": "合规一",
         "违规原文": [], "parse_status": "OK", "technical_error": None},
        {"candidate_id": "K2", "审查结果": "不违规", "判断理由": "合规二",
         "违规原文": [], "parse_status": "OK", "technical_error": None},
    ]
    jsonl(retrieval / "retrieval_candidates.jsonl", candidates)
    jsonl(judge / "predictions.jsonl", predictions)
    monkeypatch.setattr("review_pipeline.client_api_adapter.load_rule_catalog", lambda _: {})
    monkeypatch.setattr("review_pipeline.client_api_adapter.find_review_point", lambda *args, **kwargs: {
        "review_point_id": "RULE", "index": 12,
        "name": "审查点", "rule": "规则", "policy_sources": ["依据"], "type": "类型",
        "risk_level": "高", "rule_ids": [], "violation_terms": [],
    })
    content_path, response_path = build_client_api_outputs(root, enrich_fail=False)
    content = json.loads(content_path.read_text(encoding="utf-8"))
    embedded = json.loads(json.loads(response_path.read_text(encoding="utf-8"))["choices"][0]["message"]["content"])
    assert len(content["result"]) == 1
    assert content["result"][0]["index"] == 12
    assert content["result"][0]["text"] == ["片段一", "片段二"]
    assert content["result"][0]["page"] == [3, 7]
    assert content["result"][0]["is_pass"] is True
    assert (root / "review_point_result.json").is_file()
    assert (root / "client_aggregation_audit.json").is_file()
    assert embedded == content


def test_selective_review_retry_selects_and_merges_only_review_candidates():
    validated = [
        {"candidate_id": "K1", "validated_result": "PASS"},
        {"candidate_id": "K2", "validated_result": "REVIEW"},
        {"candidate_id": "K3", "validated_result": "FAIL"},
    ]
    assert _review_candidate_ids(validated) == ["K2"]
    existing = [{"candidate_id": "K1", "value": "old1"}, {"candidate_id": "K2", "value": "old2"}]
    merged = _merge_predictions(existing, [{"candidate_id": "K2", "value": "new2"}], ["K2"])
    assert merged == [{"candidate_id": "K1", "value": "old1"}, {"candidate_id": "K2", "value": "new2"}]


def test_resume_requires_all_fingerprints(tmp_path):
    artifact = tmp_path / "x"; artifact.write_text("x")
    obj = object.__new__(PipelineOrchestrator)
    manifest = {"stages": {"chunk": {"status": "PASS", "input_fingerprint": "i", "stage_fingerprint": "s", "config_fingerprint": "c"}}}
    assert obj._can_resume(manifest, "chunk", "i", "s", "c", [artifact])
    assert not obj._can_resume(manifest, "chunk", "changed", "s", "c", [artifact])


def test_resume_requires_artifacts(tmp_path):
    obj = object.__new__(PipelineOrchestrator)
    manifest = {"stages": {"chunk": {"status": "PASS", "input_fingerprint": "i", "stage_fingerprint": "s", "config_fingerprint": "c"}}}
    assert not obj._can_resume(manifest, "chunk", "i", "s", "c", [tmp_path / "missing"])


def _chunk_fixture(tmp_path: Path, count: int = 2, missing_page: bool = False):
    out = tmp_path / "chunk"; out.mkdir()
    mappings = [{"source_block_ids": [f"B{i}"], "source_table_ids": [], "semantic_type": "text", "page_numbers": [i]}
                for i in range(1, count + 1)]
    chunks = [{"chunk_id": f"D-C{i}", "chunk_type": "TEXT", "text": f"正文{i}",
               "page_numbers": ([] if missing_page and i == 1 else [i]), "source_block_ids": [f"B{i}"],
               "source_table_ids": []} for i in range(1, count + 1)]
    jsonl(out / "chunks.jsonl", chunks); jsonl(tmp_path / "source_map.jsonl", mappings)
    write_json(out / "C1_1_Source_Coverage.json", {
        "content_text_source_coverage": {"coverage": 1}, "content_bearing_table_chunk_coverage": {"coverage": 1},
        "table_source_traceability": {"coverage": 1}, "heading_source_traceability": {"coverage": 1}})
    write_json(out / "C1_1_Provenance_Validation.json", {"status": "PASS"})
    write_json(out / "C1_1_Chunk_Statistics.json", {"mergeable_short_count": 0, "mergeable_short_chunk_ids": []})
    return out, tmp_path / "source_map.jsonl"


def test_chunk_generic_gate_accepts_arbitrary_count(tmp_path):
    out, source = _chunk_fixture(tmp_path, 7)
    gate = chunk_generic_gate(out, source)
    assert gate["status"] == "PASS" and gate["total_chunks"] == 7


def test_chunk_generic_gate_rejects_missing_page(tmp_path):
    out, source = _chunk_fixture(tmp_path, missing_page=True)
    assert chunk_generic_gate(out, source)["status"] == "FAIL"


def test_chunk_mergeable_short_is_warning(tmp_path):
    out, source = _chunk_fixture(tmp_path)
    write_json(out / "C1_1_Chunk_Statistics.json", {"mergeable_short_count": 1, "mergeable_short_chunk_ids": ["D-C1"]})
    gate = chunk_generic_gate(out, source)
    assert gate["status"] == "PASS" and gate["warnings"][0]["code"] == "MERGEABLE_SHORT"


def _retrieval_fixture(tmp_path: Path, count: int):
    chunks = [{"chunk_id": f"D-C{i}", "document_region": "body", "chunk_type": "TEXT", "page_numbers": [i],
               "source_block_ids": [f"B{i}"]} for i in range(count)]
    top = [{**row, "rank": i + 1} for i, row in enumerate(chunks[:min(50, count)])]
    judge = [{"anchor_chunk_id": row["chunk_id"]} for row in top]
    jsonl(tmp_path / "chunks.jsonl", chunks); jsonl(tmp_path / "retrieval_candidates.jsonl", top); jsonl(tmp_path / "judge_inputs.jsonl", judge)
    return tmp_path / "chunks.jsonl"


def test_retrieval_generic_gate_min_50(tmp_path):
    chunks = _retrieval_fixture(tmp_path, 9)
    gate = retrieval_generic_gate(chunks, tmp_path)
    assert gate["status"] == "PASS" and gate["top_k_actual"] == 9


def test_retrieval_generic_gate_top50(tmp_path):
    chunks = _retrieval_fixture(tmp_path, 60)
    assert retrieval_generic_gate(chunks, tmp_path)["top_k_actual"] == 50


def _judge_fixture(tmp_path: Path, technical=False):
    prediction = {"candidate_id": "K1", "technical_error": ("timeout" if technical else None)}
    validated = {"candidate_id": "K1", "validated_result": ("REVIEW" if technical else "PASS"),
                 "technical_error": ("timeout" if technical else None), "grounded": True, "evidence_page_numbers": [1]}
    jsonl(tmp_path / "predictions.jsonl", [prediction]); jsonl(tmp_path / "validated_predictions.jsonl", [validated])
    jsonl(tmp_path / "findings.jsonl", []); write_json(tmp_path / "document_review_result.json", {"result": validated["validated_result"]})
    write_json(tmp_path / "final_review_result.json", {"审查结果": validated["validated_result"]})


def test_judge_generic_gate_success(tmp_path):
    _judge_fixture(tmp_path)
    assert judge_generic_gate(tmp_path, 1)["status"] == "PASS"


def test_judge_technical_failure_not_silent_pass(tmp_path):
    _judge_fixture(tmp_path, technical=True)
    gate = judge_generic_gate(tmp_path, 1)
    assert gate["status"] == "PASS" and gate["validated_review"] == 1


def test_doc011_regression_profile_passes_only_known_counts():
    gate = doc011_regression({"physical_pages": 172, "active_pages": 169, "source_active_blocks": 1946},
                             {"total_chunks": 273, "text_chunks": 230, "table_chunks": 43}, {"top_k_actual": 50})
    assert gate["status"] == "PASS"


def test_doc011_regression_does_not_accept_arbitrary_page_count():
    gate = doc011_regression({"physical_pages": 10, "active_pages": 9, "source_active_blocks": 100},
                             {"total_chunks": 20, "text_chunks": 18, "table_chunks": 2}, {"top_k_actual": 20})
    assert gate["status"] == "FAIL"


def test_batch_summary_continues_with_failed_document(tmp_path):
    rows = [{"document_id": "A", "pipeline_status": "FAILED", "error": "x"},
            {"document_id": "B", "pipeline_status": "COMPLETED", "document_result": "PASS", "total_api_cost_cny": .1}]
    write_batch_outputs(tmp_path, rows)
    summary = json.loads((tmp_path / "I1_L_Batch_Summary.json").read_text(encoding="utf-8"))
    assert summary["failed"] == 1 and summary["completed"] == 1


def test_manual_evaluation_human_fields_blank(tmp_path):
    write_batch_outputs(tmp_path, [{"document_id": "A", "pipeline_status": "COMPLETED", "document_result": "PASS"}])
    row = next(csv.DictReader((tmp_path / "I1_L_Manual_Evaluation.csv").open(encoding="utf-8-sig")))
    assert row["system_result"] == "PASS" and row["human_result"] == "" and row["human_comment"] == ""


def test_path_portability_relative_to_project_root(tmp_path):
    root = tmp_path / "project"; root.mkdir(); cfg = tmp_path / "pipeline.yaml"
    fields = ["rule_store_path", "retrieval_config_path", "prompt_program_path", "docling_project", "parser_project",
              "chunk_project", "retrieval_project", "judge_project", "docling_python", "parser_python", "chunk_python",
              "retrieval_python", "judge_python", "parser_env_file", "judge_env_file"]
    cfg.write_text("project_root: project\noutput_root: out\n" + "".join(f"{name}: x\n" for name in fields), encoding="utf-8")
    loaded = load_config(cfg)
    assert loaded.output_root == root / "out" and loaded.rule_store_path == root / "x"


def test_structured_config_reads_pipeline_env_for_relocation(tmp_path, monkeypatch):
    package = tmp_path / "portable"; config_dir = package / "05_pipeline_local" / "config"
    config_dir.mkdir(parents=True)
    relocated = tmp_path / "relocated_project"; relocated.mkdir()
    python = tmp_path / "runtime" / "python.exe"; python.parent.mkdir(); python.write_text("")
    (package / "05_pipeline_local" / ".env").write_text(
        f"TENDER_REVIEW_PROJECT_ROOT={relocated}\n"
        f"TENDER_REVIEW_OUTPUT_ROOT={tmp_path / 'custom_output'}\n"
        f"TENDER_REVIEW_DOCLING_PYTHON={python}\n",
        encoding="utf-8",
    )
    cfg = config_dir / "pipeline.yaml"
    cfg.write_text(
        "project:\n  root: ${TENDER_REVIEW_PROJECT_ROOT:-../..}\n"
        "paths:\n  output_root: ${TENDER_REVIEW_OUTPUT_ROOT:-out}\n  rule_store: rule.json\n"
        "  retrieval_config: retrieval.json\n  prompt_program: prompt.json\n"
        "projects:\n  docling: docling\n  parser: parser\n  chunk: chunk\n  retrieval: retrieval\n  judge: judge\n"
        "runtime:\n  python: null\n  docling_python_env: TENDER_REVIEW_DOCLING_PYTHON\n"
        "env_files:\n  parser: parser.env\n  judge: judge.env\n"
        "client:\n  pass_evidence_top_n: 3\nreview:\n  enabled_review_points: [RULE]\n",
        encoding="utf-8",
    )
    monkeypatch.delenv("TENDER_REVIEW_PROJECT_ROOT", raising=False)
    loaded = load_config(cfg)
    assert loaded.project_root == relocated.resolve()
    assert loaded.output_root == (tmp_path / "custom_output").resolve()
    assert loaded.docling_python == python.resolve()
    assert loaded.chunk_python == Path(sys.executable).resolve()


def test_model_settings_precedence_and_secret_redaction(tmp_path, monkeypatch):
    root = tmp_path / "project"; root.mkdir(); config_dir = tmp_path / "config"; config_dir.mkdir()
    (root / "judge.env").write_text(
        "MODEL_API_BASE=https://example.invalid/v1\nMODEL_API_KEY=secret-value\nMODEL_NAME=file-model\n",
        encoding="utf-8",
    )
    cfg = config_dir / "pipeline.yaml"
    cfg.write_text(
        f"""project:\n  root: {root}\npaths:\n  output_root: out\n  rule_store: r\n  retrieval_config: q\n  prompt_program: p\nprojects:\n  docling: d\n  parser: d\n  chunk: c\n  retrieval: x\n  judge: x\nruntime:\n  python: null\nenv_files:\n  parser: parser.env\n  judge: judge.env\njudge:\n  model: yaml-model\n  temperature: 0\n  max_tokens: 1024\nreview:\n  enabled_review_points: [RULE]\n""",
        encoding="utf-8",
    )
    monkeypatch.setenv("MODEL_NAME", "process-model")
    loaded = load_config(cfg)
    values = loaded.model_environment("judge")
    assert values["MODEL_NAME"] == "process-model"
    assert values["MODEL_MAX_TOKENS"] == "1024"
    assert loaded.public_model_settings("judge")["MODEL_API_KEY"] == "CONFIGURED"
    assert "secret-value" not in json.dumps(loaded.public_model_settings("judge"))


def test_default_yaml_has_no_windows_absolute_path_and_preserves_frozen_defaults():
    project = Path(__file__).resolve().parents[1]
    config_path = project / "config" / "pipeline.local.yaml"
    text = config_path.read_text(encoding="utf-8")
    assert not re.search(r"(?i)(?:^|[\"'])\s*[a-z]:[\\/]", text, re.MULTILINE)
    loaded = load_config(config_path)
    assert loaded.prompt_program_path.parent.name == "06_sample_artifacts"
    assert loaded.rule_store_path.parent.name == "06_sample_artifacts"
    assert loaded.judge_settings["model"] == "qwen3-8b"
    assert loaded.judge_settings["temperature"] == 0
    assert loaded.judge_settings["max_tokens"] == 1024
    assert loaded.parser_settings["model"] == "qwen3.7-flash"
    assert loaded.parser_settings["max_tokens"] == 2500
    assert loaded.client_pass_evidence_top_n == 3
    assert loaded.enabled_review_points == ("RP_REGION_INDUSTRY_AWARD",)


def test_sha_document_id_material_is_deterministic(tmp_path):
    pdf = tmp_path / "a.pdf"; pdf.write_bytes(b"pdf")
    assert sha256_file(pdf) == sha256_file(pdf)


def test_stage_failure_is_explicit(tmp_path):
    obj = object.__new__(PipelineOrchestrator)
    result = obj._execute("parser", "i", "s", "c", [], {}, False,
                          lambda: (_ for _ in ()).throw(RuntimeError("boom")))
    assert result.status == "FAIL" and "boom" in result.error


def _check_config(tmp_path: Path, with_keys: bool = True) -> PipelineConfig:
    projects = {}
    for name in ("docling", "parser", "chunk", "retrieval", "judge"):
        projects[name] = tmp_path / name; projects[name].mkdir()
    required = [projects["docling"] / "run_docling.py", projects["parser"] / "docling_postprocess" / "vlm_cli.py",
                projects["parser"] / "docling_postprocess" / "pf2_pipeline.py", projects["chunk"] / "run_md_chunking.py",
                projects["chunk"] / "run_c11_closure.py", projects["retrieval"] / "run_candidate_retrieval.py",
                projects["judge"] / "src" / "v03_pilot5" / "run_frozen_prompt_judge.py"]
    for path in required: path.parent.mkdir(parents=True, exist_ok=True); path.write_text("")
    rule = tmp_path / "rule.json"; rule.write_text("{}")
    retrieval = tmp_path / "retrieval.json"; retrieval.write_text(json.dumps({"review_item_id": "RULE"}))
    prompt = tmp_path / "prompt.json"; prompt.write_text("{}")
    parser_env = tmp_path / "parser.env"; judge_env = tmp_path / "judge.env"
    parser_env.write_text("PARSER_VLM_API_BASE=x\nPARSER_VLM_API_KEY=x\nPARSER_VLM_MODEL=x\n" if with_keys else "")
    judge_env.write_text("MODEL_API_BASE=x\nMODEL_API_KEY=x\nMODEL_NAME=x\n" if with_keys else "")
    py = Path(sys.executable)
    return PipelineConfig(tmp_path / "cfg.yaml", tmp_path, tmp_path / "out", rule, retrieval, prompt,
                          projects["docling"], projects["parser"], projects["chunk"], projects["retrieval"], projects["judge"],
                          py, py, py, py, py, parser_env, judge_env,
                          enabled_review_points=("RULE",))


def test_check_only_makes_zero_calls_and_accepts_supported_rule(tmp_path):
    pdf = tmp_path / "a.pdf"; pdf.write_bytes(b"pdf")
    def forbidden(*args, **kwargs): raise AssertionError("API/subprocess must not run")
    report = PipelineOrchestrator(_check_config(tmp_path), forbidden).check_only(pdf, "RULE", "D")
    assert report["status"] == "PASS" and report["api_calls"] == 0


def test_check_only_rejects_missing_api_and_unsupported_rule(tmp_path):
    pdf = tmp_path / "a.pdf"; pdf.write_bytes(b"pdf")
    report = PipelineOrchestrator(_check_config(tmp_path, False)).check_only(pdf, "OTHER", "D")
    assert report["status"] == "FAIL"
    assert not report["checks"]["parser_api_configured"] and not report["checks"]["rule_supported"]


def test_combined_report_contains_retrieval_text_and_judge_result(tmp_path):
    root = tmp_path / "DOC_X"
    candidate = {"candidate_id": "K1", "rank": 1, "chunk_id": "C1", "chunk_type": "TEXT",
                 "rule_name": "审查规则", "text": "具体召回条款内容", "page_numbers": [8],
                 "heading_path": ["第三章", "评分办法"], "matched_keywords": ["业绩"]}
    prediction = {"candidate_id": "K1", "片段原文": "带前后文的送审内容", "审查结果": "不违规", "判断理由": "原始理由"}
    validated = {"candidate_id": "K1", "validated_result": "PASS", "raw_result": "PASS",
                 "reason": "最终判断原因", "validation_status": "VALIDATED_PASS", "anchor_page_numbers": [8]}
    jsonl(root / "04_retrieval" / "candidate_top50.jsonl", [candidate])
    jsonl(root / "04_retrieval" / "judge_inputs.jsonl", [{"rule_context": "规则完整内容"}])
    jsonl(root / "05_judge" / "predictions.jsonl", [prediction])
    jsonl(root / "05_judge" / "validated_predictions.jsonl", [validated])
    write_json(root / "05_judge" / "final_review_result.json", {"审查结果": "PASS"})
    write_json(root / "client_content_result.json", {"result": [{
        "term": ["违规条目A"], "reason": "接口审查结论", "text": ["接口相关原文"], "page": [8],
        "is_pass": True, "name": "审查规则", "rule": "规则说明", "policy": "政策依据",
        "type": "资质加分审查", "risk_level": "高", "index": 1,
    }], "suggestions": "修改建议", "scope": "切分审核"})
    write_json(root / "client_api_response.json", {
        "usage": {"prompt_tokens": 100, "completion_tokens": 20, "total_tokens": 120}
    })
    result = build_combined_report(root)
    rendered = result.read_text(encoding="utf-8")
    assert all(text in rendered for text in (
        "具体召回条款内容", "最终判断原因", "规则完整内容", "PDF页码", "第三章",
        "甲方接口审查结果", "接口审查结论", "接口相关原文", "违规条目A", "修改建议",
        "政策依据", "切分审核", "合计 120 tokens",
    ))
