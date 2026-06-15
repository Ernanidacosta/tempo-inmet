'use strict';

exports.handler = async (event) => {
  const { layer, z, x, y } = event.queryStringParameters || {};
  if (!layer || z == null || x == null || y == null)
    return { statusCode: 400, body: '' };

  const key = process.env.OWM_KEY || '';
  if (!key) return { statusCode: 503, body: '' };

  const url =
    `https://tile.openweathermap.org/map/${layer}/${z}/${x}/${y}.png?appid=${key}`;
  let r;
  try {
    r = await fetch(url, { signal: AbortSignal.timeout(5000) });
  } catch (_) {
    return { statusCode: 504, body: '' };
  }
  if (!r.ok) return { statusCode: r.status, body: '' };

  const buf = await r.arrayBuffer();
  return {
    statusCode: 200,
    headers: {
      'Content-Type': 'image/png',
      'Cache-Control': 'public, max-age=300',
      'Access-Control-Allow-Origin': '*',
    },
    body: Buffer.from(buf).toString('base64'),
    isBase64Encoded: true,
  };
};
