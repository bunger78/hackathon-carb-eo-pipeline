import json, pathlib
from carb.powerbi import parse_listing

def test_parse_listing_fixture():
    payload = json.loads((pathlib.Path(__file__).parent / "fixtures/powerbi_listing.json").read_text())
    rows = parse_listing(payload)
    assert len(rows) == 3
    for r in rows:
        assert r["eo_number"].startswith("D-") and r["pdf_url"].startswith("http")
