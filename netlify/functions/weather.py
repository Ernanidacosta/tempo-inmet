import json
import urllib.request
import urllib.parse
from datetime import date, datetime

DAYS_PT = ["Dom", "Seg", "Ter", "Qua", "Qui", "Sex", "Sáb"]

INMET_COND = {
    "ps":  (2,  "Parcialmente nublado"),
    "pn":  (2,  "Parcialmente nublado"),
    "pp":  (3,  "Predominantemente nublado"),
    "np":  (3,  "Nublado com pancadas"),
    "ec":  (3,  "Encoberto"),
    "n":   (3,  "Nublado"),
    "c":   (63, "Chuva"),
    "ci":  (61, "Chuva isolada"),
    "cm":  (65, "Chuva moderada"),
    "ct":  (95, "Chuva e trovoada"),
    "vt":  (95, "Variável com trovoada"),
    "pt":  (96, "Pancadas e trovoadas"),
    "nv":  (45, "Nevoeiro"),
    "an":  (45, "Névoa"),
    "ne":  (73, "Neve"),
    "g":   (77, "Geada"),
    "e":   (0,  "Ensolarado"),
}

WMO_CONDITIONS = {
    0:  "Céu limpo",
    1:  "Predominantemente limpo",
    2:  "Parcialmente nublado",
    3:  "Nublado",
    45: "Nevoeiro",
    48: "Nevoeiro com geada",
    51: "Garoa leve",
    53: "Garoa moderada",
    55: "Garoa intensa",
    61: "Chuva leve",
    63: "Chuva moderada",
    65: "Chuva forte",
    71: "Neve leve",
    73: "Neve moderada",
    75: "Neve forte",
    77: "Granizo",
    80: "Pancadas de chuva leve",
    81: "Pancadas de chuva",
    82: "Pancadas de chuva forte",
    95: "Trovoada",
    96: "Trovoada com granizo",
    99: "Trovoada severa",
}

BR_STATES = {
    "Acre": "AC", "Alagoas": "AL", "Amapá": "AP", "Amazonas": "AM",
    "Bahia": "BA", "Ceará": "CE", "Distrito Federal": "DF",
    "Espírito Santo": "ES", "Goiás": "GO", "Maranhão": "MA",
    "Mato Grosso": "MT", "Mato Grosso do Sul": "MS", "Minas Gerais": "MG",
    "Pará": "PA", "Paraíba": "PB", "Paraná": "PR", "Pernambuco": "PE",
    "Piauí": "PI", "Rio de Janeiro": "RJ", "Rio Grande do Norte": "RN",
    "Rio Grande do Sul": "RS", "Rondônia": "RO", "Roraima": "RR",
    "Santa Catarina": "SC", "São Paulo": "SP", "Sergipe": "SE",
    "Tocantins": "TO",
}


def handler(event, context):
    params = event.get("queryStringParameters") or {}
    lat_s = params.get("lat")
    lon_s = params.get("lon")

    if not lat_s or not lon_s:
        return _err(400, "lat e lon são obrigatórios")

    try:
        lat, lon = float(lat_s), float(lon_s)
    except ValueError:
        return _err(400, "coordenadas inválidas")

    data = None

    try:
        data = _fetch_inmet(lat, lon)
    except Exception:
        pass

    if not data:
        try:
            data = _fetch_open_meteo(lat, lon)
        except Exception as exc:
            return _err(500, f"erro ao buscar dados: {exc}")

    return {
        "statusCode": 200,
        "headers": {
            "Content-Type": "application/json; charset=utf-8",
            "Access-Control-Allow-Origin": "*",
        },
        "body": json.dumps(data, ensure_ascii=False),
    }


def _fetch_inmet(lat, lon):
    today = date.today().isoformat()
    url = f"https://apitempo.inmet.gov.br/condicao/ponto/{lat:.4f}/{lon:.4f}/{today}"
    req = urllib.request.Request(url, headers={"User-Agent": "tempo-app/1.0"})
    with urllib.request.urlopen(req, timeout=5) as r:
        raw = json.loads(r.read().decode("utf-8"))

    if not isinstance(raw, list) or not raw:
        return None

    first = raw[0]
    if "TEM_MAX" not in first and "TEMP" not in first:
        return None

    municipio = (first.get("MUNICIPIO") or "").strip().title()
    uf = (first.get("UF") or "").strip().upper()
    if municipio and uf:
        city_name = f"{municipio}, {uf}"
    elif municipio:
        city_name = municipio
    else:
        city_name = _reverse_geocode(lat, lon)

    days = []
    for i, item in enumerate(raw[:7]):
        ckey = (item.get("TEMPO") or "").lower().strip()
        code, cond = INMET_COND.get(ckey, (1, "Parcialmente nublado"))
        days.append({
            "day": _parse_day(item.get("DT_REFERENCIA") or item.get("DT_PREVISAO") or "", i),
            "code": code,
            "condition": cond,
            "max": _rnd(item.get("TEM_MAX")),
            "min": _rnd(item.get("TEM_MIN")),
            "precip_mm": _flt(item.get("CHUVA", 0)),
            "precip_prob": _int(item.get("POP", 0)),
        })

    if not days:
        return None

    ckey = (first.get("TEMPO") or "").lower().strip()
    code, cond = INMET_COND.get(ckey, (1, "Parcialmente nublado"))
    t_max = _flt(first.get("TEM_MAX"))
    t_min = _flt(first.get("TEM_MIN"))
    temp = _rnd(first.get("TEMP")) or (round((t_max + t_min) / 2) if t_max is not None and t_min is not None else None)
    u_max = _flt(first.get("UMAX"))
    u_min = _flt(first.get("UMIN"))
    humidity = round((u_max + u_min) / 2) if u_max is not None and u_min is not None else _int(first.get("UMID"))

    return {
        "city": city_name,
        "source": "inmet",
        "current": {
            "temp": temp,
            "feels_like": None,
            "humidity": humidity,
            "wind_kph": _flt(first.get("VENTO_VEL")),
            "wind_dir": first.get("VENTO_DIR", ""),
            "condition": cond,
            "code": code,
        },
        "daily": days,
    }


def _fetch_open_meteo(lat, lon):
    qs = urllib.parse.urlencode({
        "latitude": f"{lat:.4f}",
        "longitude": f"{lon:.4f}",
        "daily": "weathercode,temperature_2m_max,temperature_2m_min,precipitation_sum,precipitation_probability_max,windspeed_10m_max",
        "current_weather": "true",
        "hourly": "relativehumidity_2m,apparent_temperature",
        "timezone": "America/Sao_Paulo",
        "forecast_days": "7",
    })
    url = f"https://api.open-meteo.com/v1/forecast?{qs}"

    raw = _get_json(url, timeout=10)
    city_name = _reverse_geocode(lat, lon)

    cw = raw.get("current_weather", {})
    daily = raw.get("daily", {})
    hourly = raw.get("hourly", {})

    code = int(cw.get("weathercode", 0))
    humidity = (hourly.get("relativehumidity_2m") or [None])[0]
    feels_like = None
    at = hourly.get("apparent_temperature") or []
    if at:
        feels_like = round(at[0])

    days = []
    times = daily.get("time", [])
    wcode = daily.get("weathercode", [])
    t_max = daily.get("temperature_2m_max", [])
    t_min = daily.get("temperature_2m_min", [])
    precip = daily.get("precipitation_sum", [])
    pop = daily.get("precipitation_probability_max", [])

    for i, dt in enumerate(times[:7]):
        dc = int(wcode[i]) if i < len(wcode) else 0
        days.append({
            "day": _parse_day(dt, i),
            "code": dc,
            "condition": WMO_CONDITIONS.get(dc, ""),
            "max": _rnd(t_max[i] if i < len(t_max) else None),
            "min": _rnd(t_min[i] if i < len(t_min) else None),
            "precip_mm": round(float(precip[i]), 1) if i < len(precip) and precip[i] is not None else 0.0,
            "precip_prob": int(pop[i]) if i < len(pop) and pop[i] is not None else 0,
        })

    return {
        "city": city_name,
        "source": "open-meteo",
        "current": {
            "temp": _rnd(cw.get("temperature")),
            "feels_like": feels_like,
            "humidity": humidity,
            "wind_kph": _rnd(cw.get("windspeed")),
            "wind_dir": _bearing(cw.get("winddirection", 0)),
            "condition": WMO_CONDITIONS.get(code, ""),
            "code": code,
        },
        "daily": days,
    }


def _reverse_geocode(lat, lon):
    try:
        url = f"https://nominatim.openstreetmap.org/reverse?lat={lat}&lon={lon}&format=json&accept-language=pt"
        req = urllib.request.Request(url, headers={"User-Agent": "tempo-app/1.0"})
        with urllib.request.urlopen(req, timeout=4) as r:
            data = json.loads(r.read().decode("utf-8"))
        addr = data.get("address", {})
        city = addr.get("city") or addr.get("town") or addr.get("village") or addr.get("municipality") or ""
        state = addr.get("state", "")
        abbr = BR_STATES.get(state.strip(), "")
        if city and abbr:
            return f"{city}, {abbr}"
        return city or "Uberlândia, MG"
    except Exception:
        return "Uberlândia, MG"


def _get_json(url, timeout=8):
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def _parse_day(dt_str, index):
    if index == 0:
        return "Hoje"
    if index == 1:
        return "Amanhã"
    try:
        part = (dt_str or "")[:10]
        dt = datetime.strptime(part, "%Y-%m-%d")
        return DAYS_PT[(dt.weekday() + 1) % 7]
    except Exception:
        return DAYS_PT[index % 7]


def _bearing(deg):
    dirs = ["N", "NE", "L", "SE", "S", "SO", "O", "NO"]
    return dirs[round(float(deg) / 45) % 8]


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
        "statusCode": status,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps({"error": msg}, ensure_ascii=False),
    }
