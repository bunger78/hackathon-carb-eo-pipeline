from seed.seed_vehicles import INDUCTION_MAP


def test_na_maps_to_NA():
    assert INDUCTION_MAP.get("na") == "NA"


def test_carb_maps_to_NA():
    assert INDUCTION_MAP.get("carb") == "NA"


def test_diesel_maps_to_none():
    assert INDUCTION_MAP.get("diesel") is None


def test_unknown_maps_to_none():
    assert INDUCTION_MAP.get("rotary", None) is None


def test_turbo_and_supercharged():
    assert INDUCTION_MAP.get("turbo") == "TURBO"
    assert INDUCTION_MAP.get("supercharged") == "SC"
