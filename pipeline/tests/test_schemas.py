import pytest
from pydantic import ValidationError
from schemas.extraction import Extraction, FitmentRow

def test_minimal_extraction_validates():
    e = Extraction(eo_number="D-123-45", confidence=0.9)
    assert e.fitment == [] and e.supersedes == []

def test_fitment_row_carries_part_numbers():
    r = FitmentRow(part_numbers=["7M1500"], year_start=2016, year_end=2020,
                   make="Chevrolet", model="Silverado 1500", displacement_l=5.3)
    assert r.part_numbers == ["7M1500"]

def test_category_is_constrained():
    with pytest.raises(ValidationError):
        Extraction(eo_number="D-1-1", confidence=0.5, category="wheels")

def test_confidence_bounds():
    with pytest.raises(ValidationError):
        Extraction(eo_number="D-1-1", confidence=1.5)
