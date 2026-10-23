from __future__ import annotations

from dataclasses import dataclass
from html.parser import HTMLParser


@dataclass
class Cell:
    text: str
    rowspan: int = 1
    colspan: int = 1
    is_header: bool = False


@dataclass
class ParsedTable:
    headers: list[str]
    rows: list[list[str]]
    raw_row_count: int


class _TableHTMLParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.rows: list[list[Cell]] = []
        self.row: list[Cell] | None = None
        self.cell: Cell | None = None
        self.parts: list[str] = []

    def handle_starttag(self, tag, attrs):
        tag = tag.lower()
        attrs = dict(attrs)
        if tag == "tr":
            self.row = []
        elif tag in ("td", "th") and self.row is not None:
            def positive_int(value):
                try:
                    return max(1, int(value or 1))
                except ValueError:
                    return 1
            self.cell = Cell("", positive_int(attrs.get("rowspan")),
                             positive_int(attrs.get("colspan")), tag == "th")
            self.parts = []
        elif tag == "br" and self.cell is not None:
            self.parts.append("\n")

    def handle_data(self, data):
        if self.cell is not None:
            self.parts.append(data)

    def handle_endtag(self, tag):
        tag = tag.lower()
        if tag in ("td", "th") and self.cell is not None and self.row is not None:
            self.cell.text = " ".join("".join(self.parts).split())
            self.row.append(self.cell)
            self.cell = None
            self.parts = []
        elif tag == "tr" and self.row is not None:
            self.rows.append(self.row)
            self.row = None


def _expand(rows: list[list[Cell]]) -> list[list[str]]:
    grid: list[list[str | None]] = []
    for r_index, row in enumerate(rows):
        while len(grid) <= r_index:
            grid.append([])
        col = 0
        for cell in row:
            while col < len(grid[r_index]) and grid[r_index][col] is not None:
                col += 1
            for rr in range(r_index, r_index + cell.rowspan):
                while len(grid) <= rr:
                    grid.append([])
                needed = col + cell.colspan
                if len(grid[rr]) < needed:
                    grid[rr].extend([None] * (needed - len(grid[rr])))
                for cc in range(col, needed):
                    if grid[rr][cc] is None:
                        grid[rr][cc] = cell.text
            col += cell.colspan
    width = max((len(row) for row in grid), default=0)
    return [[value or "" for value in row + [None] * (width - len(row))] for row in grid]


def parse_html_table(raw_html: str) -> ParsedTable:
    parser = _TableHTMLParser()
    parser.feed(raw_html)
    parser.close()
    matrix = _expand(parser.rows)
    nonempty = [i for i, row in enumerate(matrix) if any(cell.strip() for cell in row)]
    if not nonempty:
        return ParsedTable([], [], len(parser.rows))
    explicit_header = next((i for i, row in enumerate(parser.rows)
                            if any(cell.is_header for cell in row)), None)
    header_index = explicit_header if explicit_header is not None else nonempty[0]
    headers = matrix[header_index]
    headers = [header.strip() or f"列{i + 1}" for i, header in enumerate(headers)]
    data_rows = [row for i, row in enumerate(matrix)
                 if i > header_index and any(cell.strip() for cell in row)]
    return ParsedTable(headers, data_rows, len(parser.rows))


def is_noncontent_empty_table(raw_html: str) -> bool:
    """Return True only when an HTML table contains no usable cell or free text.

    This is intentionally stricter than ``not parsed.rows``: a header-only table
    is content, while ``<table></table>`` and tables containing only empty cells
    are structural artifacts.
    """
    class _TableStructureValidator(HTMLParser):
        structural = {"table", "thead", "tbody", "tfoot", "tr", "td", "th"}

        def __init__(self):
            super().__init__(convert_charrefs=True)
            self.stack: list[str] = []
            self.table_count = 0
            self.valid = True

        def handle_starttag(self, tag, attrs):
            tag = tag.lower()
            if tag in self.structural:
                self.stack.append(tag)
                if tag == "table":
                    self.table_count += 1

        def handle_endtag(self, tag):
            tag = tag.lower()
            if tag in self.structural:
                if not self.stack or self.stack[-1] != tag:
                    self.valid = False
                else:
                    self.stack.pop()

    validator = _TableStructureValidator()
    validator.feed(raw_html)
    validator.close()
    if not validator.valid or validator.stack or validator.table_count == 0:
        return False

    parser = _TableHTMLParser()
    parser.feed(raw_html)
    parser.close()
    cell_texts = [cell.text.strip() for row in parser.rows for cell in row]
    if any(cell_texts):
        return False

    class _VisibleTextParser(HTMLParser):
        def __init__(self):
            super().__init__(convert_charrefs=True)
            self.parts: list[str] = []

        def handle_data(self, data):
            self.parts.append(data)

    visible = _VisibleTextParser()
    visible.feed(raw_html)
    visible.close()
    return not "".join(visible.parts).strip()


def serialize_row(headers: list[str], row: list[str]) -> str:
    width = max(len(headers), len(row))
    output = []
    for i in range(width):
        header = headers[i] if i < len(headers) else f"列{i + 1}"
        value = row[i] if i < len(row) else ""
        output.append(f"{header}：{value}")
    return "\n".join(output)
