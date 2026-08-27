from schemas.extraction import FitmentRow
from matching.engine import VehicleIndex, match_row, model_matches

def V(id, year, make, model, disp=None, ind=None, cyl=None):
    return {"id": id, "year": year, "make": make, "model": model,
            "displacement_l": disp, "induction": ind, "cylinders": cyl}

VEHICLES = [
    V("v1", 2016, "Chevrolet", "Silverado 15 Hybrid", 5.3, "NA", 8),
    V("v2", 1999, "Toyota", "Celica", 1.8, "NA", 4),
    V("v3", 1999, "Ford", "Ranger", 3.0, "NA", 6),
]

def test_model_bidirectional():
    assert model_matches("Silverado", "Silverado 15 Hybrid")   # vehicle more specific
    assert model_matches("Celica GT", "Celica")                # fitment more specific

def test_conjunction_guard():
    assert not model_matches("Rangerand Explorer", "Ranger")

def test_engine_tiers():
    idx = VehicleIndex(VEHICLES)
    exact = FitmentRow(make="Chevrolet", model="Silverado", year_start=2016, year_end=2016,
                       displacement_l=5.3, induction="NA", cylinders=8)
    assert match_row(exact, idx) == [(VEHICLES[0], "exact")]
    med = FitmentRow(make="Chevrolet", model="Silverado", year_start=2016, year_end=2016,
                     displacement_l=5.4)
    assert match_row(med, idx)[0][1] == "medium"
    gen = FitmentRow(make="Toyota", model="Celica", year_start=1999, year_end=1999)
    assert match_row(gen, idx)[0][1] == "generic"

def test_year_window():
    idx = VehicleIndex(VEHICLES)
    row = FitmentRow(make="Toyota", model="Celica", year_start=2005, year_end=2010)
    assert match_row(row, idx) == []
