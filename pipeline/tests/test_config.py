from config import settings

def test_settings_have_sane_defaults():
    assert settings.region == "us-central1"
    assert settings.max_attempts == 3
    assert settings.price_out_per_mtok > settings.price_in_per_mtok
