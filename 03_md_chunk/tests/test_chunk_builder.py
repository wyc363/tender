import unittest

from md_chunking.chunk_builder import Limits, build_chunks, clause_refs
from md_chunking.markdown_events import parse_markdown


class ChunkTests(unittest.TestCase):
    def test_heading_and_table_are_hard_boundaries(self):
        source = "## 1.标题\n\n1.1 正文\n\n<table><tr><td>H</td></tr><tr><td>V</td></tr></table>\n\n尾文\n\n## 2.标题\n\n2.1 后文"
        events, _ = parse_markdown(source)
        chunks, _ = build_chunks(events, Limits())
        self.assertEqual([c["chunk_type"] for c in chunks], ["TEXT", "TABLE", "TEXT", "TEXT"])
        self.assertEqual([c["section_heading"] for c in chunks], ["1.标题", "1.标题", "1.标题", "2.标题"])

    def test_heading_not_clause_and_order_preserved(self):
        self.assertEqual(clause_refs("## 5.标题"), [])
        self.assertEqual(clause_refs("5.2 二\n5.1 一\n5.2 再次"), ["5.2", "5.1"])

    def test_long_table_row_repeats_identity(self):
        content = "句子。" * 800
        source = f"## 表\n<table><tr><td>项号</td><td>条款</td><td>内容</td></tr><tr><td>10</td><td>3.1</td><td>{content}</td></tr></table>"
        events, _ = parse_markdown(source)
        chunks, _ = build_chunks(events, Limits())
        self.assertGreater(len(chunks), 1)
        for chunk in chunks:
            self.assertIn("项号：10", chunk["text"])
            self.assertIn("条款：3.1", chunk["text"])
            self.assertIn("table_row_part", chunk)
            self.assertLessEqual(chunk["char_count"], 1800)

    def test_header_only_table_still_becomes_chunk(self):
        events, _ = parse_markdown("<table><tr><td>唯一内容</td></tr></table>")
        chunks, _ = build_chunks(events, Limits())
        self.assertEqual(len(chunks), 1)
        self.assertEqual(chunks[0]["chunk_type"], "TABLE")
        self.assertIn("唯一内容", chunks[0]["text"])
        self.assertEqual((chunks[0]["table_row_start"], chunks[0]["table_row_end"]), (0, 0))

    def test_invalid_limits(self):
        with self.assertRaises(ValueError):
            Limits(target=100, soft_max=50).validate()
