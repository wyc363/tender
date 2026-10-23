"""V0.3 Pilot-5 decision-only experiment."""

EXPERIMENT_FIELDS = (
    "sample_id", "project_id", "review_item", "rule_context", "document_text", "gold_decision"
)
MODEL_INPUT_FIELDS = ("review_item", "rule_context", "document_text")

