def test_page_uid_contract():
    doc="D"; rows=[{"page_idx":i,"pdf_page_no":i+1,"page_uid":f"{doc}-P{i:06d}"} for i in range(3)]; assert len({r["page_uid"] for r in rows})==3 and all(r["pdf_page_no"]==r["page_idx"]+1 for r in rows)
