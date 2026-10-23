from src.document_processing.content_units_v3 import parse_clause_start,split_clause_segments,parse_table_html,parse_list_items

def test_numeric_false_positives():
    for text in ["0.03km","3.5%","1.25万元","2026.09","0.5m","4.0m/s"]: assert parse_clause_start(text) is None

def test_clause_and_multi_clause():
    assert parse_clause_start("17.1 投标人应提交材料")["parent"]=="17"
    parts=split_clause_segments("7.1 第一项\n内容\n7.2 第二项")
    assert [x[1]["normalized"] for x in parts]==["7.1","7.2"]
    assert parse_clause_start("3.5. 评标委员会")["normalized"]=="3.5"

def test_table_parser_rowspan():
    rows=parse_table_html('<table><tr><th>项目</th><th>内容</th></tr><tr><td rowspan="2">获奖业绩（0分）</td><td>A</td></tr><tr><td>B</td></tr></table>')
    assert rows[2][0]=="获奖业绩（0分）" and rows[2][1]=="B"

def test_clause_list_markers_not_decimal():
    assert parse_clause_start("1.25万元预算") is None
    assert parse_clause_start("1.1 招标范围")["depth"]==2

def test_clause_list_keeps_all_items():
    items=parse_list_items("7.1 材料如下：\n（1）营业执照\n（2）资格证书")
    assert [x["marker"] for x in items]==["（1）","（2）"]
