import unittest

from md_chunking.table_parser import parse_html_table, serialize_row


class TableTests(unittest.TestCase):
    def test_rowspan_and_colspan_expand(self):
        html = ("<table><tr><th colspan='2'>头</th><th>C</th></tr>"
                "<tr><td rowspan='2'>19</td><td>a</td><td>x</td></tr>"
                "<tr><td>b</td><td>y</td></tr></table>")
        table = parse_html_table(html)
        self.assertEqual(table.headers, ["头", "头", "C"])
        self.assertEqual(table.rows, [["19", "a", "x"], ["19", "b", "y"]])
        self.assertIn("头：19", serialize_row(table.headers, table.rows[1]))

    def test_first_nonempty_row_is_header(self):
        table = parse_html_table("<table><tr><td></td></tr><tr><td>A</td></tr><tr><td>B</td></tr></table>")
        self.assertEqual(table.headers, ["A"])
        self.assertEqual(table.rows, [["B"]])
