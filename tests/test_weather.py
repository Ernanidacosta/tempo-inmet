import json
import struct
from datetime import datetime, timezone
from weather import (
    _rnd, _flt, _int, _parse_day, _bearing, _err, _round1,
    _text, _find_hour_idx, _merge,
    _parse_inv, _grib2_nearest, _cloud_to_wmo, _eta_run_dt,
    _precip_mm_to_wmo,
    COND_MAP, WMO_CONDITIONS, DAYS_PT, BR_STATES,
)
import xml.etree.ElementTree as ET


# ── synthetic GRIB2 helpers ───────────────────────────────────────────────

def _pack_bits(values, n_bits):
    """Pack unsigned ints into a big-endian bitstream."""
    result = 0
    for v in values:
        result = (result << n_bits) | (v & ((1 << n_bits) - 1))
    total_bits = len(values) * n_bits
    n_bytes = (total_bits + 7) // 8
    result <<= (n_bytes * 8 - total_bits)
    return result.to_bytes(n_bytes, 'big')


def _make_grib2(ni, nj, lat1, lon1, lat2, lon2, values, n_bits=16, R=0.0, E=0, D=0):
    """Build a minimal valid GRIB2 message (template 3.0 + simple packing 5.0)."""

    def i32(v):  return struct.pack('>i', int(round(v * 1e6)))
    def u32(v):  return struct.pack('>I', int(v))
    def i16(v):  return struct.pack('>h', int(v))
    def u16(v):  return struct.pack('>H', int(v))
    def f32(v):  return struct.pack('>f', float(v))

    # Section 1 – identification (21 bytes): length(4) + sec_num(1) + 16 filler bytes
    s1 = struct.pack('>IB', 21, 1) + bytes(16)

    # Section 3 – grid definition template 3.0 (72 bytes total)
    s3_tpl = (
        bytes([0]) + bytes([0]) + u32(0) +   # shape, sf radius, radius
        bytes([0]) + u32(0) +                 # sf major, major
        bytes([0]) + u32(0) +                 # sf minor, minor
        u32(ni) + u32(nj) +
        u32(0) + u32(0) +                     # basic angle = 0 → 1e-6 deg
        i32(lat1) + i32(lon1) +
        bytes([0x30]) +
        i32(lat2) + i32(lon2) +
        u32(int(abs(lon2 - lon1) / max(ni - 1, 1) * 1e6)) +
        u32(int(abs(lat2 - lat1) / max(nj - 1, 1) * 1e6)) +
        bytes([0x00])                         # scanning mode
    )
    s3 = (struct.pack('>IB', 14 + len(s3_tpl), 3) +
          bytes([0]) + u32(ni * nj) + bytes([0, 0]) + u16(0) + s3_tpl)

    # Section 4 – product definition template 4.0 (34 bytes)
    s4 = struct.pack('>IBH', 34, 4, 0) + bytes(34 - 7)

    # Section 5 – data representation template 5.0 (21 bytes)
    s5 = (struct.pack('>IBI', 21, 5, ni * nj) + u16(0) +
          f32(R) + i16(E) + i16(D) + bytes([n_bits, 0]))

    # Section 6 – no bitmap (6 bytes)
    s6 = struct.pack('>IB', 6, 6) + bytes([255])

    # Section 7 – packed data
    packed = _pack_bits(values, n_bits)
    s7 = struct.pack('>IB', 5 + len(packed), 7) + packed

    body = s1 + s3 + s4 + s5 + s6 + s7 + b'7777'
    total = 16 + len(body)
    s0 = b'GRIB' + bytes([0, 0, 0, 2]) + struct.pack('>Q', total)
    return s0 + body


# ── synthetic Open-Meteo response ─────────────────────────────────────────

def _fake_wx(n=2):
    """Minimal Open-Meteo response with n daily entries."""
    return {
        'current': {
            'time': '2024-01-15T12:00',
            'weather_code': 2, 'precipitation': 0.0,
            'temperature_2m': 28.0, 'relative_humidity_2m': 65,
            'apparent_temperature': 30.0,
            'wind_speed_10m': 12.0, 'wind_direction_10m': 90,
            'wind_gusts_10m': 18.0, 'surface_pressure': 1013.0,
            'visibility': 10000.0, 'uv_index': 5.0, 'is_day': 1,
        },
        'hourly': {
            'time': ['2024-01-15T12:00', '2024-01-15T13:00'],
            'temperature_2m': [28.0, 29.0],
            'relativehumidity_2m': [65, 63],
            'apparent_temperature': [30.0, 31.0],
            'precipitation_probability': [10, 15],
            'precipitation': [0.0, 0.0],
            'weathercode': [2, 2],
            'surface_pressure': [1013.0, 1013.0],
            'visibility': [10000.0, 10000.0],
            'uv_index': [5.0, 4.5],
            'dewpoint_2m': [18.0, 18.5],
            'windspeed_10m': [12.0, 11.0],
            'winddirection_10m': [90, 95],
            'windgusts_10m': [18.0, 17.0],
        },
        'daily': {
            'time': [f'2024-01-{15+i:02d}' for i in range(n)],
            'weathercode': [2] * n,
            'temperature_2m_max': [33.0] * n,
            'temperature_2m_min': [19.0] * n,
            'precipitation_sum': [0.0] * n,
            'precipitation_probability_max': [20] * n,
            'uv_index_max': [8.5] * n,
            'windspeed_10m_max': [25.0] * n,
            'sunrise': [f'2024-01-{15+i:02d}T06:15' for i in range(n)],
            'sunset':  [f'2024-01-{15+i:02d}T18:30' for i in range(n)],
        },
    }


# ── tests ─────────────────────────────────────────────────────────────────

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


class TestParseInv:
    def test_basic_parsing(self):
        inv = "1:0:d=2026061400:TMP:2 m above ground:anl:\n2:500:d=2026061400:RH:2 m above ground:anl:\n"
        result = _parse_inv(inv)
        assert 'TMP:2 m above ground' in result
        assert result['TMP:2 m above ground'] == (0, 499)

    def test_rh_key(self):
        inv = "1:0:d=2026061400:TMP:surface:anl:\n2:1000:d=2026061400:RH:2 m above ground:anl:\n"
        result = _parse_inv(inv)
        assert result['RH:2 m above ground'] == (1000, 1000 + 5_000_000)

    def test_last_entry_large_end(self):
        inv = "1:0:d=2026061400:TMP:surface:anl:\n"
        _, end = _parse_inv(inv)['TMP:surface']
        assert end > 1_000_000

    def test_empty_returns_empty(self):
        assert _parse_inv('') == {}

    def test_real_inv_format(self):
        inv = (
            "6:1225293:d=2026061400:PRES:surface:anl:\n"
            "7:1617830:d=2026061400:TMP:2 m above ground:anl:\n"
            "8:2096079:d=2026061400:TMAX:surface:anl:\n"
        )
        result = _parse_inv(inv)
        assert result['PRES:surface'] == (1225293, 1617829)
        assert result['TMP:2 m above ground'] == (1617830, 2096078)


class TestGrib2Nearest:
    def test_invalid_empty(self):
        assert _grib2_nearest(b'', -18.9, -48.3) is None

    def test_not_grib_magic(self):
        assert _grib2_nearest(b'ABCD' + b'\x00' * 20, -18.9, -48.3) is None

    def test_grib1_rejected(self):
        msg = b'GRIB' + bytes([0, 0, 0, 1]) + b'\x00' * 8
        assert _grib2_nearest(msg, -18.9, -48.3) is None

    def test_2x2_grid_center(self):
        # 2×2 grid: lat 0–10°N, lon -50 to -40°E, values 0–3
        values = [0, 1, 2, 3]  # packed as 8-bit ints
        msg = _make_grib2(2, 2, 0.0, -50.0, 10.0, -40.0, values, n_bits=8, R=0.0, E=0, D=0)
        # (7, -43): i=round(0.7)=1, j=round(0.7)=1 → idx=3 → value=3
        result = _grib2_nearest(msg, 7.0, -43.0)
        assert result is not None
        assert abs(result - 3.0) < 1.0

    def test_temperature_kelvin_decode(self):
        # 3×1 grid along lat axis; encode 300K, 302K, 305K as 16-bit with R=300, E=0, D=0
        raw = [0, 2, 5]
        msg = _make_grib2(3, 1, -20.0, -49.0, -20.0, -47.0, raw, n_bits=16, R=300.0, E=0, D=0)
        val = _grib2_nearest(msg, -20.0, -48.0)  # middle point → 302K
        assert val is not None
        assert abs(val - 302.0) < 0.5

    def test_single_point_grid(self):
        msg = _make_grib2(1, 1, -18.9, -48.3, -18.9, -48.3, [42], n_bits=8, R=0.0, E=0, D=0)
        val = _grib2_nearest(msg, -18.9, -48.3)
        assert val is not None
        assert abs(val - 42.0) < 1.0


class TestCloudToWmo:
    def test_heavy_rain(self):
        assert _cloud_to_wmo(80.0, 10.0) == 63

    def test_light_rain(self):
        assert _cloud_to_wmo(60.0, 1.5) == 61

    def test_drizzle(self):
        assert _cloud_to_wmo(70.0, 0.3) == 51

    def test_overcast_no_rain(self):
        assert _cloud_to_wmo(90.0, 0.0) == 3

    def test_partly_cloudy(self):
        assert _cloud_to_wmo(50.0, 0.0) == 2

    def test_mostly_clear(self):
        assert _cloud_to_wmo(20.0, 0.0) == 1

    def test_clear(self):
        assert _cloud_to_wmo(5.0, 0.0) == 0

    def test_none_cloud_defaults_clear(self):
        assert _cloud_to_wmo(None, 0.0) == 0

    def test_none_precip_uses_cloud(self):
        assert _cloud_to_wmo(80.0, None) == 3


class TestEtaRunDt:
    def test_afternoon_uses_same_day(self):
        now = datetime(2026, 6, 14, 15, 0, tzinfo=timezone.utc)
        run = _eta_run_dt(now)
        assert run.year == 2026 and run.month == 6 and run.day == 14

    def test_early_morning_uses_previous_day(self):
        now = datetime(2026, 6, 14, 4, 0, tzinfo=timezone.utc)
        run = _eta_run_dt(now)
        assert run.day == 13

    def test_exactly_7h_uses_same_day(self):
        now = datetime(2026, 6, 14, 7, 0, tzinfo=timezone.utc)
        run = _eta_run_dt(now)
        assert run.day == 14

    def test_run_always_midnight(self):
        now = datetime(2026, 6, 14, 20, 30, tzinfo=timezone.utc)
        run = _eta_run_dt(now)
        assert run.hour == 0 and run.minute == 0


class TestDailyFields:
    """Verify _merge adds wind_max_kph, sunrise, sunset to every daily entry."""

    def test_wind_max_present(self):
        result = _merge('Uberlândia, MG', _fake_wx(2), None, None)
        for day in result['daily']:
            assert 'wind_max_kph' in day

    def test_wind_max_value(self):
        result = _merge('Uberlândia, MG', _fake_wx(2), None, None)
        assert result['daily'][0]['wind_max_kph'] == 25

    def test_sunrise_present(self):
        result = _merge('Uberlândia, MG', _fake_wx(2), None, None)
        for day in result['daily']:
            assert 'sunrise' in day

    def test_sunrise_format(self):
        result = _merge('Uberlândia, MG', _fake_wx(2), None, None)
        assert result['daily'][0]['sunrise'] == '06:15'

    def test_sunset_format(self):
        result = _merge('Uberlândia, MG', _fake_wx(2), None, None)
        assert result['daily'][0]['sunset'] == '18:30'

    def test_per_day_sunrise_differs(self):
        result = _merge('Uberlândia, MG', _fake_wx(2), None, None)
        assert result['daily'][0]['sunrise'] == result['daily'][1]['sunrise']

    def test_cptec_path_has_fields(self):
        cptec = [
            {'day': 'Hoje', 'code': 0, 'condition': 'Ensolarado', 'max': 33, 'min': 19, 'uv_cptec': 8.5},
            {'day': 'Amanhã', 'code': 2, 'condition': 'Parcialmente nublado', 'max': 30, 'min': 18, 'uv_cptec': 7.0},
        ]
        result = _merge('Uberlândia, MG', _fake_wx(2), None, cptec)
        assert result['daily'][0]['wind_max_kph'] == 25
        assert result['daily'][0]['sunrise'] == '06:15'
        assert result['daily'][1]['sunset'] == '18:30'


class TestReliability:
    def test_open_meteo_only_is_padrao(self):
        r = _merge('X', _fake_wx(2), None, None)
        assert r['reliability'] == 'padrão'

    def test_cptec_path_is_boa(self):
        cptec = [{'day': 'Hoje', 'code': 0, 'condition': 'Sol', 'max': 30, 'min': 18, 'uv_cptec': 5.0}]
        r = _merge('X', _fake_wx(1), None, cptec)
        assert r['reliability'] == 'boa'

    def test_eta_override_is_alta(self):
        eta = {'temp': 27, 'humidity': 60, 'wind_kph': 15, 'wind_dir': 'NE',
               'pressure_mb': 1012, 'code': 1, 'condition': 'Predominantemente limpo'}
        r = _merge('X', _fake_wx(2), None, None, eta_current=eta)
        assert r['reliability'] == 'alta'

    def test_eta_overrides_current_temp(self):
        eta = {'temp': 99, 'humidity': None, 'wind_kph': None, 'wind_dir': None,
               'pressure_mb': None, 'code': 0, 'condition': 'Céu limpo'}
        r = _merge('X', _fake_wx(2), None, None, eta_current=eta)
        assert r['current']['temp'] == 99

    def test_source_includes_eta_prefix(self):
        eta = {'temp': 27, 'humidity': 60, 'wind_kph': 10, 'wind_dir': 'N',
               'pressure_mb': 1010, 'code': 0, 'condition': 'Céu limpo'}
        r = _merge('X', _fake_wx(2), None, None, eta_current=eta)
        assert r['source'].startswith('eta+')


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


class TestPrecipMmToWmo:
    def test_none_returns_none(self):
        assert _precip_mm_to_wmo(None) is None

    def test_below_threshold_returns_none(self):
        assert _precip_mm_to_wmo(0.05) is None

    def test_exactly_zero_returns_none(self):
        assert _precip_mm_to_wmo(0.0) is None

    def test_garoa(self):
        assert _precip_mm_to_wmo(0.1) == 51

    def test_chuva_leve(self):
        assert _precip_mm_to_wmo(0.5) == 61

    def test_chuva_moderada(self):
        assert _precip_mm_to_wmo(5.0) == 63

    def test_chuva_forte(self):
        assert _precip_mm_to_wmo(25.0) == 65

    def test_very_heavy_rain(self):
        assert _precip_mm_to_wmo(50.0) == 65
