import json
from pathlib import Path
def test_regression_catalog_has_twenty_cases():
    p=Path(__file__).parent/"fixtures/stage2_regression_cases.json"; assert len(json.loads(p.read_text(encoding="utf-8")))>=20
