import os
import subprocess
import sys
import unittest
from pathlib import Path

from md_chunking.chunk_builder import Limits, build_chunks
from md_chunking.chunk_provenance import (
    build_heading_context_assignments,
    classify_skipped_noncontent_sources,
    enrich_chunks,
)
from md_chunking.markdown_events import parse_markdown
from md_chunking.quality import quality_report
from md_chunking.table_parser import is_noncontent_empty_table


class C11ProvenanceClosureTests(unittest.TestCase):
    def _parse_build(self, markdown):
        events, stats = parse_markdown(markdown)
        chunks, _ = build_chunks(events, Limits())
        for index, chunk in enumerate(chunks, 1):
            chunk["chunk_id"] = f"D-C{index:06d}"
            chunk["source_order"] = index
        return events, stats, chunks

    def _maps(self):
        return [
            {"render_id": "R1", "semantic_type": "heading", "source_block_ids": ["H1"],
             "page_numbers": [1], "md_line_start": 1, "md_line_end": 1},
            {"render_id": "R2", "semantic_type": "heading", "source_block_ids": ["H2"],
             "page_numbers": [1], "md_line_start": 3, "md_line_end": 3},
            {"render_id": "R3", "semantic_type": "text", "source_block_ids": ["B1"],
             "page_numbers": [1], "md_line_start": 5, "md_line_end": 5},
        ]

    def test_empty_table_tag_is_noncontent(self):
        self.assertTrue(is_noncontent_empty_table("<table></table>"))

    def test_whitespace_table_is_noncontent(self):
        self.assertTrue(is_noncontent_empty_table("<table>\n  \n</table>"))

    def test_empty_cells_are_noncontent(self):
        self.assertTrue(is_noncontent_empty_table("<table><tr><td> </td><th></th></tr></table>"))

    def test_header_only_table_is_content(self):
        self.assertFalse(is_noncontent_empty_table("<table><tr><th>项目</th></tr></table>"))

    def test_body_cell_table_is_content(self):
        self.assertFalse(is_noncontent_empty_table("<table><tr><td>值</td></tr></table>"))

    def test_free_text_inside_table_is_content(self):
        self.assertFalse(is_noncontent_empty_table("<table>说明</table>"))

    def test_malformed_table_with_text_is_not_empty(self):
        self.assertFalse(is_noncontent_empty_table("<table><tr><td>说明"))

    def test_malformed_empty_table_is_not_classified_noncontent(self):
        self.assertFalse(is_noncontent_empty_table("<table><tr><td></table>"))

    def test_empty_table_generates_no_chunk(self):
        _, _, chunks = self._parse_build("<table>\n</table>\n")
        self.assertEqual(chunks, [])

    def test_normal_table_generates_table_chunk(self):
        _, _, chunks = self._parse_build("<table><tr><td>项目</td></tr><tr><td>值</td></tr></table>\n")
        self.assertEqual([c["chunk_type"] for c in chunks], ["TABLE"])

    def test_empty_table_is_registered_as_skipped_source(self):
        events, _, _ = self._parse_build("<table>\n</table>\n")
        maps = [{"render_id": "RT", "semantic_type": "table", "source_block_ids": ["T1"],
                 "source_table_ids": ["T1"], "page_numbers": [2],
                 "md_line_start": 1, "md_line_end": 2}]
        skipped = classify_skipped_noncontent_sources(events, maps)
        self.assertEqual(skipped[0]["classification"], "EMPTY_NONCONTENT_TABLE")

    def test_skipped_source_retains_table_id(self):
        events, _, _ = self._parse_build("<table>\n</table>\n")
        maps = [{"render_id": "RT", "semantic_type": "table", "source_block_ids": ["T1"],
                 "source_table_ids": ["T1"], "page_numbers": [2],
                 "md_line_start": 1, "md_line_end": 2}]
        self.assertEqual(classify_skipped_noncontent_sources(events, maps)[0]["source_table_ids"], ["T1"])

    def test_quality_gate_accepts_accounted_empty_table(self):
        events, stats, chunks = self._parse_build("<table>\n</table>\n")
        report = quality_report("D", events, chunks, stats, "X", Limits())
        self.assertEqual(report["table_event_without_chunk_count"], 0)
        self.assertEqual(report["skipped_noncontent_table_event_count"], 1)

    def test_two_consecutive_headings_attach_to_text(self):
        md = "# 章\n\n## 节\n\n正文\n"
        events, _, chunks = self._parse_build(md)
        assigned, terminal = build_heading_context_assignments(chunks, events, self._maps(), md)
        self.assertEqual([r["render_id"] for r in assigned[chunks[0]["chunk_id"]]], ["R1", "R2"])
        self.assertEqual(terminal, [])

    def test_heading_context_has_source_ids(self):
        md = "# 章\n\n## 节\n\n正文\n"
        events, _, chunks = self._parse_build(md)
        row = enrich_chunks(chunks, self._maps(), md, {1: "content"}, events)[0]
        self.assertEqual(row["heading_context_source_ids"], ["H1", "H2"])

    def test_heading_context_has_render_ids(self):
        md = "# 章\n\n## 节\n\n正文\n"
        events, _, chunks = self._parse_build(md)
        row = enrich_chunks(chunks, self._maps(), md, {1: "content"}, events)[0]
        self.assertEqual(row["heading_context_render_ids"], ["R1", "R2"])

    def test_heading_context_has_texts(self):
        md = "# 章\n\n## 节\n\n正文\n"
        events, _, chunks = self._parse_build(md)
        row = enrich_chunks(chunks, self._maps(), md, {1: "content"}, events)[0]
        self.assertEqual(row["heading_context_texts"], ["章", "节"])

    def test_three_consecutive_headings_are_preserved(self):
        md = "# 章\n\n## 节\n\n### 项\n\n正文\n"
        events, _, chunks = self._parse_build(md)
        maps = [
            {"render_id": f"R{i}", "semantic_type": "heading", "source_block_ids": [f"H{i}"],
             "page_numbers": [1], "md_line_start": line, "md_line_end": line}
            for i, line in enumerate((1, 3, 5), 1)
        ] + [{"render_id": "RB", "semantic_type": "text", "source_block_ids": ["B"],
              "page_numbers": [1], "md_line_start": 7, "md_line_end": 7}]
        assigned, _ = build_heading_context_assignments(chunks, events, maps, md)
        self.assertEqual([r["heading_text"] for r in assigned[chunks[0]["chunk_id"]]], ["章", "节", "项"])

    def test_overwritten_heading_source_id_is_retained(self):
        md = "## 前标题\n\n## 后标题\n\n正文\n"
        events, _, chunks = self._parse_build(md)
        maps = [
            {"render_id": "R10", "semantic_type": "heading", "source_block_ids": ["B010"],
             "page_numbers": [142], "md_line_start": 1, "md_line_end": 1},
            {"render_id": "R11", "semantic_type": "heading", "source_block_ids": ["B011"],
             "page_numbers": [142], "md_line_start": 3, "md_line_end": 3},
            {"render_id": "R12", "semantic_type": "text", "source_block_ids": ["B012"],
             "page_numbers": [142], "md_line_start": 5, "md_line_end": 5},
        ]
        row = enrich_chunks(chunks, maps, md, {142: "content"}, events)[0]
        self.assertEqual(row["heading_context_source_ids"], ["B010", "B011"])
        self.assertEqual(row["heading_source_block_ids"], ["B010", "B011"])

    def test_b010_fixture_preserves_exact_text_as_context_only(self):
        title = "并附上联合体牵头人授权书"
        md = f"## {title}\n\n## 联 合 体 牵 头 人 授 权 书\n\n本授权委托书声明……\n"
        events, _, chunks = self._parse_build(md)
        maps = [
            {"render_id": "R1720", "semantic_type": "heading", "source_block_ids": ["P0142-B010"],
             "page_numbers": [142], "md_line_start": 1, "md_line_end": 1},
            {"render_id": "R1721", "semantic_type": "heading", "source_block_ids": ["P0142-B011"],
             "page_numbers": [142], "md_line_start": 3, "md_line_end": 3},
            {"render_id": "R1722", "semantic_type": "text", "source_block_ids": ["P0142-B012"],
             "page_numbers": [142], "md_line_start": 5, "md_line_end": 5},
        ]
        row = enrich_chunks(chunks, maps, md, {142: "content"}, events)[0]
        self.assertIn(title, row["heading_context_texts"])
        self.assertNotIn(title, row["text"])

    def test_consecutive_headings_do_not_create_heading_chunks(self):
        _, _, chunks = self._parse_build("# 章\n\n## 节\n\n正文\n")
        self.assertEqual(len(chunks), 1)
        self.assertEqual(chunks[0]["text"], "正文")

    def test_heading_context_attaches_to_table(self):
        md = "# 表题\n\n<table><tr><td>项目</td></tr><tr><td>值</td></tr></table>\n"
        events, _, chunks = self._parse_build(md)
        maps = [
            {"render_id": "RH", "semantic_type": "heading", "source_block_ids": ["H"],
             "page_numbers": [1], "md_line_start": 1, "md_line_end": 1},
            {"render_id": "RT", "semantic_type": "table", "source_block_ids": ["T"],
             "source_table_ids": ["T"], "page_numbers": [1], "md_line_start": 3, "md_line_end": 3},
        ]
        assigned, _ = build_heading_context_assignments(chunks, events, maps, md)
        self.assertEqual(assigned[chunks[0]["chunk_id"]][0]["render_id"], "RH")

    def test_terminal_headings_are_structural_audit(self):
        md = "正文\n\n# 末级一\n\n## 末级二\n"
        events, _, chunks = self._parse_build(md)
        maps = [
            {"render_id": "RB", "semantic_type": "text", "source_block_ids": ["B"],
             "page_numbers": [1], "md_line_start": 1, "md_line_end": 1},
            {"render_id": "RH1", "semantic_type": "heading", "source_block_ids": ["H1"],
             "page_numbers": [1], "md_line_start": 3, "md_line_end": 3},
            {"render_id": "RH2", "semantic_type": "heading", "source_block_ids": ["H2"],
             "page_numbers": [1], "md_line_start": 5, "md_line_end": 5},
        ]
        _, terminal = build_heading_context_assignments(chunks, events, maps, md)
        self.assertEqual([r["source_block_ids"][0] for r in terminal], ["H1", "H2"])

    def test_empty_table_does_not_consume_pending_heading(self):
        md = "# 标题\n\n<table>\n</table>\n\n正文\n"
        events, _, chunks = self._parse_build(md)
        maps = [
            {"render_id": "RH", "semantic_type": "heading", "source_block_ids": ["H"],
             "page_numbers": [1], "md_line_start": 1, "md_line_end": 1},
            {"render_id": "RT", "semantic_type": "table", "source_block_ids": ["T"],
             "source_table_ids": ["T"], "page_numbers": [1], "md_line_start": 3, "md_line_end": 4},
            {"render_id": "RB", "semantic_type": "text", "source_block_ids": ["B"],
             "page_numbers": [1], "md_line_start": 6, "md_line_end": 6},
        ]
        assigned, _ = build_heading_context_assignments(chunks, events, maps, md)
        self.assertEqual(assigned[chunks[0]["chunk_id"]][0]["render_id"], "RH")

    def test_cli_imports_without_pythonpath(self):
        root = Path(__file__).resolve().parents[1]
        env = dict(os.environ)
        env.pop("PYTHONPATH", None)
        result = subprocess.run([sys.executable, str(root / "run_md_chunking.py"), "--help"],
                                cwd=root, env=env, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
