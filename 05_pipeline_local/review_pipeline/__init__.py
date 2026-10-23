"""Local single-rule tender review pipeline."""

from typing import Any

__all__ = ["PipelineOrchestrator", "StageResult"]


def __getattr__(name: str) -> Any:
    # Keep package import lightweight so ``python -m review_pipeline.report``
    # does not pre-import the report module through the orchestrator.
    if name in __all__:
        from .orchestrator import PipelineOrchestrator, StageResult
        return {"PipelineOrchestrator": PipelineOrchestrator, "StageResult": StageResult}[name]
    raise AttributeError(name)
