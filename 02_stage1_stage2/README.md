# Tender Review POC — Milestone A

This directory contains only the text-layer PDF scanner, section locator, review-point section policy, parse planner, tests, and generated MinerU PowerShell commands. It does not invoke MinerU or any LLM.

```powershell
python scripts/run_milestone_a.py --pdf-dir '..\招标文件原件'
pytest -q
```
