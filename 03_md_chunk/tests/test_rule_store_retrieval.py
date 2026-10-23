import json
import tempfile
import unittest
from pathlib import Path

from retrieval.context import add_context_windows
from retrieval.rule_store import compile_rule_store_config


TARGET = "地域/行业业绩奖项及外部荣誉作为招投标加分、中标条件"


class RuleStoreCompilerTests(unittest.TestCase):
    def _store_path(self):
        store = {
            TARGET: {
                "rule_context_v2": "边界增强上下文",
                "leaves": [
                    {"规则序号": 1201.0, "审查点名称": TARGET,
                     "违规条目": "*1.XX省业绩得分", "合法条目": "*1.全国平台核验合法",
                     "扫描范围": "评标办法：商务部分"},
                    {"规则序号": 1202.0, "审查点名称": TARGET,
                     "违规条目": "*2.XX省奖项加分", "合法条目": "*1.全国平台核验合法",
                     "扫描范围": "评标办法：商务部分"},
                ],
                "boundary_guidance": [
                    {"review_item": TARGET, "boundary_id": "R2",
                     "text": "允许全国平台核验时不得判为地域限制", "source_row": 1}
                ],
            },
            "另一类规则": {"leaves": []},
        }
        temp = tempfile.TemporaryDirectory()
        path = Path(temp.name) / "rules.json"
        path.write_text(json.dumps(store, ensure_ascii=False), encoding="utf-8")
        return temp, path

    def test_only_selected_review_item_is_compiled_and_legal_examples_are_deduplicated(self):
        temp, path = self._store_path()
        self.addCleanup(temp.cleanup)
        config, manifest = compile_rule_store_config(path, TARGET, {
            "review_item_id": "RP", "queries": [], "scan_scope_terms": []
        })
        self.assertEqual(manifest["selected_review_item_count"], 1)
        self.assertEqual(manifest["available_review_item_count"], 2)
        self.assertEqual(manifest["risk_query_count"], 2)
        self.assertEqual(manifest["boundary_query_count"], 1)
        self.assertEqual(config["legal_examples"], ["全国平台核验合法"])
        self.assertEqual(config["rule_context_v2"], "边界增强上下文")
        self.assertIn("【违规规则与示例】", config["judge_rule_context"])
        self.assertIn("【严格判定门槛】", config["judge_rule_context"])
        self.assertIn("不得因原文没有说明全国平台核验方式而反推存在地域限制", config["judge_rule_context"])
        self.assertIn("规则1201.0", config["judge_rule_context"])
        self.assertEqual(config["queries"][-1]["weight"], 0.5)
        self.assertNotIn("XX", config["queries"][0]["text"])

    def test_rejects_cross_item_leaf(self):
        temp, path = self._store_path()
        self.addCleanup(temp.cleanup)
        data = json.loads(path.read_text(encoding="utf-8"))
        data[TARGET]["leaves"][0]["审查点名称"] = "另一类规则"
        path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        with self.assertRaises(ValueError):
            compile_rule_store_config(path, TARGET, {"queries": []})


class ContextWindowTests(unittest.TestCase):
    def test_neighbor_text_is_added_without_changing_anchor_text(self):
        chunks = [
            {"document_id": "D", "chunk_id": "C1", "source_order": 1,
             "source_line_start": 1, "source_line_end": 2, "text": "评分项目：企业业绩"},
            {"document_id": "D", "chunk_id": "C2", "source_order": 2,
             "source_line_start": 3, "source_line_end": 4, "text": "本省业绩得2分"},
            {"document_id": "D", "chunk_id": "C3", "source_order": 3,
             "source_line_start": 5, "source_line_end": 6, "text": "下一评分项"},
        ]
        candidate = {"chunk_id": "C2", "text": "本省业绩得2分"}
        add_context_windows([candidate], chunks, 1)
        self.assertEqual(candidate["text"], "本省业绩得2分")
        self.assertEqual(candidate["context_chunk_ids"], ["C1", "C2", "C3"])
        self.assertIn("评分项目：企业业绩", candidate["document_text"])

    def test_complete_table_anchor_does_not_expand_neighbors(self):
        chunks = [
            {"document_id": "D", "chunk_id": "C1", "source_order": 1, "chunk_type": "TABLE",
             "source_line_start": 1, "source_line_end": 1, "text": "前表", "char_count": 2},
            {"document_id": "D", "chunk_id": "C2", "source_order": 2, "chunk_type": "TABLE",
             "source_line_start": 2, "source_line_end": 2, "text": "目标表格条款", "char_count": 6},
            {"document_id": "D", "chunk_id": "C3", "source_order": 3, "chunk_type": "TABLE",
             "source_line_start": 3, "source_line_end": 3, "text": "后表", "char_count": 2},
        ]
        candidate = {"chunk_id": "C2", "text": "目标表格条款"}
        add_context_windows([candidate], chunks, 1, table_neighbors=0)
        self.assertEqual(candidate["context_chunk_ids"], ["C2"])


if __name__ == "__main__":
    unittest.main()
