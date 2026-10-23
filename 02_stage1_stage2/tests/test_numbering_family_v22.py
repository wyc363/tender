from src.document_processing.heading_numbering import parse_numbering

def test_required_numbering_families():
    cases={"第2章 合同":"CHAPTER","第 3 节 条款":"SECTION","（七）合同授予":"CHINESE_PAREN","一、总则":"CHINESE_DUNHAO","1.2 范围":"ARABIC_DECIMAL","附件 4：":"ATTACHMENT","附录":"APPENDIX"}
    for text,family in cases.items(): assert parse_numbering(text)["family"]==family
    assert parse_numbering("附件四")["ordinal"]==4

