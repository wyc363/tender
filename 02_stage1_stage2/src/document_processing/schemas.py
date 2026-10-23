from __future__ import annotations
from pydantic import BaseModel, Field

class SourceSpan(BaseModel):
    pdf_page: int; page_idx: int; bbox: list[float] | None = None; source_file: str; source_block_id: str | None = None

class UnifiedBlock(BaseModel):
    block_id: str; doc_id: str; page_idx: int; pdf_page: int; bbox: list[float] | None = None; reading_order: int | None = None
    parser_type: str; canonical_region: str | None = None; region_confidence: float | None = None
    region_evidence: list[str] = Field(default_factory=list); text_raw: str = ""; text_normalized: str = ""; html: str | None = None
    source_file: str; source_block_ids: list[str] = Field(default_factory=list); alternate_sources: list[str] = Field(default_factory=list)
    review_enabled: bool = True; text_flow_enabled: bool = True; owner_type: str | None = None; owner_id: str | None = None
    quality_flags: list[str] = Field(default_factory=list)

class LogicalUnit(BaseModel):
    unit_id: str; doc_id: str; unit_type: str; text_raw: str; text_normalized: str
    clause_no: str | None = None; clause_title: str | None = None; table_id: str | None = None; table_row_id: str | None = None
    section_path: list[str] = Field(default_factory=list); pdf_page_start: int; pdf_page_end: int
    source_block_ids: list[str]; source_spans: list[SourceSpan]; parent_unit_id: str | None = None
    structure_confidence: float | None = None; quality_flags: list[str] = Field(default_factory=list)

class Chunk(BaseModel):
    chunk_id: str; doc_id: str; file_name: str; chunk_type: str; text_raw: str; text_normalized: str; text_for_retrieval: str
    clause_no: str | None = None; clause_title: str | None = None; table_id: str | None = None; table_row_id: str | None = None
    section_path: list[str] = Field(default_factory=list); pdf_page_start: int; pdf_page_end: int
    source_spans: list[SourceSpan]; source_block_ids: list[str]; parent_unit_id: str | None = None
    prev_chunk_id: str | None = None; next_chunk_id: str | None = None; structure_confidence: float | None = None
    quality_flags: list[str] = Field(default_factory=list); review_enabled: bool = True
    parser_version: str = "mineru-adapter-v1"; pipeline_version: str = "stage2-v1"
