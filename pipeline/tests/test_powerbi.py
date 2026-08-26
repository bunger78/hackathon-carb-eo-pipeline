import json, pathlib
from carb.powerbi import parse_listing, _decode_rows

def test_parse_listing_fixture():
    payload = json.loads((pathlib.Path(__file__).parent / "fixtures/powerbi_listing.json").read_text())
    rows = parse_listing(payload)
    assert len(rows) == 3
    for r in rows:
        assert r["eo_number"].startswith("D-") and r["pdf_url"].startswith("http")


def test_decode_rows_dsr_mechanics():
    """Synthetic DSR exercising: ValueDicts int-index lookup, R-bitmask carry-over
    merge across rows, and malformed rows (null C / non-dict row) that must be
    skipped without crashing and without corrupting the carry-over state used by
    later rows."""
    d0 = ["D-1", "D-2", "D-3", "D-4"]
    d1 = ["https://example.com/shared.pdf", "https://example.com/other.pdf"]

    ds = {
        "ValueDicts": {"D0": d0, "D1": d1},
        "PH": [{"DM0": [
            # row1: both columns resolved via ValueDicts int index -> D-1 / shared.pdf
            {"C": [0, 0], "R": 0},
            # row2: R=2 (bit1 set) carries col1 (url) from row1; col0 (eo) is new -> D-2 / shared.pdf
            {"C": [1], "R": 2},
            # row3: malformed row ("C": null) -- legacy code crashed with TypeError: list(None).
            # R=3 fully carries both columns from row2, so this must decode identically
            # to row2 (D-2 / shared.pdf) instead of crashing.
            {"C": None, "R": 3},
            # row4: malformed row -- not a dict at all (legacy crashed with AttributeError
            # on row.get). Must be skipped WITHOUT updating the carry-over state.
            "not-a-row",
            # row5: R=2 again, carries col1 (url) forward. If row4's skip had corrupted
            # prev_c (e.g. reset to []), this row would decode nothing at all instead of
            # D-3 / shared.pdf -- so this assertion proves the carry-over survived intact.
            {"C": [2], "R": 2},
            # row6: fresh pair, no carry -- proves D1 index 1 (not just index 0) resolves.
            {"C": [3, 1], "R": 0},
        ]}],
    }

    assert _decode_rows(ds) == {
        "D-1": "https://example.com/shared.pdf",
        "D-2": "https://example.com/shared.pdf",
        "D-3": "https://example.com/shared.pdf",
        "D-4": "https://example.com/other.pdf",
    }
