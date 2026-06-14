import json
import math
import struct
import unicodedata
import urllib.request
import urllib.parse
import xml.etree.ElementTree as ET
from datetime import datetime, timezone, timedelta

DAYS_PT = ['Dom', 'Seg', 'Ter', 'Qua', 'Qui', 'Sex', 'Sáb']

COND_MAP = {
    'e':   (0,  'Ensolarado'),
    'ps':  (1,  'Predomínio de sol'),
    'pc':  (2,  'Predomínio de céu claro'),
    'pn':  (2,  'Parcialmente nublado'),
    'pp':  (3,  'Predominantemente nublado'),
    'ec':  (3,  'Encoberto'),
    'n':   (3,  'Nublado'),
    'np':  (3,  'Nublado com pancadas'),
    'in':  (3,  'Instável'),
    'ni':  (3,  'Nublado e instável'),
    'ci':  (51, 'Garoa isolada'),
    'nc':  (61, 'Nublado com chuva'),
    'c':   (61, 'Chuva'),
    'cv':  (61, 'Chuvoso'),
    'cn':  (61, 'Chuva com nuvens'),
    'cm':  (65, 'Chuva moderada'),
    't':   (95, 'Trovoada'),
    'ct':  (95, 'Chuva com trovoada'),
    'vt':  (95, 'Variável com trovoada'),
    'pt':  (96, 'Pancadas de trovoada'),
    'nv':  (45, 'Nevoeiro'),
    'an':  (45, 'Névoa'),
    'ne':  (73, 'Neve'),
    'g':   (77, 'Geada'),
}

WMO_CONDITIONS = {
    0: 'Céu limpo', 1: 'Predominantemente limpo', 2: 'Parcialmente nublado',
    3: 'Nublado', 45: 'Nevoeiro', 48: 'Nevoeiro com geada',
    51: 'Garoa leve', 53: 'Garoa', 55: 'Garoa intensa',
    61: 'Chuva leve', 63: 'Chuva moderada', 65: 'Chuva forte',
    71: 'Neve leve', 73: 'Neve', 75: 'Neve forte', 77: 'Granizo',
    80: 'Pancadas leves', 81: 'Pancadas de chuva', 82: 'Pancadas fortes',
    95: 'Trovoada', 96: 'Trovoada com granizo', 99: 'Trovoada severa',
}

AQI_LABELS = [(0, 'Ótima'), (20, 'Boa'), (40, 'Moderada'),
              (60, 'Ruim'), (80, 'Muito Ruim'), (100, 'Extremamente Ruim')]

BR_STATES = {
    'Acre': 'AC', 'Alagoas': 'AL', 'Amapá': 'AP', 'Amazonas': 'AM',
    'Bahia': 'BA', 'Ceará': 'CE', 'Distrito Federal': 'DF',
    'Espírito Santo': 'ES', 'Goiás': 'GO', 'Maranhão': 'MA',
    'Mato Grosso': 'MT', 'Mato Grosso do Sul': 'MS', 'Minas Gerais': 'MG',
    'Pará': 'PA', 'Paraíba': 'PB', 'Paraná': 'PR', 'Pernambuco': 'PE',
    'Piauí': 'PI', 'Rio de Janeiro': 'RJ', 'Rio Grande do Norte': 'RN',
    'Rio Grande do Sul': 'RS', 'Rondônia': 'RO', 'Roraima': 'RR',
    'Santa Catarina': 'SC', 'São Paulo': 'SP', 'Sergipe': 'SE', 'Tocantins': 'TO',
}

# ── ETA GRIB2 constants ────────────────────────────────────────────────────

_ETA_BASE = "https://dataserver.cptec.inpe.br/dataserver_modelos/eta/ams_08km/brutos"

# Variables to fetch: (inventory key, output attribute)
_ETA_VARS = [
    ('TMP:2 m above ground',   'tmp_k'),
    ('RH:2 m above ground',    'rh'),
    ('UGRD:10 m above ground', 'ugrd'),
    ('VGRD:10 m above ground', 'vgrd'),
    ('PRES:surface',           'pres_pa'),
    ('APCP:surface',           'apcp_mm'),
    ('LCDC:surface',           'lcdc_pct'),
]


# ── handler ────────────────────────────────────────────────────────────────

def handler(event, context):
    params = event.get('queryStringParameters') or {}
    lat_s, lon_s = params.get('lat'), params.get('lon')
    if not lat_s or not lon_s:
        return _err(400, 'lat e lon são obrigatórios')
    try:
        lat, lon = float(lat_s), float(lon_s)
    except ValueError:
        return _err(400, 'coordenadas inválidas')

    city_name = _reverse_geocode(lat, lon)

    try:
        wx_raw, aq_raw = _fetch_open_meteo(lat, lon)
    except Exception as exc:
        return _err(500, f'erro Open-Meteo: {exc}')

    cptec_days = None
    try:
        city_short = city_name.split(',')[0].strip()
        cptec_days = _fetch_cptec(city_short)
    except Exception:
        pass

    eta_current = None
    try:
        eta_current = _fetch_eta_current(lat, lon)
    except Exception:
        pass

    return {
        'statusCode': 200,
        'headers': {
            'Content-Type': 'application/json; charset=utf-8',
            'Access-Control-Allow-Origin': '*',
        },
        'body': json.dumps(
            _merge(city_name, wx_raw, aq_raw, cptec_days, eta_current),
            ensure_ascii=False,
        ),
    }


# ── ETA GRIB2 integration ──────────────────────────────────────────────────

def _eta_run_dt(now_utc):
    """Latest available ETA 00Z run (ready ~7h after initialization)."""
    run = now_utc.replace(hour=0, minute=0, second=0, microsecond=0)
    if now_utc.hour < 7:
        run -= timedelta(days=1)
    return run


def _parse_inv(text):
    """
    Parse wgrib2 .inv file.
    Returns {var_key: (byte_start, byte_end)} mapping.
    """
    entries = []
    for line in text.strip().splitlines():
        p = line.split(':')
        if len(p) >= 5:
            try:
                entries.append((int(p[1]), f'{p[3]}:{p[4]}'))
            except ValueError:
                pass
    result = {}
    for i, (offset, key) in enumerate(entries):
        end = entries[i + 1][0] - 1 if i + 1 < len(entries) else offset + 5_000_000
        result[key] = (offset, end)
    return result


def _range_get(url, start, end, timeout=9):
    """HTTP Range request — returns only bytes [start, end] of a remote file."""
    req = urllib.request.Request(
        url,
        headers={'Range': f'bytes={start}-{end}', 'User-Agent': 'tempo-app/1.0'},
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def _grib2_nearest(msg: bytes, lat: float, lon: float):
    """
    Pure-Python GRIB2 decoder.
    Extracts the value at the nearest grid point for a single message.
    Handles regular lat/lon grid (template 3.0) + simple packing (template 5.0).
    Returns float or None on failure.
    """
    if len(msg) < 16 or msg[:4] != b'GRIB' or msg[7] != 2:
        return None

    pos = 16  # skip section 0 (always 16 bytes in GRIB2)
    ni = nj = None
    lat1 = lon1 = lat2 = lon2 = None
    R = E = D = n_bits = None
    sec7_start = None

    while pos + 4 <= len(msg):
        # End section is 4 ASCII '7' bytes, not a normal section
        if msg[pos:pos + 4] == b'7777':
            break

        if pos + 5 > len(msg):
            break
        sec_len = struct.unpack('>I', msg[pos:pos + 4])[0]
        if sec_len < 5 or pos + sec_len > len(msg):
            break
        sec_num = msg[pos + 4]

        if sec_num == 3 and pos + 72 <= len(msg):
            tpl = struct.unpack('>H', msg[pos + 12:pos + 14])[0]
            if tpl == 0:  # regular lat/lon
                ni  = struct.unpack('>I', msg[pos + 30:pos + 34])[0]
                nj  = struct.unpack('>I', msg[pos + 34:pos + 38])[0]
                lat1 = struct.unpack('>i', msg[pos + 46:pos + 50])[0] * 1e-6
                lon1 = struct.unpack('>i', msg[pos + 50:pos + 54])[0] * 1e-6
                lat2 = struct.unpack('>i', msg[pos + 55:pos + 59])[0] * 1e-6
                lon2 = struct.unpack('>i', msg[pos + 59:pos + 63])[0] * 1e-6

        elif sec_num == 5 and pos + 21 <= len(msg):
            tpl = struct.unpack('>H', msg[pos + 9:pos + 11])[0]
            if tpl == 0:  # simple packing
                R      = struct.unpack('>f', msg[pos + 11:pos + 15])[0]
                E      = struct.unpack('>h', msg[pos + 15:pos + 17])[0]
                D      = struct.unpack('>h', msg[pos + 17:pos + 19])[0]
                n_bits = msg[pos + 19]

        elif sec_num == 7:
            sec7_start = pos + 5  # data starts 5 bytes into section 7

        pos += sec_len

    if None in (ni, nj, lat1, lon1, lat2, lon2, R, n_bits, sec7_start):
        return None

    # Normalise longitude to match grid convention (0-360 or -180/180)
    if lon1 > 180:
        lon1 -= 360
    if lon2 > 180:
        lon2 -= 360
    req_lon = lon + 360 if lon < lon1 and lon1 > -180 else lon

    dlat = (lat2 - lat1) / (nj - 1) if nj > 1 else 1.0
    dlon = (lon2 - lon1) / (ni - 1) if ni > 1 else 1.0

    i = max(0, min(ni - 1, round((req_lon - lon1) / dlon)))
    j = max(0, min(nj - 1, round((lat - lat1) / dlat)))
    idx = j * ni + i

    if n_bits == 0:
        return float(R) / (10.0 ** D)

    # Unpack bit-packed integer at index idx
    bit_pos  = idx * n_bits
    byte_pos = sec7_start + bit_pos // 8
    bit_off  = bit_pos % 8
    n_bytes  = (bit_off + n_bits + 7) // 8

    if byte_pos + n_bytes > len(msg):
        return None

    raw    = int.from_bytes(msg[byte_pos:byte_pos + n_bytes], 'big')
    shift  = n_bytes * 8 - bit_off - n_bits
    packed = (raw >> shift) & ((1 << n_bits) - 1)

    return (R + packed * (2.0 ** E)) / (10.0 ** D)


def _cloud_to_wmo(lcdc_pct, apcp_mm):
    """Map low-cloud-cover % + 1h precipitation mm → WMO weather code."""
    mm = apcp_mm or 0.0
    if mm > 5.0:
        return 63   # moderate rain
    if mm > 1.0:
        return 61   # light rain
    if mm > 0.1:
        return 51   # drizzle
    pct = lcdc_pct or 0.0
    if pct > 75:
        return 3    # overcast
    if pct > 40:
        return 2    # partly cloudy
    if pct > 15:
        return 1    # mostly clear
    return 0        # clear sky


def _fetch_eta_current(lat, lon):
    """
    Fetch current surface conditions from CPTEC ETA 8km GRIB2 model.
    Returns a dict with temp, humidity, wind, pressure, code — or None if
    the model data is unavailable or decoding fails.
    """
    now = datetime.now(timezone.utc)
    run_dt  = _eta_run_dt(now)
    run_str = run_dt.strftime('%Y%m%d00')

    h_now = max(0, min(48, int((now - run_dt).total_seconds() / 3600)))
    valid_dt  = run_dt + timedelta(hours=h_now)
    valid_str = valid_dt.strftime('%Y%m%d%H')

    base  = f"{_ETA_BASE}/{run_dt.strftime('%Y/%m/%d')}/00"
    fname = f"Eta_ams_08km_{run_str}_{valid_str}"

    try:
        inv_raw = _get_raw(f"{base}/{fname}.inv", timeout=5)
        inv_text = inv_raw.decode('utf-8', errors='replace')
    except Exception:
        return None

    offsets  = _parse_inv(inv_text)
    grib_url = f"{base}/{fname}.grib2"

    raw = {}
    for var_key, attr in _ETA_VARS:
        if var_key not in offsets:
            raw[attr] = None
            continue
        start, end = offsets[var_key]
        try:
            msg = _range_get(grib_url, start, end, timeout=9)
            raw[attr] = _grib2_nearest(msg, lat, lon)
        except Exception:
            raw[attr] = None

    if raw.get('tmp_k') is None:
        return None

    tmp_c   = round(raw['tmp_k'] - 273.15)
    rh      = round(raw['rh']) if raw.get('rh') is not None else None
    ugrd    = raw.get('ugrd') or 0.0
    vgrd    = raw.get('vgrd') or 0.0
    wspd_ms = math.sqrt(ugrd ** 2 + vgrd ** 2)
    wspd_kh = round(wspd_ms * 3.6)
    wdir    = round((270 - math.degrees(math.atan2(vgrd, ugrd))) % 360)
    pres_pa = raw.get('pres_pa')
    apcp    = raw.get('apcp_mm') or 0.0
    lcdc    = raw.get('lcdc_pct')
    code    = _cloud_to_wmo(lcdc, apcp)

    return {
        'temp':        tmp_c,
        'humidity':    rh,
        'wind_kph':    wspd_kh,
        'wind_dir':    _bearing(wdir),
        'pressure_mb': round(pres_pa / 100) if pres_pa else None,
        'code':        code,
        'condition':   WMO_CONDITIONS.get(code, ''),
    }


# ── CPTEC XML ──────────────────────────────────────────────────────────────

def _fetch_open_meteo(lat, lon):
    qs = urllib.parse.urlencode({
        'latitude': f'{lat:.4f}', 'longitude': f'{lon:.4f}',
        'hourly': 'temperature_2m,relativehumidity_2m,apparent_temperature,precipitation_probability,precipitation,weathercode,surface_pressure,visibility,uv_index,dewpoint_2m,windspeed_10m,winddirection_10m,windgusts_10m',
        'daily': 'weathercode,temperature_2m_max,temperature_2m_min,precipitation_sum,precipitation_probability_max,windspeed_10m_max,sunrise,sunset,uv_index_max',
        'current_weather': 'true',
        'timezone': 'America/Sao_Paulo',
        'forecast_days': '7',
    })
    wx = _get_json(f'https://api.open-meteo.com/v1/forecast?{qs}', timeout=10)

    aq = None
    try:
        qs2 = urllib.parse.urlencode({
            'latitude': f'{lat:.4f}', 'longitude': f'{lon:.4f}',
            'hourly': 'pm2_5,european_aqi',
            'timezone': 'America/Sao_Paulo',
            'forecast_days': '1',
        })
        aq = _get_json(f'https://air-quality-api.open-meteo.com/v1/air-quality?{qs2}', timeout=6)
    except Exception:
        pass

    return wx, aq


def _fetch_cptec(city_name_raw):
    def _strip(s):
        return ''.join(c for c in unicodedata.normalize('NFD', s)
                       if unicodedata.category(c) != 'Mn')

    url = f'https://servicos.cptec.inpe.br/XML/listaCidades?city={urllib.parse.quote(_strip(city_name_raw))}'
    xml_bytes = _get_raw(url, timeout=5)
    root = ET.fromstring(xml_bytes.decode('iso-8859-1', errors='replace'))
    cidades = root.findall('cidade')
    if not cidades:
        raise ValueError('CPTEC: cidade não encontrada')

    city_id = cidades[0].find('id').text
    xml2 = _get_raw(f'https://servicos.cptec.inpe.br/XML/cidade/7dias/{city_id}/previsao.xml', timeout=6)
    root2 = ET.fromstring(xml2.decode('iso-8859-1', errors='replace'))

    days = []
    for i, p in enumerate(root2.findall('previsao')[:7]):
        tempo = (_text(p, 'tempo') or '').lower().strip()
        code, cond = COND_MAP.get(tempo, (1, 'Parcialmente nublado'))
        days.append({
            'day': _parse_day(_text(p, 'dia'), i),
            'code': code,
            'condition': cond,
            'max': _rnd(_text(p, 'maxima')),
            'min': _rnd(_text(p, 'minima')),
            'uv_cptec': _flt(_text(p, 'iuv')),
        })
    return days


# ── merge ──────────────────────────────────────────────────────────────────

def _merge(city_name, wx, aq, cptec_days, eta_current=None):
    cw     = wx.get('current_weather', {})
    daily  = wx.get('daily', {})
    hourly = wx.get('hourly', {})

    cw_time = cw.get('time', '')
    h_times = hourly.get('time', [])
    hi      = _find_hour_idx(cw_time, h_times)

    def h(key):
        arr = hourly.get(key) or []
        return arr[hi] if hi < len(arr) else None

    code    = int(cw.get('weathercode', 0))
    vis_m   = h('visibility')
    vis_km  = round(vis_m / 1000, 1) if vis_m is not None else None

    # Air quality
    aqi_val = pm25 = aqi_label = None
    if aq:
        aq_h     = aq.get('hourly', {})
        aq_times = aq_h.get('time', [])
        aq_hi    = _find_hour_idx(cw_time, aq_times)
        aq_aqi   = aq_h.get('european_aqi') or []
        aq_pm    = aq_h.get('pm2_5') or []
        if aq_hi < len(aq_aqi) and aq_aqi[aq_hi] is not None:
            aqi_val   = int(aq_aqi[aq_hi])
            aqi_label = next((lb for thr, lb in reversed(AQI_LABELS) if aqi_val >= thr), 'Ótima')
        if aq_hi < len(aq_pm) and aq_pm[aq_hi] is not None:
            pm25 = round(aq_pm[aq_hi], 1)

    # Sunrise / sunset
    sunrises = daily.get('sunrise', [])
    sunsets  = daily.get('sunset', [])
    sunrise  = sunrises[0][11:16] if sunrises and len(sunrises[0]) >= 16 else None
    sunset   = sunsets[0][11:16]  if sunsets  and len(sunsets[0])  >= 16 else None

    uv_max_list = daily.get('uv_index_max', [])
    uv_max = round(uv_max_list[0], 1) if uv_max_list and uv_max_list[0] is not None else None

    # Hourly output (next 24h from Open-Meteo)
    h_code = hourly.get('weathercode', [])
    h_temp = hourly.get('temperature_2m', [])
    h_pop  = hourly.get('precipitation_probability', [])
    hourly_out = []
    for j in range(hi, min(hi + 24, len(h_times))):
        hourly_out.append({
            'time':        h_times[j][11:16],
            'code':        int(h_code[j]) if j < len(h_code) and h_code[j] is not None else 0,
            'temp':        _rnd(h_temp[j]) if j < len(h_temp) else None,
            'precip_prob': _int(h_pop[j])  if j < len(h_pop)  else 0,
        })

    # Daily output
    d_times    = daily.get('time', [])
    d_wcode    = daily.get('weathercode', [])
    d_max      = daily.get('temperature_2m_max', [])
    d_min      = daily.get('temperature_2m_min', [])
    d_precip   = daily.get('precipitation_sum', [])
    d_pop      = daily.get('precipitation_probability_max', [])
    d_uv       = daily.get('uv_index_max', [])
    d_wind_max = daily.get('windspeed_10m_max', [])

    def _day_sunrise(i):
        return sunrises[i][11:16] if i < len(sunrises) and sunrises[i] and len(sunrises[i]) >= 16 else None

    def _day_sunset(i):
        return sunsets[i][11:16] if i < len(sunsets) and sunsets[i] and len(sunsets[i]) >= 16 else None

    if cptec_days:
        daily_out = []
        for i, d in enumerate(cptec_days):
            daily_out.append({
                'day':          d['day'],
                'code':         d['code'],
                'condition':    d['condition'],
                'max':          d['max'],
                'min':          d['min'],
                'precip_mm':    round(float(d_precip[i]), 1) if i < len(d_precip) and d_precip[i] is not None else 0.0,
                'precip_prob':  _int(d_pop[i]) if i < len(d_pop) else 0,
                'uv_max':       d.get('uv_cptec') or (_round1(d_uv[i]) if i < len(d_uv) and d_uv[i] is not None else None),
                'wind_max_kph': _rnd(d_wind_max[i]) if i < len(d_wind_max) and d_wind_max[i] is not None else None,
                'sunrise':      _day_sunrise(i),
                'sunset':       _day_sunset(i),
            })
        source      = 'cptec+open-meteo'
        reliability = 'boa'
    else:
        daily_out = []
        for i, dt in enumerate(d_times[:7]):
            dc = int(d_wcode[i]) if i < len(d_wcode) and d_wcode[i] is not None else 0
            daily_out.append({
                'day':          _parse_day(dt, i),
                'code':         dc,
                'condition':    WMO_CONDITIONS.get(dc, ''),
                'max':          _rnd(d_max[i] if i < len(d_max) else None),
                'min':          _rnd(d_min[i] if i < len(d_min) else None),
                'precip_mm':    round(float(d_precip[i]), 1) if i < len(d_precip) and d_precip[i] is not None else 0.0,
                'precip_prob':  _int(d_pop[i]) if i < len(d_pop) else 0,
                'uv_max':       _round1(d_uv[i]) if i < len(d_uv) and d_uv[i] is not None else None,
                'wind_max_kph': _rnd(d_wind_max[i]) if i < len(d_wind_max) and d_wind_max[i] is not None else None,
                'sunrise':      _day_sunrise(i),
                'sunset':       _day_sunset(i),
            })
        source      = 'open-meteo'
        reliability = 'padrão'

    # Current conditions (Open-Meteo base)
    wd = cw.get('winddirection')
    current = {
        'temp':          _rnd(cw.get('temperature')),
        'feels_like':    _rnd(h('apparent_temperature')),
        'humidity':      _int(h('relativehumidity_2m')),
        'wind_kph':      _rnd(cw.get('windspeed')),
        'wind_dir':      _bearing(wd) if wd is not None else None,
        'wind_gust_kph': _rnd(h('windgusts_10m')),
        'condition':     WMO_CONDITIONS.get(code, ''),
        'code':          code,
        'visibility_km': vis_km,
        'pressure_mb':   _rnd(h('surface_pressure')),
        'dew_point':     _rnd(h('dewpoint_2m')),
        'uv_index':      _round1(h('uv_index')),
        'is_day':        bool(cw.get('is_day', 1)),
    }

    # Override current conditions with ETA 8km when available
    if eta_current:
        for key in ('temp', 'humidity', 'wind_kph', 'wind_dir',
                    'pressure_mb', 'code', 'condition'):
            if eta_current.get(key) is not None:
                current[key] = eta_current[key]
        source      = f'eta+{source}'
        reliability = 'alta'

    return {
        'city':        city_name,
        'source':      source,
        'reliability': reliability,
        'current':     current,
        'today': {
            'sunrise':   sunrise,
            'sunset':    sunset,
            'uv_max':    uv_max,
            'aqi':       aqi_val,
            'aqi_label': aqi_label,
            'pm25':      pm25,
        },
        'hourly': hourly_out,
        'daily':  daily_out,
    }


# ── helpers ────────────────────────────────────────────────────────────────

def _find_hour_idx(cw_time, times):
    if not times or not cw_time:
        return 0
    cw_h = cw_time[:13]
    for i, t in enumerate(times):
        if t[:13] >= cw_h:
            return i
    return max(0, len(times) - 1)


def _reverse_geocode(lat, lon):
    try:
        url = f'https://nominatim.openstreetmap.org/reverse?lat={lat}&lon={lon}&format=json&accept-language=pt'
        req = urllib.request.Request(url, headers={'User-Agent': 'tempo-app/1.0'})
        with urllib.request.urlopen(req, timeout=5) as r:
            data = json.loads(r.read().decode('utf-8'))
        addr  = data.get('address', {})
        city  = (addr.get('city') or addr.get('town') or addr.get('village')
                 or addr.get('municipality') or '')
        state = BR_STATES.get(addr.get('state', '').strip(), '')
        return f'{city}, {state}' if city and state else city or 'Uberlândia, MG'
    except Exception:
        return 'Uberlândia, MG'


def _get_json(url, timeout=8):
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return json.loads(r.read().decode('utf-8'))


def _get_raw(url, timeout=6):
    req = urllib.request.Request(url, headers={'User-Agent': 'tempo-app/1.0'})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def _text(elem, tag):
    sub = elem.find(tag)
    return sub.text if sub is not None else None


def _parse_day(dt_str, index):
    if index == 0:
        return 'Hoje'
    if index == 1:
        return 'Amanhã'
    try:
        dt = datetime.strptime((dt_str or '')[:10], '%Y-%m-%d')
        return DAYS_PT[(dt.weekday() + 1) % 7]
    except Exception:
        return DAYS_PT[index % 7]


def _bearing(deg):
    dirs = ['N', 'NE', 'L', 'SE', 'S', 'SO', 'O', 'NO']
    return dirs[round(float(deg) / 45) % 8]


def _round1(v):
    try:
        return round(float(v), 1) if v is not None else None
    except (ValueError, TypeError):
        return None


def _flt(v, default=None):
    try:
        return float(v) if v is not None else default
    except (ValueError, TypeError):
        return default


def _rnd(v):
    try:
        return round(float(v)) if v is not None else None
    except (ValueError, TypeError):
        return None


def _int(v, default=0):
    try:
        return int(float(v)) if v is not None else default
    except (ValueError, TypeError):
        return default


def _err(status, msg):
    return {
        'statusCode': status,
        'headers': {'Content-Type': 'application/json'},
        'body': json.dumps({'error': msg}, ensure_ascii=False),
    }
