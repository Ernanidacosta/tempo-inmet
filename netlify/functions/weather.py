import json
import unicodedata
import urllib.request
import urllib.parse
import xml.etree.ElementTree as ET
from datetime import datetime

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

    return {
        'statusCode': 200,
        'headers': {
            'Content-Type': 'application/json; charset=utf-8',
            'Access-Control-Allow-Origin': '*',
        },
        'body': json.dumps(_merge(city_name, wx_raw, aq_raw, cptec_days), ensure_ascii=False),
    }


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


def _merge(city_name, wx, aq, cptec_days):
    cw = wx.get('current_weather', {})
    daily = wx.get('daily', {})
    hourly = wx.get('hourly', {})

    cw_time = cw.get('time', '')
    h_times = hourly.get('time', [])
    hi = _find_hour_idx(cw_time, h_times)

    def h(key):
        arr = hourly.get(key) or []
        return arr[hi] if hi < len(arr) else None

    code = int(cw.get('weathercode', 0))
    vis_m = h('visibility')
    vis_km = round(vis_m / 1000, 1) if vis_m is not None else None

    # Air quality
    aqi_val = pm25 = aqi_label = None
    if aq:
        aq_h = aq.get('hourly', {})
        aq_times = aq_h.get('time', [])
        aq_hi = _find_hour_idx(cw_time, aq_times)
        aq_aqi = aq_h.get('european_aqi') or []
        aq_pm = aq_h.get('pm2_5') or []
        if aq_hi < len(aq_aqi) and aq_aqi[aq_hi] is not None:
            aqi_val = int(aq_aqi[aq_hi])
            aqi_label = next((lb for thr, lb in reversed(AQI_LABELS) if aqi_val >= thr), 'Ótima')
        if aq_hi < len(aq_pm) and aq_pm[aq_hi] is not None:
            pm25 = round(aq_pm[aq_hi], 1)

    # Sunrise / sunset
    sunrises = daily.get('sunrise', [])
    sunsets = daily.get('sunset', [])
    sunrise = sunrises[0][11:16] if sunrises and len(sunrises[0]) >= 16 else None
    sunset = sunsets[0][11:16] if sunsets and len(sunsets[0]) >= 16 else None

    uv_max_list = daily.get('uv_index_max', [])
    uv_max = round(uv_max_list[0], 1) if uv_max_list and uv_max_list[0] is not None else None

    # Hourly output — next 24h
    h_code = hourly.get('weathercode', [])
    h_temp = hourly.get('temperature_2m', [])
    h_pop = hourly.get('precipitation_probability', [])
    hourly_out = []
    for j in range(hi, min(hi + 24, len(h_times))):
        hourly_out.append({
            'time': h_times[j][11:16],
            'code': int(h_code[j]) if j < len(h_code) and h_code[j] is not None else 0,
            'temp': _rnd(h_temp[j]) if j < len(h_temp) else None,
            'precip_prob': _int(h_pop[j]) if j < len(h_pop) else 0,
        })

    # Daily output
    d_times = daily.get('time', [])
    d_wcode = daily.get('weathercode', [])
    d_max = daily.get('temperature_2m_max', [])
    d_min = daily.get('temperature_2m_min', [])
    d_precip = daily.get('precipitation_sum', [])
    d_pop = daily.get('precipitation_probability_max', [])
    d_uv = daily.get('uv_index_max', [])
    d_wind_max = daily.get('windspeed_10m_max', [])

    def _day_sunrise(i):
        return sunrises[i][11:16] if i < len(sunrises) and sunrises[i] and len(sunrises[i]) >= 16 else None

    def _day_sunset(i):
        return sunsets[i][11:16] if i < len(sunsets) and sunsets[i] and len(sunsets[i]) >= 16 else None

    if cptec_days:
        daily_out = []
        for i, d in enumerate(cptec_days):
            daily_out.append({
                'day': d['day'],
                'code': d['code'],
                'condition': d['condition'],
                'max': d['max'],
                'min': d['min'],
                'precip_mm': round(float(d_precip[i]), 1) if i < len(d_precip) and d_precip[i] is not None else 0.0,
                'precip_prob': _int(d_pop[i]) if i < len(d_pop) else 0,
                'uv_max': d.get('uv_cptec') or (_round1(d_uv[i]) if i < len(d_uv) and d_uv[i] is not None else None),
                'wind_max_kph': _rnd(d_wind_max[i]) if i < len(d_wind_max) and d_wind_max[i] is not None else None,
                'sunrise': _day_sunrise(i),
                'sunset': _day_sunset(i),
            })
        source = 'cptec+open-meteo'
    else:
        daily_out = []
        for i, dt in enumerate(d_times[:7]):
            dc = int(d_wcode[i]) if i < len(d_wcode) and d_wcode[i] is not None else 0
            daily_out.append({
                'day': _parse_day(dt, i),
                'code': dc,
                'condition': WMO_CONDITIONS.get(dc, ''),
                'max': _rnd(d_max[i] if i < len(d_max) else None),
                'min': _rnd(d_min[i] if i < len(d_min) else None),
                'precip_mm': round(float(d_precip[i]), 1) if i < len(d_precip) and d_precip[i] is not None else 0.0,
                'precip_prob': _int(d_pop[i]) if i < len(d_pop) else 0,
                'uv_max': _round1(d_uv[i]) if i < len(d_uv) and d_uv[i] is not None else None,
                'wind_max_kph': _rnd(d_wind_max[i]) if i < len(d_wind_max) and d_wind_max[i] is not None else None,
                'sunrise': _day_sunrise(i),
                'sunset': _day_sunset(i),
            })
        source = 'open-meteo'

    wd = cw.get('winddirection')
    return {
        'city': city_name,
        'source': source,
        'current': {
            'temp': _rnd(cw.get('temperature')),
            'feels_like': _rnd(h('apparent_temperature')),
            'humidity': _int(h('relativehumidity_2m')),
            'wind_kph': _rnd(cw.get('windspeed')),
            'wind_dir': _bearing(wd) if wd is not None else None,
            'wind_gust_kph': _rnd(h('windgusts_10m')),
            'condition': WMO_CONDITIONS.get(code, ''),
            'code': code,
            'visibility_km': vis_km,
            'pressure_mb': _rnd(h('surface_pressure')),
            'dew_point': _rnd(h('dewpoint_2m')),
            'uv_index': _round1(h('uv_index')),
            'is_day': bool(cw.get('is_day', 1)),
        },
        'today': {
            'sunrise': sunrise,
            'sunset': sunset,
            'uv_max': uv_max,
            'aqi': aqi_val,
            'aqi_label': aqi_label,
            'pm25': pm25,
        },
        'hourly': hourly_out,
        'daily': daily_out,
    }


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
        addr = data.get('address', {})
        city = (addr.get('city') or addr.get('town') or addr.get('village')
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
