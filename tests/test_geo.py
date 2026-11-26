from app.domain.geo import COUNTRIES, is_valid_geo


def test_geo_catalog_has_expected_codes_and_unique_uppercase_values() -> None:
    codes = [code for code, _ in COUNTRIES]
    assert "AU" in codes
    assert "MX" in codes
    assert len(codes) == len(set(codes))
    assert all(code.isupper() and len(code) == 2 for code in codes)
    assert is_valid_geo("AU")
    assert not is_valid_geo("ZZ")
