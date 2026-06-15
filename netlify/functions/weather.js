'use strict';

const DAYS_PT = ['Dom', 'Seg', 'Ter', 'Qua', 'Qui', 'Sex', 'Sáb'];

const COND_MAP = {
  e: [0, 'Ensolarado'], ps: [1, 'Predomínio de sol'], pc: [2, 'Predomínio de céu claro'],
  pn: [2, 'Parcialmente nublado'], pp: [3, 'Predominantemente nublado'], ec: [3, 'Encoberto'],
  n: [3, 'Nublado'], np: [3, 'Nublado com pancadas'], in: [3, 'Instável'], ni: [3, 'Nublado e instável'],
  ci: [51, 'Garoa isolada'], nc: [61, 'Nublado com chuva'], c: [61, 'Chuva'], cv: [61, 'Chuvoso'],
  cn: [61, 'Chuva com nuvens'], cm: [65, 'Chuva moderada'], t: [95, 'Trovoada'],
  ct: [95, 'Chuva com trovoada'], vt: [95, 'Variável com trovoada'], pt: [96, 'Pancadas de trovoada'],
  nv: [45, 'Nevoeiro'], an: [45, 'Névoa'], ne: [73, 'Neve'], g: [77, 'Geada'],
};

const WMO_CONDITIONS = {
  0: 'Céu limpo', 1: 'Predominantemente limpo', 2: 'Parcialmente nublado', 3: 'Nublado',
  45: 'Nevoeiro', 48: 'Nevoeiro com geada',
  51: 'Garoa leve', 53: 'Garoa', 55: 'Garoa intensa',
  56: 'Chuva gelada leve', 57: 'Chuva gelada',
  61: 'Chuva leve', 63: 'Chuva moderada', 65: 'Chuva forte',
  66: 'Chuva gelada', 67: 'Chuva gelada forte',
  71: 'Neve leve', 73: 'Neve', 75: 'Neve forte', 77: 'Granizo',
  80: 'Pancadas leves', 81: 'Pancadas de chuva', 82: 'Pancadas fortes',
  85: 'Neve em pancadas', 86: 'Neve em pancadas fortes',
  95: 'Trovoada', 96: 'Trovoada com granizo', 99: 'Trovoada severa',
};

const AQI_LABELS = [[0, 'Ótima'], [20, 'Boa'], [40, 'Moderada'], [60, 'Ruim'], [80, 'Muito Ruim'], [100, 'Extremamente Ruim']];

const BR_STATES = {
  Acre: 'AC', Alagoas: 'AL', Amapá: 'AP', Amazonas: 'AM', Bahia: 'BA', Ceará: 'CE',
  'Distrito Federal': 'DF', 'Espírito Santo': 'ES', Goiás: 'GO', Maranhão: 'MA',
  'Mato Grosso': 'MT', 'Mato Grosso do Sul': 'MS', 'Minas Gerais': 'MG', Pará: 'PA',
  Paraíba: 'PB', Paraná: 'PR', Pernambuco: 'PE', Piauí: 'PI', 'Rio de Janeiro': 'RJ',
  'Rio Grande do Norte': 'RN', 'Rio Grande do Sul': 'RS', Rondônia: 'RO', Roraima: 'RR',
  'Santa Catarina': 'SC', 'São Paulo': 'SP', Sergipe: 'SE', Tocantins: 'TO',
};

const TIO_TO_WMO = {
  1000: 0, 1100: 0, 1101: 2, 1102: 3, 1001: 3, 2000: 45, 2100: 45,
  4000: 51, 4001: 61, 4200: 61, 4201: 65, 5000: 71, 5001: 71,
  5100: 71, 5101: 75, 6000: 56, 6001: 66, 6200: 66, 6201: 67,
  7000: 75, 7101: 75, 7102: 75, 8000: 95,
};

exports.handler = async (event) => {
  const params = event.queryStringParameters || {};
  const latS = params.lat;
  const lonS = params.lon;

  if (!latS || !lonS) return err(400, 'lat e lon são obrigatórios');

  const lat = Number.parseFloat(latS);
  const lon = Number.parseFloat(lonS);
  if (Number.isNaN(lat) || Number.isNaN(lon)) return err(400, 'coordenadas inválidas');

  // Round 1 — geocode + Open-Meteo em paralelo
  let cityName, wxRaw, aqRaw;
  try {
    [cityName, [wxRaw, aqRaw]] = await Promise.all([
      reverseGeocode(lat, lon),
      fetchOpenMeteo(lat, lon),
    ]);
  } catch (exc) {
    return err(500, `erro Open-Meteo: ${exc.message || exc}`);
  }

  // Round 2 — CPTEC + OWM/Tomorrow.io em paralelo
  const [cptecDays, obsCurrent] = await Promise.all([
    fetchCptec(cityName.split(',')[0].trim()).catch(() => null),
    fetchOwm(lat, lon)
      .then(r => r || fetchTomorrowio(lat, lon))
      .catch(() => null),
  ]);

  return {
    statusCode: 200,
    headers: {
      'Content-Type': 'application/json; charset=utf-8',
      'Access-Control-Allow-Origin': '*',
      'Cache-Control': 'public, max-age=600',
    },
    body: JSON.stringify(merge(cityName, wxRaw, aqRaw, cptecDays, obsCurrent)),
  };
};

function owmIdToWmo(owmId, precipMm = null) {
  if (owmId >= 200 && owmId <= 232) return 95;
  if (owmId >= 300 && owmId <= 321) return 51;
  if (owmId >= 500 && owmId <= 531) return precipMmToWmo(precipMm) || 61;
  if (owmId >= 600 && owmId <= 622) return 71;
  if (owmId >= 700 && owmId <= 771) return 45;
  if (owmId === 800) return 0;
  if (owmId === 801) return 1;
  if ([802, 803].includes(owmId)) return 2;
  return 3;
}

async function fetchOwm(lat, lon) {
  const key = process.env.OWM_KEY || '';
  if (!key) return null;

  const data = await getJson(`https://api.openweathermap.org/data/2.5/weather?lat=${lat.toFixed(4)}&lon=${lon.toFixed(4)}&appid=${key}&units=metric`, 6000);
  const m = data.main || {};
  const w = data.wind || {};
  const ow = (data.weather || [{}])[0];
  const rainMm = data.rain?.['1h'] || 0.0;
  const wmo = owmIdToWmo(ow.id || 800, rainMm);
  const wd = w.deg;

  return {
    source_type: 'owm',
    temp: rnd(m.temp),
    feels_like: rnd(m.feels_like),
    humidity: intVal(m.humidity),
    wind_kph: rnd((w.speed || 0) * 3.6),
    wind_dir: wd !== undefined && wd !== null ? bearing(wd) : null,
    wind_gust_kph: rnd((w.gust || 0) * 3.6),
    pressure_mb: rnd(m.pressure),
    visibility_km: data.visibility != null ? round1(data.visibility / 1000) : null,
    precip_now_mm: rainMm ? Math.round(rainMm * 10) / 10 : null,
    code: wmo,
    condition: WMO_CONDITIONS[wmo] || '',
  };
}

async function fetchTomorrowio(lat, lon) {
  const key = process.env.TOMORROWIO_KEY || '';
  if (!key) return null;

  const fields = 'precipitationIntensity,temperature,humidity,windSpeed,windDirection,windGust,weatherCode,thunderstormProbability';
  const data = await getJson(`https://api.tomorrow.io/v4/weather/realtime?location=${lat.toFixed(4)},${lon.toFixed(4)}&fields=${fields}&units=metric&apikey=${key}`, 6000);
  const v = data.data?.values || {};
  const precip = v.precipitationIntensity || 0.0;
  const wmo = precipMmToWmo(precip) || TIO_TO_WMO[v.weatherCode || 1000] || 3;
  const wd = v.windDirection;
  const tp = v.thunderstormProbability;

  return {
    source_type: 'tomorrowio',
    temp: rnd(v.temperature),
    humidity: intVal(v.humidity),
    wind_kph: rnd((v.windSpeed || 0) * 3.6),
    wind_dir: wd !== undefined && wd !== null ? bearing(wd) : null,
    wind_gust_kph: rnd((v.windGust || 0) * 3.6),
    precip_now_mm: precip ? Math.round(precip * 10) / 10 : null,
    thunder_prob: tp !== undefined && tp !== null ? intVal(tp) : null,
    code: wmo,
    condition: WMO_CONDITIONS[wmo] || '',
  };
}

async function fetchOpenMeteo(lat, lon) {
  const qs = new URLSearchParams({
    latitude: lat.toFixed(4),
    longitude: lon.toFixed(4),
    hourly: 'temperature_2m,relativehumidity_2m,apparent_temperature,precipitation_probability,precipitation,weathercode,surface_pressure,visibility,uv_index,dewpoint_2m,windspeed_10m,winddirection_10m,windgusts_10m',
    daily: 'weathercode,temperature_2m_max,temperature_2m_min,precipitation_sum,precipitation_probability_max,windspeed_10m_max,sunrise,sunset,uv_index_max',
    current: 'weather_code,precipitation,temperature_2m,relative_humidity_2m,apparent_temperature,wind_speed_10m,wind_direction_10m,wind_gusts_10m,surface_pressure,visibility,uv_index,is_day',
    timezone: 'America/Sao_Paulo',
    forecast_days: '7',
  });
  const wx = await getJson(`https://api.open-meteo.com/v1/forecast?${qs.toString()}`, 10000);

  let aq = null;
  try {
    const qs2 = new URLSearchParams({
      latitude: lat.toFixed(4),
      longitude: lon.toFixed(4),
      hourly: 'pm2_5,european_aqi',
      timezone: 'America/Sao_Paulo',
      forecast_days: '1',
    });
    aq = await getJson(`https://air-quality-api.open-meteo.com/v1/air-quality?${qs2.toString()}`, 6000);
  } catch (_) {
    aq = null;
  }

  return [wx, aq];
}

async function fetchCptec(cityNameRaw) {
  const stripped = stripAccents(cityNameRaw);
  const xml = await getText(`https://servicos.cptec.inpe.br/XML/listaCidades?city=${encodeURIComponent(stripped)}`, 5000, 'latin1');
  const cidades = extractBlocks(xml, 'cidade');
  if (!cidades.length) throw new Error('CPTEC: cidade não encontrada');

  const cityId = tagText(cidades[0], 'id');
  const xml2 = await getText(`https://servicos.cptec.inpe.br/XML/cidade/7dias/${cityId}/previsao.xml`, 6000, 'latin1');
  const previsoes = extractBlocks(xml2, 'previsao').slice(0, 7);

  return previsoes.map((p, i) => {
    const tempo = (tagText(p, 'tempo') || '').toLowerCase().trim();
    const [code, cond] = COND_MAP[tempo] || [1, 'Parcialmente nublado'];
    return {
      day: parseDay(tagText(p, 'dia'), i),
      code,
      condition: cond,
      max: rnd(tagText(p, 'maxima')),
      min: rnd(tagText(p, 'minima')),
      uv_cptec: flt(tagText(p, 'iuv')),
    };
  });
}

function precipMmToWmo(mm) {
  if (mm === undefined || mm === null || mm < 0.1) return null;
  if (mm >= 25.0) return 65;
  if (mm >= 5.0) return 63;
  if (mm >= 0.5) return 61;
  return 51;
}

function merge(cityName, wx, aq, cptecDays, obsCurrent = null) {
  const cw = wx.current || {};
  const daily = wx.daily || {};
  const hourly = wx.hourly || {};

  const cwTime = cw.time || '';
  const hTimes = hourly.time || [];
  const hi = findHourIdx(cwTime, hTimes);
  const h = (key) => {
    const arr = hourly[key] || [];
    return hi < arr.length ? arr[hi] : null;
  };

  let code = Number.parseInt(cw.weather_code || 0, 10);
  const rainCode = precipMmToWmo(cw.precipitation);
  if (rainCode && code < 51) code = rainCode;

  const visM = cw.visibility ?? h('visibility');
  const visKm = visM !== undefined && visM !== null ? Math.round((visM / 1000) * 10) / 10 : null;

  let aqiVal = null;
  let pm25 = null;
  let aqiLabel = null;
  if (aq) {
    const aqH = aq.hourly || {};
    const aqTimes = aqH.time || [];
    const aqHi = findHourIdx(cwTime, aqTimes);
    const aqAqi = aqH.european_aqi || [];
    const aqPm = aqH.pm2_5 || [];
    if (aqHi < aqAqi.length && aqAqi[aqHi] !== null && aqAqi[aqHi] !== undefined) {
      aqiVal = Number.parseInt(aqAqi[aqHi], 10);
      aqiLabel = 'Ótima';
      for (const [thr, lb] of AQI_LABELS) {
        if (aqiVal >= thr) aqiLabel = lb;
      }
    }
    if (aqHi < aqPm.length && aqPm[aqHi] !== null && aqPm[aqHi] !== undefined) pm25 = Math.round(aqPm[aqHi] * 10) / 10;
  }

  const sunrises = daily.sunrise || [];
  const sunsets = daily.sunset || [];
  const sunrise = sunrises[0] && sunrises[0].length >= 16 ? sunrises[0].slice(11, 16) : null;
  const sunset = sunsets[0] && sunsets[0].length >= 16 ? sunsets[0].slice(11, 16) : null;

  const uvMaxList = daily.uv_index_max || [];
  const uvMax = uvMaxList.length && uvMaxList[0] !== null && uvMaxList[0] !== undefined ? round1(uvMaxList[0]) : null;

  const hCode = hourly.weathercode || [];
  const hTemp = hourly.temperature_2m || [];
  const hPop = hourly.precipitation_probability || [];
  const hourlyOut = [];
  for (let j = hi; j < Math.min(hi + 24, hTimes.length); j++) {
    hourlyOut.push({
      time: hTimes[j].slice(11, 16),
      code: j < hCode.length && hCode[j] !== null && hCode[j] !== undefined ? Number.parseInt(hCode[j], 10) : 0,
      temp: j < hTemp.length ? rnd(hTemp[j]) : null,
      precip_prob: j < hPop.length ? intVal(hPop[j]) : 0,
    });
  }

  const dTimes = daily.time || [];
  const dWcode = daily.weathercode || [];
  const dMax = daily.temperature_2m_max || [];
  const dMin = daily.temperature_2m_min || [];
  const dPrecip = daily.precipitation_sum || [];
  const dPop = daily.precipitation_probability_max || [];
  const dUv = daily.uv_index_max || [];
  const dWindMax = daily.windspeed_10m_max || [];

  const daySunrise = (i) => (i < sunrises.length && sunrises[i] && sunrises[i].length >= 16 ? sunrises[i].slice(11, 16) : null);
  const daySunset = (i) => (i < sunsets.length && sunsets[i] && sunsets[i].length >= 16 ? sunsets[i].slice(11, 16) : null);

  let dailyOut = [];
  let source = 'open-meteo';
  let reliability = 'padrão';

  if (cptecDays) {
    dailyOut = cptecDays.map((d, i) => ({
      day: d.day,
      code: d.code,
      condition: d.condition,
      max: d.max,
      min: d.min,
      precip_mm: i < dPrecip.length && dPrecip[i] !== null && dPrecip[i] !== undefined ? Math.round(Number(dPrecip[i]) * 10) / 10 : 0.0,
      precip_prob: i < dPop.length ? intVal(dPop[i]) : 0,
      uv_max: d.uv_cptec || (i < dUv.length && dUv[i] !== null && dUv[i] !== undefined ? round1(dUv[i]) : null),
      wind_max_kph: i < dWindMax.length && dWindMax[i] !== null && dWindMax[i] !== undefined ? rnd(dWindMax[i]) : null,
      sunrise: daySunrise(i),
      sunset: daySunset(i),
    }));
    source = 'cptec+open-meteo';
    reliability = 'boa';
  } else {
    dailyOut = dTimes.slice(0, 7).map((dt, i) => {
      const dc = i < dWcode.length && dWcode[i] !== null && dWcode[i] !== undefined ? Number.parseInt(dWcode[i], 10) : 0;
      return {
        day: parseDay(dt, i),
        code: dc,
        condition: WMO_CONDITIONS[dc] || '',
        max: rnd(i < dMax.length ? dMax[i] : null),
        min: rnd(i < dMin.length ? dMin[i] : null),
        precip_mm: i < dPrecip.length && dPrecip[i] !== null && dPrecip[i] !== undefined ? Math.round(Number(dPrecip[i]) * 10) / 10 : 0.0,
        precip_prob: i < dPop.length ? intVal(dPop[i]) : 0,
        uv_max: i < dUv.length && dUv[i] !== null && dUv[i] !== undefined ? round1(dUv[i]) : null,
        wind_max_kph: i < dWindMax.length && dWindMax[i] !== null && dWindMax[i] !== undefined ? rnd(dWindMax[i]) : null,
        sunrise: daySunrise(i),
        sunset: daySunset(i),
      };
    });
  }

  const wd = cw.wind_direction_10m;
  const current = {
    temp: rnd(cw.temperature_2m),
    feels_like: rnd(cw.apparent_temperature),
    humidity: intVal(cw.relative_humidity_2m),
    wind_kph: rnd(cw.wind_speed_10m),
    wind_dir: wd !== undefined && wd !== null ? bearing(wd) : null,
    wind_gust_kph: rnd(cw.wind_gusts_10m),
    condition: WMO_CONDITIONS[code] || '',
    code,
    visibility_km: visKm,
    pressure_mb: rnd(cw.surface_pressure),
    dew_point: rnd(h('dewpoint_2m')),
    uv_index: round1(cw.uv_index),
    is_day: Boolean(cw.is_day ?? 1),
    precip_now_mm: null,
    thunder_prob: null,
  };

  if (obsCurrent) {
    const obsKeys = ['temp', 'feels_like', 'humidity', 'wind_kph', 'wind_dir', 'wind_gust_kph', 'pressure_mb', 'visibility_km', 'code', 'condition', 'precip_now_mm', 'thunder_prob'];
    for (const key of obsKeys) {
      if (obsCurrent[key] !== null && obsCurrent[key] !== undefined) current[key] = obsCurrent[key];
    }
    if (obsCurrent.source_type === 'owm') {
      source = `owm+${source}`;
      reliability = 'alta';
    } else {
      source = `tomorrowio+${source}`;
      if (reliability === 'padrão') reliability = 'boa';
    }
  }

  // Reconciliação: OWM "céu limpo" não pode silenciar precipitação medida pelo Open-Meteo.
  // Se o modelo vê ≥ 0.3mm na janela atual e a condição ainda aparece como "limpo/nublado",
  // é porque a estação OWM (possivelmente distante) não captou a chuva local.
  const modelPrecip = cw.precipitation ?? 0;
  if (current.code < 51 && modelPrecip >= 0.3) {
    const rc = precipMmToWmo(modelPrecip);
    if (rc) { current.code = rc; current.condition = WMO_CONDITIONS[rc] || ''; }
  }
  // Fallback: se OWM não reportou rain.1h mas o modelo mede precipitação, propagar o valor
  // para que o card de Chuva mostre mm/h em vez de "% chance".
  if (!current.precip_now_mm && modelPrecip >= 0.1) {
    current.precip_now_mm = Math.round(modelPrecip * 10) / 10;
  }

  return {
    city: cityName,
    source,
    reliability,
    key_status: {
      owm: Boolean(process.env.OWM_KEY),
      tomorrowio: Boolean(process.env.TOMORROWIO_KEY),
    },
    current,
    today: { sunrise, sunset, uv_max: uvMax, aqi: aqiVal, aqi_label: aqiLabel, pm25 },
    hourly: hourlyOut,
    daily: dailyOut,
  };
}

function findHourIdx(cwTime, times) {
  if (!times?.length || !cwTime) return 0;
  const cwH = cwTime.slice(0, 13);
  for (let i = 0; i < times.length; i++) {
    if (times[i].slice(0, 13) >= cwH) return i;
  }
  return Math.max(0, times.length - 1);
}

async function reverseGeocode(lat, lon) {
  try {
    const url = `https://nominatim.openstreetmap.org/reverse?lat=${lat}&lon=${lon}&format=json&accept-language=pt`;
    const data = await getJson(url, 5000, { 'User-Agent': 'tempo-app/1.0' });
    const addr = data.address || {};
    const city = addr.city || addr.town || addr.village || addr.municipality || '';
    const state = BR_STATES[(addr.state || '').trim()] || '';
    return city && state ? `${city}, ${state}` : city || 'Uberlândia, MG';
  } catch (_) {
    return 'Uberlândia, MG';
  }
}

async function getJson(url, timeoutMs = 8000, headers = {}) {
  const res = await fetch(url, { signal: AbortSignal.timeout(timeoutMs), headers });
  if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
  return res.json();
}

async function getText(url, timeoutMs = 6000, encoding = 'utf-8') {
  const res = await fetch(url, {
    signal: AbortSignal.timeout(timeoutMs),
    headers: { 'User-Agent': 'tempo-app/1.0' },
  });
  if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
  return Buffer.from(await res.arrayBuffer()).toString(encoding);
}

function extractBlocks(xml, tag) {
  const re = new RegExp(`<${tag}>([\\s\\S]*?)<\\/${tag}>`, 'g');
  return [...xml.matchAll(re)].map((m) => m[1]);
}

function tagText(xml, tag) {
  const re = new RegExp(`<${tag}>([\\s\\S]*?)<\\/${tag}>`);
  const m = xml.match(re);
  return m ? decodeXml(m[1].trim()) : null;
}

function decodeXml(s) {
  return String(s)
    .replaceAll('&amp;', '&')
    .replaceAll('&lt;', '<')
    .replaceAll('&gt;', '>')
    .replaceAll('&quot;', '"')
    .replaceAll('&apos;', "'");
}

function stripAccents(s) {
  return String(s).normalize('NFD').replace(/[\u0300-\u036f]/g, '');
}

function parseDay(dtStr, index) {
  if (index === 0) return 'Hoje';
  if (index === 1) return 'Amanhã';
  try {
    const dt = new Date(`${String(dtStr || '').slice(0, 10)}T12:00:00-03:00`);
    if (Number.isNaN(dt.getTime())) throw new Error('invalid date');
    return DAYS_PT[dt.getDay()];
  } catch (_) {
    return DAYS_PT[index % 7];
  }
}

function bearing(deg) {
  const dirs = ['N', 'NE', 'L', 'SE', 'S', 'SO', 'O', 'NO'];
  return dirs[Math.round(Number(deg) / 45) % 8];
}

function round1(v) {
  const n = Number(v);
  return Number.isFinite(n) ? Math.round(n * 10) / 10 : null;
}

function flt(v, defaultValue = null) {
  const n = Number(v);
  return Number.isFinite(n) ? n : defaultValue;
}

function rnd(v) {
  const n = Number(v);
  return Number.isFinite(n) ? Math.round(n) : null;
}

function intVal(v, defaultValue = 0) {
  const n = Number(v);
  return Number.isFinite(n) ? Math.trunc(n) : defaultValue;
}

function err(status, msg) {
  return {
    statusCode: status,
    headers: { 'Content-Type': 'application/json; charset=utf-8', 'Access-Control-Allow-Origin': '*' },
    body: JSON.stringify({ error: msg }),
  };
}
