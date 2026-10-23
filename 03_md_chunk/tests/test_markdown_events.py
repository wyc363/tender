import unittest

from md_chunking.markdown_events import UnclosedTableError, parse_markdown


class EventTests(unittest.TestCase):
    def test_heading_text_and_inline_table_order(self):
        source = "# 第 2 章 示例\n\n正文\n\n<table><tr><td>A</td></tr></table>\n\n## 下一节\n尾文"
        events, stats = parse_markdown(source)
        self.assertEqual([e.event_type for e in events], ["HEADING", "TEXT", "TABLE", "HEADING", "TEXT"])
        self.assertEqual(events[1].chapter_heading, "第 2 章 示例")
        self.assertEqual(events[-1].section_heading, "下一节")
        self.assertEqual(stats["nonblank_unassigned_lines"], [])

    def test_multiline_table(self):
        events, _ = parse_markdown("<table>\n<tr><td>x</td></tr>\n</table>")
        self.assertEqual(len(events), 1)
        self.assertEqual((events[0].line_start, events[0].line_end), (1, 3))

    def test_unclosed_table_fails(self):
        with self.assertRaises(UnclosedTableError):
            parse_markdown("正文\n<table><tr><td>x</td></tr>")
