import json
from weather import (
    _rnd, _flt, _int, _parse_day, _bearing, _err, _round1,
    _text, _find_hour_idx,
    COND_MAP, WMO_CONDITIONS, DAYS_PT, BR_STATES,
)
import xml.etree.ElementTree as ET


class TestHelpers:
    def test_rnd_basic(self):
        assert _rnd("28.7") == 29
        assert _rnd(28.4) == 28
        assert _rnd(None) is None

    def test_rnd_invalid(self):
        assert _rnd("abc") is None
        assert _rnd("") is None

    def test_flt_basic(self):
        assert _flt("12.5") == 12.5
        assert _flt(0) == 0.0
        assert _flt(None) is None

    def test_flt_default(self):
        assert _flt(None, 0) == 0
        assert _flt("bad", 9.9) == 9.9

    def test_int_basic(self):
        assert _int("20") == 20
        assert _int(0) == 0
        assert _int(None) == 0

    def test_int_float_string(self):
        assert _int("80.0") == 80

    def test_round1(self):
        assert _round1(5.67) == 5.7
        assert _round1(None) is None
        assert _round1("bad") is None
        assert _round1(0) == 0.0

    def test_bearing_cardinal(self):
        assert _bearing(0) == "N"
        assert _bearing(90) == "L"
        assert _bearing(180) == "S"
        assert _bearing(270) == "O"

    def test_bearing_intercardinal(self):
        assert _bearing(45) == "NE"
        assert _bearing(135) == "SE"
        assert _bearing(225) == "SO"
        assert _bearing(315) == "NO"

    def test_bearing_wraps(self):
        assert _bearing(360) == "N"


class TestParseDay:
    def test_today(self):
        assert _parse_day("2024-01-15", 0) == "Hoje"

    def test_tomorrow(self):
        assert _parse_day("2024-01-16", 1) == "Amanhã"

    def test_monday(self):
        assert _parse_day("2024-01-15", 2) == "Seg"

    def test_sunday(self):
        assert _parse_day("2024-01-14", 2) == "Dom"

    def test_saturday(self):
        assert _parse_day("2024-01-20", 2) == "Sáb"

    def test_fallback_on_empty(self):
        assert _parse_day("", 3) in DAYS_PT

    def test_fallback_on_invalid(self):
        assert _parse_day("nope", 4) in DAYS_PT

    def test_datetime_with_time_suffix(self):
        assert _parse_day("2024-01-15 00:00:00", 2) == "Seg"


class TestFindHourIdx:
    def test_exact_match(self):
        times = ["2024-01-15T00:00", "2024-01-15T01:00", "2024-01-15T13:00"]
        assert _find_hour_idx("2024-01-15T13:15", times) == 2

    def test_before_all(self):
        times = ["2024-01-15T10:00", "2024-01-15T11:00"]
        assert _find_hour_idx("2024-01-15T09:00", times) == 0

    def test_empty_times(self):
        assert _find_hour_idx("2024-01-15T12:00", []) == 0

    def test_empty_cw_time(self):
        assert _find_hour_idx("", ["2024-01-15T00:00"]) == 0

    def test_past_all_slots(self):
        times = ["2024-01-15T00:00", "2024-01-15T01:00"]
        result = _find_hour_idx("2024-01-15T23:00", times)
        assert result == len(times) - 1


class TestTextHelper:
    def test_existing_tag(self):
        elem = ET.fromstring("<root><dia>2024-01-15</dia></root>")
        assert _text(elem, "dia") == "2024-01-15"

    def test_missing_tag(self):
        elem = ET.fromstring("<root></root>")
        assert _text(elem, "dia") is None


class TestCondMap:
    def test_chuva_maps_to_rain(self):
        code, _ = COND_MAP["c"]
        assert code == 61

    def test_trovoada_maps_to_storm(self):
        code, _ = COND_MAP["ct"]
        assert code == 95

    def test_nublado_maps_to_overcast(self):
        code, _ = COND_MAP["n"]
        assert code == 3

    def test_neve_maps_to_snow(self):
        code, _ = COND_MAP["ne"]
        assert code == 73

    def test_ensolarado_maps_to_clear(self):
        code, _ = COND_MAP["e"]
        assert code == 0

    def test_all_entries_valid(self):
        for key, (code, text) in COND_MAP.items():
            assert isinstance(code, int), f"code not int for '{key}'"
            assert text, f"empty text for '{key}'"


class TestWmoConditions:
    def test_clear_sky(self):
        assert "limpo" in WMO_CONDITIONS[0].lower()

    def test_thunderstorm(self):
        assert "trovoada" in WMO_CONDITIONS[95].lower()

    def test_no_empty_strings(self):
        for code, text in WMO_CONDITIONS.items():
            assert text, f"empty condition for WMO code {code}"


class TestBrStates:
    def test_sao_paulo(self):
        assert BR_STATES["São Paulo"] == "SP"

    def test_minas_gerais(self):
        assert BR_STATES["Minas Gerais"] == "MG"

    def test_all_two_chars(self):
        for state, abbr in BR_STATES.items():
            assert len(abbr) == 2, f"{state} must be 2 chars"


class TestErrHelper:
    def test_status_code(self):
        assert _err(400, "x")["statusCode"] == 400

    def test_body_json(self):
        body = json.loads(_err(500, "erro")["body"])
        assert body["error"] == "erro"

    def test_content_type(self):
        assert _err(404, "x")["headers"]["Content-Type"] == "application/json"


class TestHandlerValidation:
    def test_missing_params(self):
        from weather import handler
        assert handler({"queryStringParameters": {}}, None)["statusCode"] == 400

    def test_invalid_coords(self):
        from weather import handler
        r = handler({"queryStringParameters": {"lat": "abc", "lon": "xyz"}}, None)
        assert r["statusCode"] == 400

    def test_none_params(self):
        from weather import handler
        assert handler({"queryStringParameters": None}, None)["statusCode"] == 400
