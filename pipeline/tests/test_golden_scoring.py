from tools.golden_eval import f1, row_key, score


def test_f1_identical_sets():
    assert f1({"a", "b"}, {"a", "b"}) == 1.0


def test_f1_disjoint_sets():
    assert f1({"a"}, {"b"}) == 0.0


def test_f1_empty_vs_empty():
    assert f1(set(), set()) == 1.0


def test_f1_partial_overlap():
    a, b = {"x", "y", "z"}, {"x", "w"}  # recall 1/3, precision 1/2
    p, r = 1 / 2, 1 / 3
    assert round(f1(a, b), 3) == round(2 * p * r / (p + r), 3)


def test_row_key_casefolds_model():
    assert row_key({"year_start": 1999, "model": "Celica"}) == (1999, "celica")
    assert row_key({"year_start": None, "model": None}) == (None, "")


def test_score_no_extraction():
    expected = {"eo_number": "D-1", "manufacturer": "Acme", "category": "tune",
                "part_numbers": ["A1"],
                "fitment": [{"year_start": 2000, "model": "X", "part_numbers": ["A1"]}]}
    assert score(expected, None) == {"scalar": 0.0, "pn_f1": 0.0, "assoc_f1": 0.0, "fit_delta": -1}


def test_score_hand_built_pair():
    expected = {
        "eo_number": "D-1", "manufacturer": "Acme", "category": "tune",
        "part_numbers": ["A1", "A2"],
        "fitment": [
            {"year_start": 2000, "model": "Civic", "part_numbers": ["A1"]},
            {"year_start": 2001, "model": "Accord", "part_numbers": ["A2"]},
        ],
    }
    got = {
        "eo_number": "D-1", "manufacturer": "Acme", "category": "engine",  # scalar mismatch
        "part_numbers": ["A1", "A3"],  # 1 shared, 1 missed, 1 extra
        "fitment": [
            {"year_start": 2000, "model": "civic", "part_numbers": ["A1"]},  # row key casefold match
            {"year_start": 2001, "model": "Accord", "part_numbers": []},
        ],
    }
    s = score(expected, got)
    assert s["scalar"] == 2 / 3  # eo_number, manufacturer match; category doesn't
    assert s["pn_f1"] == 0.5
    assert s["assoc_f1"] == 0.5  # rows: 1.0 (A1 exact) and 0.0 (A2 vs empty)
    assert s["fit_delta"] == 0
