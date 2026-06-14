import json
from weather import (
    _rnd, _flt, _int, _parse_day, _bearing, _err,
    INMET_COND, WMO_CONDITIONS, DAYS_PT, BR_STATES,
)


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
        assert _parse_day("2024-01-15", 2) == "Seg"  # 2024-01-15 is a Monday

    def test_sunday(self):
        assert _parse_day("2024-01-14", 2) == "Dom"  # 2024-01-14 is a Sunday

    def test_saturday(self):
        assert _parse_day("2024-01-20", 2) == "Sáb"  # 2024-01-20 is a Saturday

    def test_fallback_on_empty(self):
        result = _parse_day("", 3)
        assert result in DAYS_PT

    def test_fallback_on_invalid(self):
        result = _parse_day("nope", 4)
        assert result in DAYS_PT

    def test_datetime_with_time(self):
        result = _parse_day("2024-01-15 00:00:00", 2)
        assert result == "Seg"


class TestInmetCondMap:
    def test_chuva_maps_to_rain_code(self):
        code, _ = INMET_COND["c"]
        assert code == 63

    def test_trovoada_maps_to_storm_code(self):
        code, _ = INMET_COND["ct"]
        assert code == 95

    def test_nublado_maps_to_overcast(self):
        code, _ = INMET_COND["n"]
        assert code == 3

    def test_neve_maps_to_snow(self):
        code, _ = INMET_COND["ne"]
        assert code == 73

    def test_all_conditions_have_text(self):
        for key, (code, text) in INMET_COND.items():
            assert text, f"empty text for key '{key}'"
            assert isinstance(code, int)


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
            assert len(abbr) == 2, f"{state} abbreviation should be 2 chars"


class TestErrHelper:
    def test_status_code(self):
        r = _err(400, "bad request")
        assert r["statusCode"] == 400

    def test_body_is_json(self):
        r = _err(500, "erro")
        body = json.loads(r["body"])
        assert "error" in body
        assert body["error"] == "erro"

    def test_content_type(self):
        r = _err(404, "not found")
        assert r["headers"]["Content-Type"] == "application/json"


class TestHandlerValidation:
    def test_missing_params(self):
        from weather import handler
        result = handler({"queryStringParameters": {}}, None)
        assert result["statusCode"] == 400

    def test_invalid_coords(self):
        from weather import handler
        result = handler({"queryStringParameters": {"lat": "abc", "lon": "xyz"}}, None)
        assert result["statusCode"] == 400

    def test_none_params(self):
        from weather import handler
        result = handler({"queryStringParameters": None}, None)
        assert result["statusCode"] == 400
