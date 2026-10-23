from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable

from .client_api_adapter import build_client_api_outputs
from .config import PipelineConfig
from .gates import chunk_generic_gate, doc011_regression, judge_generic_gate, retrieval_generic_gate
from .parser_adapter import run_parser
from .report import build_combined_report
from .utils import (now_iso, read_json, read_jsonl, redact, sha256_file,
                    sha256_value, tree_fingerprint, write_json)


STAGES = ["input", "docling", "parser", "chunk", "retrieval", "judge", "finalize"]


@dataclass
class StageResult:
    stage: str
    status: str
    started_at: str
    finished_at: str
    duration_seconds: float
    artifacts: dict[str, str] = field(default_factory=dict)
    metrics: dict[str, Any] = field(default_factory=dict)
    gate: dict[str, Any] = field(default_factory=dict)
    error: str | None = None
    input_fingerprint: str | None = None
    stage_fingerprint: str | None = None
    config_fingerprint: str | None = None


class PipelineError(RuntimeError):
    pass


class PipelineOrchestrator:
    def __init__(self, config: PipelineConfig, command_runner: Callable[..., subprocess.CompletedProcess] | None = None):
        self.config = config
        self.command_runner = command_runner or subprocess.run
        self._log_path: Path | None = None

    def _log(self, stage: str, message: str) -> None:
        line = f"{now_iso()} [{stage}] {redact(message)}\n"
        if self._log_path:
            self._log_path.parent.mkdir(parents=True, exist_ok=True)
            with self._log_path.open("a", encoding="utf-8") as handle:
                handle.write(line)

    def _run(self, stage: str, command: list[str], cwd: Path) -> subprocess.CompletedProcess:
        self._log(stage, "COMMAND " + " ".join(command))
        environment = os.environ.copy()
        if stage.startswith("parser"):
            environment.update(self.config.model_environment("parser"))
        elif stage.startswith("judge"):
            environment.update(self.config.model_environment("judge"))
        result = self.command_runner(command, cwd=str(cwd), text=True, encoding="utf-8",
                                     errors="replace", capture_output=True, env=environment)
        if result.stdout:
            self._log(stage, "STDOUT " + result.stdout[-8000:])
        if result.stderr:
            self._log(stage, "STDERR " + result.stderr[-8000:])
        if result.returncode:
            raise PipelineError(f"Command failed ({result.returncode}): {' '.join(command)}\n{redact(result.stderr[-2000:])}")
        return result

    def _stage_fingerprint(self, name: str) -> str:
        c = self.config
        mapping = {
            "input": [Path(__file__)],
            "docling": [Path(__file__).with_name("docling_adapter.py")],
            "parser": [c.parser_project / "docling_postprocess", Path(__file__).with_name("parser_adapter.py")],
            "chunk": [c.chunk_project / "run_md_chunking.py", c.chunk_project / "run_c11_closure.py", c.chunk_project / "src"],
            "retrieval": [c.retrieval_project / "run_candidate_retrieval.py", c.retrieval_project / "src"],
            "judge": [c.judge_project / "src" / "v03_pilot5" / "run_frozen_prompt_judge.py",
                      c.retrieval_project / "run_r2_pipeline.py", c.retrieval_project / "src" / "r2"],
            "finalize": [Path(__file__), Path(__file__).with_name("report.py"),
                         Path(__file__).with_name("client_api_adapter.py"),
                         Path(__file__).with_name("review_point_aggregator.py"),
                         Path(__file__).with_name("client_enrichment.py"),
                         Path(__file__).with_name("rule_catalog.py"),
                         Path(__file__).resolve().parents[1] / "config" / "rule_catalog.compiled.json"],
        }
        return tree_fingerprint(mapping[name])

    def _paths(self, document_id: str) -> dict[str, Path]:
        root = self.config.output_root / document_id
        return {"root": root, "input": root / "00_input", "docling": root / "01_docling",
                "parser": root / "02_parser", "chunk": root / "03_chunk",
                "retrieval": root / "04_retrieval", "judge": root / "05_judge"}

    def _can_resume(self, manifest: dict[str, Any], name: str, input_fp: str, stage_fp: str,
                    config_fp: str, artifacts: list[Path]) -> bool:
        old = manifest.get("stages", {}).get(name, {})
        return (old.get("status") in {"PASS", "SKIPPED"}
                and old.get("input_fingerprint") == input_fp
                and old.get("stage_fingerprint") == stage_fp
                and old.get("config_fingerprint") == config_fp
                and all(path.exists() for path in artifacts))

    def _execute(self, name: str, input_fp: str, stage_fp: str, config_fp: str,
                 artifacts: list[Path], manifest: dict[str, Any], resume: bool,
                 fn: Callable[[], tuple[dict[str, str], dict[str, Any], dict[str, Any]]]) -> StageResult:
        start_iso, start = now_iso(), time.perf_counter()
        if resume and self._can_resume(manifest, name, input_fp, stage_fp, config_fp, artifacts):
            old = manifest["stages"][name]
            return StageResult(name, "SKIPPED", start_iso, now_iso(), round(time.perf_counter() - start, 3),
                               old.get("artifacts", {}), {**old.get("metrics", {}), "resumed": True},
                               old.get("gate", {}), input_fingerprint=input_fp,
                               stage_fingerprint=stage_fp, config_fingerprint=config_fp)
        try:
            made, metrics, gate = fn()
            status = "PASS" if gate.get("status", "PASS") == "PASS" else "FAIL"
            result = StageResult(name, status, start_iso, now_iso(), round(time.perf_counter() - start, 3),
                                 made, metrics, gate, input_fingerprint=input_fp,
                                 stage_fingerprint=stage_fp, config_fingerprint=config_fp)
        except Exception as exc:
            result = StageResult(name, "FAIL", start_iso, now_iso(), round(time.perf_counter() - start, 3),
                                 error=redact(str(exc)), input_fingerprint=input_fp,
                                 stage_fingerprint=stage_fp, config_fingerprint=config_fp)
        return result

    def check_only(self, pdf_path: str | Path, rule_id: str, document_id: str | None = None) -> dict[str, Any]:
        pdf = Path(pdf_path).expanduser().resolve()
        c = self.config
        rule_cfg = read_json(c.retrieval_config_path) if c.retrieval_config_path.is_file() else {}
        parser_values = c.model_environment("parser")
        judge_values = c.model_environment("judge")
        parser_env = {name: bool(parser_values.get(name)) for name in
                      ["PARSER_VLM_API_BASE", "PARSER_VLM_API_KEY", "PARSER_VLM_MODEL"]}
        judge_env = {name: bool(judge_values.get(name)) for name in
                     ["MODEL_API_BASE", "MODEL_API_KEY", "MODEL_NAME"]}
        code_paths = [c.docling_project / "run_docling.py", c.parser_project / "docling_postprocess" / "vlm_cli.py",
                      c.parser_project / "docling_postprocess" / "pf2_pipeline.py",
                      c.chunk_project / "run_md_chunking.py", c.chunk_project / "run_c11_closure.py",
                      c.retrieval_project / "run_candidate_retrieval.py",
                      c.judge_project / "src" / "v03_pilot5" / "run_frozen_prompt_judge.py"]
        executable_paths = [c.docling_python, c.parser_python, c.chunk_python, c.retrieval_python, c.judge_python]
        output_writable = True
        try:
            c.output_root.mkdir(parents=True, exist_ok=True)
            probe = c.output_root / ".i1_write_probe"; probe.write_text("ok"); probe.unlink()
        except OSError:
            output_writable = False
        required_config_fields = (
            "project_root", "output_root", "rule_store_path", "retrieval_config_path",
            "prompt_program_path", "docling_project", "parser_project", "chunk_project",
            "retrieval_project", "judge_project", "docling_python", "parser_python",
            "chunk_python", "retrieval_python", "judge_python", "parser_env_file",
            "judge_env_file",
        )
        checks = {
            "pdf_exists": pdf.is_file() and pdf.suffix.lower() == ".pdf",
            "config_complete": all(getattr(c, key) for key in required_config_fields),
            "stage_code_exists": all(path.exists() for path in code_paths),
            "python_runtimes_exist": all(path.is_file() for path in executable_paths),
            "rule_store_exists": c.rule_store_path.is_file(),
            "rule_supported": rule_cfg.get("review_item_id") == rule_id,
            "rule_enabled": rule_id in c.enabled_review_points,
            "prompt_program_exists": c.prompt_program_path.is_file(),
            "parser_api_configured": all(parser_env.values()),
            "judge_api_configured": all(judge_env.values()),
            "output_writable": output_writable,
            "api_calls_zero": True,
        }
        report = {"status": "PASS" if all(checks.values()) else "FAIL", "execution_mode": "CHECK_ONLY",
                  "document_id": document_id, "rule_id": rule_id, "checks": checks, "api_calls": 0,
                  "parser_model": parser_values.get("PARSER_VLM_MODEL", ""),
                  "judge_model": judge_values.get("MODEL_NAME", ""),
                  "pass_evidence_top_n": c.client_pass_evidence_top_n,
                  "enabled_review_points": list(c.enabled_review_points),
                  "parser_api_key": "CONFIGURED" if parser_env["PARSER_VLM_API_KEY"] else "MISSING",
                  "judge_api_key": "CONFIGURED" if judge_env["MODEL_API_KEY"] else "MISSING"}
        write_json(c.output_root / "I1_L_Check_Only_Report.json", report)
        return report

    def run_document(self, pdf_path: str | Path, document_id: str | None, rule_id: str,
                     output_root: str | Path | None = None, resume: bool = True,
                     stop_after: str | None = None, regression_profile: str | None = None,
                     on_stage_change: Callable[[str], None] | None = None) -> dict[str, Any]:
        c = self.config
        pdf = Path(pdf_path).expanduser().resolve()
        if not pdf.is_file():
            raise FileNotFoundError(pdf)
        if rule_id not in c.enabled_review_points:
            raise ValueError(f"Review point is not enabled: {rule_id}")
        pdf_sha = sha256_file(pdf)
        document_id = document_id or f"DOC_{pdf_sha[:8]}"
        if output_root:
            c = PipelineConfig(**{**asdict(c), "output_root": Path(output_root).resolve()})
            self.config = c
        paths = self._paths(document_id)
        for path in paths.values(): path.mkdir(parents=True, exist_ok=True)
        self._log_path = paths["root"] / "pipeline.log"
        manifest_path = paths["root"] / "pipeline_manifest.json"
        manifest = read_json(manifest_path) if resume and manifest_path.is_file() else {"stages": {}}
        config_fp = sha256_value({
            "config_file": sha256_file(c.config_path),
            "parser": c.public_model_settings("parser"),
            "judge": c.public_model_settings("judge"),
            "client_pass_evidence_top_n": c.client_pass_evidence_top_n,
            "enabled_review_points": list(c.enabled_review_points),
        })
        manifest.update({"document_id": document_id, "original_filename": pdf.name, "source_pdf": str(pdf),
                         "pdf_sha256": pdf_sha, "file_size": pdf.stat().st_size,
                         "rule_id": rule_id, "config_path": str(c.config_path), "stages": manifest.get("stages", {})})
        self._log("pipeline", f"START document={document_id} rule={rule_id}")

        source_pdf = paths["input"] / "source.pdf"
        document_json = paths["docling"] / "document.json"
        page_blocks = paths["parser"] / "page_blocks.jsonl"
        good_md = paths["parser"] / "good.md"
        source_map = paths["parser"] / "source_map.jsonl"
        chunks = paths["chunk"] / "chunks.jsonl"
        judge_inputs = paths["retrieval"] / "judge_inputs.jsonl"
        predictions = paths["judge"] / "predictions.jsonl"
        final_result = paths["root"] / "final_review_result.json"
        final_report = paths["root"] / "final_review_report.html"
        client_content = paths["root"] / "client_content_result.json"
        client_response = paths["root"] / "client_api_response.json"
        review_point_result = paths["root"] / "review_point_result.json"
        aggregation_audit = paths["root"] / "client_aggregation_audit.json"
        results: dict[str, StageResult] = {}

        def save(result: StageResult) -> None:
            results[result.stage] = result; manifest["stages"][result.stage] = asdict(result); write_json(manifest_path, manifest)
            self._log(result.stage, f"status={result.status} duration={result.duration_seconds}")
            if result.status == "FAIL":
                write_json(paths["root"] / "pipeline_summary.json", {
                    "document_id": document_id, "filename": pdf.name, "pdf_sha256": pdf_sha,
                    "pipeline_status": "FAILED", "failed_stage": result.stage,
                    "error": result.error or "stage gate failed",
                })
                raise PipelineError(f"{result.stage} failed: {result.error or result.gate}")

        def stopped(name: str) -> bool:
            return stop_after == name

        def notify(name: str) -> None:
            if on_stage_change:
                on_stage_change(name)

        notify("input")
        save(self._execute("input", pdf_sha, self._stage_fingerprint("input"), config_fp, [source_pdf], manifest, resume,
             lambda: self._input_stage(pdf, source_pdf, pdf_sha)))
        if stopped("input"): return self._finish_partial(manifest, paths, results, "input")

        notify("docling")
        save(self._execute("docling", sha256_file(source_pdf), self._stage_fingerprint("docling"), config_fp,
             [document_json], manifest, resume, lambda: self._docling_stage(source_pdf, paths["docling"])))
        if stopped("docling"): return self._finish_partial(manifest, paths, results, "docling")

        parser_input = sha256_value([sha256_file(source_pdf), sha256_file(document_json)])
        notify("parser")
        save(self._execute("parser", parser_input, self._stage_fingerprint("parser"), config_fp,
             [page_blocks, good_md, source_map, paths["parser"] / "final_page_structures.jsonl"], manifest, resume,
             lambda: self._parser_stage(source_pdf, document_json, paths["parser"], resume)))
        if stopped("parser"): return self._finish_partial(manifest, paths, results, "parser")

        notify("chunk")
        save(self._execute("chunk", sha256_value([sha256_file(good_md), sha256_file(source_map)]),
             self._stage_fingerprint("chunk"), config_fp, [chunks], manifest, resume,
             lambda: self._chunk_stage(document_id, paths["parser"], paths["chunk"])))
        if stopped("chunk"): return self._finish_partial(manifest, paths, results, "chunk")

        notify("retrieval")
        save(self._execute("retrieval", sha256_file(chunks), self._stage_fingerprint("retrieval"), config_fp,
             [judge_inputs, paths["retrieval"] / "retrieval_candidates.jsonl"], manifest, resume,
             lambda: self._retrieval_stage(chunks, paths["retrieval"], rule_id)))
        if stopped("retrieval"): return self._finish_partial(manifest, paths, results, "retrieval")

        notify("judge")
        save(self._execute("judge", sha256_value([sha256_file(judge_inputs), sha256_file(c.prompt_program_path)]),
             self._stage_fingerprint("judge"), config_fp,
             [predictions, paths["judge"] / "validated_predictions.jsonl", paths["judge"] / "document_review_result.json"],
             manifest, resume, lambda: self._judge_stage(chunks, judge_inputs, paths["judge"])))
        if stopped("judge"): return self._finish_partial(manifest, paths, results, "judge")

        finalize_input = sha256_value([
            sha256_file(paths["judge"] / "final_review_result.json"),
            sha256_file(paths["judge"] / "document_review_result.json"),
            sha256_file(paths["judge"] / "predictions.jsonl"),
            sha256_file(paths["judge"] / "R2_Judge_Usage.json"),
            sha256_file(paths["retrieval"] / "retrieval_candidates.jsonl"),
            sha256_value(c.public_model_settings("judge")),
        ])
        notify("aggregation")
        save(self._execute("finalize", finalize_input,
             self._stage_fingerprint("finalize"), config_fp,
             [final_result, final_report, client_content, client_response,
              review_point_result, aggregation_audit], manifest, resume,
             lambda: self._finalize_stage(paths, on_stage_change)))

        parser_gate = results["parser"].gate; chunk_gate = results["chunk"].gate; retrieval_gate = results["retrieval"].gate
        regression = None
        if regression_profile:
            if regression_profile != "DOC011": raise ValueError(f"Unsupported regression profile: {regression_profile}")
            regression = doc011_regression(parser_gate, chunk_gate, retrieval_gate)
            write_json(paths["root"] / "I1_L_DOC011_Regression_Report.json", regression)
            if regression["status"] != "PASS": raise PipelineError("DOC011 regression gate failed")
        return self._complete(manifest, paths, results, regression)

    def _input_stage(self, source: Path, target: Path, pdf_sha: str):
        if not target.exists() or sha256_file(target) != pdf_sha:
            shutil.copy2(source, target)
        gate = {"status": "PASS", "checks": {"source_copied": target.is_file(), "sha256_match": sha256_file(target) == pdf_sha}}
        return {"source_pdf": str(target)}, {"pdf_sha256": pdf_sha, "file_size": target.stat().st_size}, gate

    def _docling_stage(self, source_pdf: Path, output: Path):
        work = output / "_work"
        integration_root = Path(__file__).resolve().parents[1]

        self._run(
            "docling",
            [
                str(self.config.docling_python),
                "-m",
                "review_pipeline.docling_adapter",
                "--input",
                str(source_pdf),
                "--output",
                str(work),
            ],
            integration_root,
        )

        generated = work / "document.json"
        if not generated.is_file():
            raise PipelineError("Docling did not produce document.json")

        shutil.copy2(generated, output / "document.json")

        md = generated.with_name("document.md")
        if md.is_file():
            shutil.copy2(md, output / "document.md")

        return (
            {"document_json": str(output / "document.json")},
            {},
            {"status": "PASS", "checks": {"document_json_exists": True}},
        )
    def _parser_stage(self, pdf: Path, document_json: Path, output: Path, resume: bool):
        t0 = time.perf_counter()
        self._run("parser/a1", [str(self.config.parser_python), "-m", "docling_postprocess.vlm_cli",
                  "--pdf", str(pdf), "--json", str(document_json), "--output-dir", str(output), "--check-only"],
                  self.config.parser_project)
        a1_seconds = round(time.perf_counter() - t0, 3)
        t1 = time.perf_counter()
        report = run_parser(pdf, document_json, output, output, self.config.parser_project,
                            self.config.parser_env_file, resume,
                            environment=self.config.model_environment("parser"))
        pf_seconds = round(time.perf_counter() - t1, 3)
        metrics = {"a1_duration_seconds": a1_seconds, "pf2_duration_seconds": pf_seconds,
                   "physical_pages": report["physical_pages"], "active_pages": report["active_pages"],
                   "parser_vlm_calls": report["vlm_attempts"], "parser_tokens": report["total_tokens"],
                   "parser_cost_cny": (report.get("estimated_cost") or {}).get("estimated_cost")}
        artifacts = {key: str(output / name) for key, name in {
            "page_blocks": "page_blocks.jsonl", "page_images": "page_images",
            "final_page_structures": "final_page_structures.jsonl", "good_md": "good.md",
            "source_map": "source_map.jsonl"}.items()}
        return artifacts, metrics, report

    def _chunk_stage(self, document_id: str, parser_dir: Path, output: Path):
        # C1.1 intentionally preserves chunks_raw.jsonl inside one frozen run. When
        # the upstream GoodMD fingerprint changes, the orchestrator owns cache
        # invalidation and must remove the prior run's raw snapshot first.
        for stale in (output / "chunks_raw.jsonl", output / "chunks.jsonl", output / "parse_events.jsonl"):
            if stale.exists():
                stale.unlink()
        self._run("chunk", [str(self.config.chunk_python), str(self.config.chunk_project / "run_md_chunking.py"),
                            "--input", str(parser_dir / "good.md"), "--document-id", document_id,
                            "--output", str(output), "--strict"], self.config.chunk_project)
        self._run("chunk", [str(self.config.chunk_python), str(self.config.chunk_project / "run_c11_closure.py"),
                            "--chunk-dir", str(output), "--good-md", str(parser_dir / "good.md"),
                            "--source-map", str(parser_dir / "source_map.jsonl"),
                            "--page-structures", str(parser_dir / "final_page_structures.jsonl")], self.config.chunk_project)
        gate = chunk_generic_gate(output, parser_dir / "source_map.jsonl")
        write_json(output / "Chunk_Generic_Gate.json", gate)
        return {"chunks_raw": str(output / "chunks_raw.jsonl"), "chunks": str(output / "chunks.jsonl")}, gate, gate

    def _retrieval_stage(self, chunks: Path, output: Path, rule_id: str):
        self._run("retrieval", [str(self.config.retrieval_python), str(self.config.retrieval_project / "run_candidate_retrieval.py"),
                  "--chunks", str(chunks), "--config", str(self.config.retrieval_config_path),
                  "--output", str(output), "--rule-store", str(self.config.rule_store_path),
                  "--review-item", read_json(self.config.retrieval_config_path)["review_item"],
                  "--prompt-program", str(self.config.prompt_program_path)], self.config.retrieval_project)
        gate = retrieval_generic_gate(chunks, output); write_json(output / "Retrieval_Generic_Gate.json", gate)
        artifacts = {name: str(output / name) for name in
                     ("retrieval_candidates.jsonl", "context_enriched_candidates.jsonl", "judge_inputs.jsonl")}
        return artifacts, gate, gate

    def _judge_stage(self, chunks: Path, judge_inputs: Path, output: Path):
        judge_fp = sha256_value({
            "judge_inputs": sha256_file(judge_inputs),
            "prompt_program": sha256_file(self.config.prompt_program_path),
            "judge_code": self._stage_fingerprint("judge"),
            "judge_env_config": sha256_value(self.config.public_model_settings("judge")),
        })
        state_path = output / "judge_resume_state.json"
        prior_fp = read_json(state_path).get("fingerprint") if state_path.is_file() else None
        if prior_fp and prior_fp != judge_fp:
            for name in ("predictions.jsonl", "run_manifest.json", "validated_predictions.jsonl",
                         "findings.jsonl", "document_review_result.json", "final_review_result.json"):
                stale = output / name
                if stale.exists(): stale.unlink()
            for name in ("raw_responses",):
                stale_dir = output / name
                if stale_dir.exists(): shutil.rmtree(stale_dir)
        write_json(state_path, {"fingerprint": judge_fp, "api_key": "CONFIGURED",
                                "resume_requires_exact_fingerprint": True})
        self._run("judge", [str(self.config.judge_python), "-m", "src.v03_pilot5.run_frozen_prompt_judge",
                  "--input", str(judge_inputs), "--program", str(self.config.prompt_program_path),
                  "--output", str(output), "--run"], self.config.judge_project)
        self._run("judge", [str(self.config.judge_python), str(self.config.retrieval_project / "run_r2_pipeline.py"),
                  "--input", str(judge_inputs), "--chunks", str(chunks), "--rule-store", str(self.config.rule_store_path),
                  "--program", str(self.config.prompt_program_path), "--output", str(output), "--postprocess",
                  "--predictions", str(output / "predictions.jsonl"), "--run-manifest", str(output / "run_manifest.json")],
                  self.config.retrieval_project)
        expected = len(read_jsonl(judge_inputs)); gate = judge_generic_gate(output, expected)
        write_json(output / "Judge_Generic_Gate.json", gate)
        usage = read_json(output / "R2_Judge_Usage.json")
        metrics = {**gate, "judge_tokens": usage.get("total_tokens"), "judge_cost_cny": usage.get("estimated_cost_cny")}
        return {name: str(output / name) for name in ("predictions.jsonl", "validated_predictions.jsonl",
                "findings.jsonl", "document_review_result.json", "final_review_result.json")}, metrics, gate

    def _finalize_stage(self, paths: dict[str, Path],
                        on_stage_change: Callable[[str], None] | None = None):
        source = paths["judge"] / "final_review_result.json"; target = paths["root"] / "final_review_result.json"
        shutil.copy2(source, target)
        client_content, client_response = build_client_api_outputs(
            paths["root"], judge_env_file=self.config.judge_env_file, enrich_fail=True,
            pass_evidence_top_n=self.config.client_pass_evidence_top_n,
            model_environment=self.config.model_environment("judge"),
            enrichment_settings=self.config.client_settings,
        )
        if on_stage_change:
            on_stage_change("client_output")
        report = build_combined_report(paths["root"])
        checks = {"final_result_exists": target.is_file(), "combined_html_exists": report.is_file(),
                  "client_content_exists": client_content.is_file(), "client_response_exists": client_response.is_file(),
                  "review_point_result_exists": (paths["root"] / "review_point_result.json").is_file(),
                  "client_aggregation_audit_exists": (paths["root"] / "client_aggregation_audit.json").is_file()}
        return {"final_review_result": str(target), "final_review_report": str(report),
                "client_content_result": str(client_content), "client_api_response": str(client_response),
                "review_point_result": str(paths["root"] / "review_point_result.json"),
                "client_aggregation_audit": str(paths["root"] / "client_aggregation_audit.json")}, {}, {
            "status": "PASS" if all(checks.values()) else "FAIL", "checks": checks}

    def _finish_partial(self, manifest, paths, results, stage):
        summary = {"document_id": manifest["document_id"], "pipeline_status": "STOPPED",
                   "stopped_after": stage, "stages": {k: v.status for k, v in results.items()}}
        write_json(paths["root"] / "pipeline_summary.json", summary); return summary

    def _complete(self, manifest, paths, results, regression):
        parser = results["parser"].metrics; chunk = results["chunk"].gate
        retrieval = results["retrieval"].gate; judge = results["judge"].metrics
        document = read_json(paths["judge"] / "document_review_result.json")
        total_duration = round(sum(r.duration_seconds for r in results.values()), 3)
        total_cost = round(float(parser.get("parser_cost_cny") or 0) + float(judge.get("judge_cost_cny") or 0), 6)
        logical = lambda name: "PASS" if results[name].status in {"PASS", "SKIPPED"} else results[name].status
        summary = {
            "document_id": manifest["document_id"], "filename": manifest["original_filename"],
            "pdf_sha256": manifest["pdf_sha256"], "physical_pages": parser.get("physical_pages"),
            "parser_status": logical("parser"), "parser_vlm_calls": parser.get("parser_vlm_calls"),
            "parser_tokens": parser.get("parser_tokens"), "parser_cost_cny": parser.get("parser_cost_cny"),
            "chunk_status": logical("chunk"), "total_chunks": chunk.get("total_chunks"),
            "text_chunks": chunk.get("text_chunks"), "table_chunks": chunk.get("table_chunks"),
            "retrieval_status": logical("retrieval"), "retrieval_corpus": retrieval.get("retrieval_corpus"),
            "top_k": retrieval.get("top_k_actual"), "judge_status": logical("judge"),
            "judge_inputs": judge.get("judge_inputs"), "raw_pass": read_json(paths["judge"] / "R2_Candidate_Statistics.json").get("raw_pass"),
            "raw_fail": read_json(paths["judge"] / "R2_Candidate_Statistics.json").get("raw_fail"),
            "raw_review": read_json(paths["judge"] / "R2_Candidate_Statistics.json").get("raw_review"),
            "validated_pass": judge.get("validated_pass"), "validated_fail": judge.get("validated_fail"),
            "validated_review": judge.get("validated_review"), "document_result": document["result"],
            "findings": document.get("finding_count", 0), "review_items": document.get("review_required_count", 0),
            "total_duration_seconds": total_duration, "total_api_cost_cny": total_cost,
            "pipeline_status": "COMPLETED", "resumed_stages": [name for name, result in results.items() if result.status == "SKIPPED"],
            "client_content_result": str(paths["root"] / "client_content_result.json"),
            "client_api_response": str(paths["root"] / "client_api_response.json"),
            "review_point_result": str(paths["root"] / "review_point_result.json"),
            "client_aggregation_audit": str(paths["root"] / "client_aggregation_audit.json"),
            "regression": regression,
        }
        write_json(paths["root"] / "pipeline_summary.json", summary)
        manifest["pipeline_status"] = "COMPLETED"; manifest["finished_at"] = now_iso(); write_json(paths["root"] / "pipeline_manifest.json", manifest)
        self._log("pipeline", f"COMPLETED result={document['result']} cost={total_cost}")
        return summary
