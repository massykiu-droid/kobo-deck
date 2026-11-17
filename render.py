#!/usr/bin/env python3
"""Genera le immagini del deck Kobo: meteo (Open-Meteo) + fotovoltaico (FusionSolar Kiosk)
in deck.png e andamento dei titoli (Yahoo Finance) in stocks.png, a rotazione in api/display.

Variabili d'ambiente:
  KIOSK_URL   URL Kiosk FusionSolar (contiene ?kk=...). Tenerlo come secret.
  LAT, LON    coordinate per il meteo (default: Segrate)
  PLACE       nome della localita' mostrato in alto
Uso: python render.py [--sample DIR] [--out FILE]
"""
import argparse
import json
import math
import re
import traceback
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from PIL import Image, ImageDraw, ImageFont

W, H = 758, 1024  # Kobo Glo, verticale
TZ = ZoneInfo("Europe/Rome")
FONT_DIR = "/usr/share/fonts/truetype/dejavu/"
BLACK, DARK, MID, LIGHT, WHITE = 0, 70, 130, 200, 255

GIORNI = ["Lun", "Mar", "Mer", "Gio", "Ven", "Sab", "Dom"]
GIORNI_L = ["Lunedì", "Martedì", "Mercoledì", "Giovedì", "Venerdì", "Sabato", "Domenica"]
MESI = ["gennaio", "febbraio", "marzo", "aprile", "maggio", "giugno", "luglio",
        "agosto", "settembre", "ottobre", "novembre", "dicembre"]


def font(size, bold=False):
    name = "DejaVuSans-Bold.ttf" if bold else "DejaVuSans.ttf"
    return ImageFont.truetype(FONT_DIR + name, size)


def msg(e, tb=False):
    """Messaggio d'errore breve e senza segreti (il parametro kk del Kiosk viene oscurato)."""
    t = f"{type(e).__name__}: {e}"
    if tb:
        t += " @ " + " | ".join(l.strip() for l in traceback.format_exc().splitlines()[-4:-1])
    return re.sub(r"kk=[^&\s'\"]+", "kk=***", t)[:300]


def get_json(url, timeout=20, tries=4):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 kobo-deck"})
    last = None
    for n in range(tries):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.loads(r.read().decode("utf-8"))
        except Exception as e:  # noqa: BLE001
            last = e
            time.sleep(3 * (n + 1))
    raise last


# ---------------------------------------------------------------- dati
def fetch_weather(lat, lon):
    q = urllib.parse.urlencode({
        "latitude": lat, "longitude": lon, "timezone": "Europe/Rome", "forecast_days": 5,
        "current": "temperature_2m,apparent_temperature,weather_code,wind_speed_10m,relative_humidity_2m,is_day",
        "hourly": "temperature_2m,precipitation_probability,weather_code",
        "daily": "weather_code,temperature_2m_max,temperature_2m_min,precipitation_probability_max,sunrise,sunset",
    })
    return get_json("https://api.open-meteo.com/v1/forecast?" + q)


def fetch_air(lat, lon):
    q = urllib.parse.urlencode({
        "latitude": lat, "longitude": lon, "timezone": "Europe/Rome",
        "current": "european_aqi,pm10,pm2_5,nitrogen_dioxide,ozone",
    })
    return get_json("https://air-quality-api.open-meteo.com/v1/air-quality?" + q, tries=3)["current"]


def aqi_label(v):
    for lim, lab in ((20, "Buona"), (40, "Discreta"), (60, "Moderata"), (80, "Scadente"), (100, "Molto scadente")):
        if v <= lim:
            return lab
    return "Pessima"


def fetch_solar(kiosk_url):
    u = urllib.parse.urlparse(kiosk_url)
    kk = urllib.parse.parse_qs(urllib.parse.urlparse(u.fragment).query).get("kk") \
        or urllib.parse.parse_qs(u.query).get("kk")
    if not kk:
        raise ValueError("KIOSK_URL senza parametro kk")
    api = f"{u.scheme}://{u.netloc}/rest/pvms/web/kiosk/v1/station-kiosk-file?kk={kk[0]}"
    raw = get_json(api)
    if not raw.get("success"):
        raise RuntimeError(f"Kiosk: failCode {raw.get('failCode')}")
    return json.loads(raw["data"].replace("&quot;", '"'))


# ---------------------------------------------------------------- titoli
# (nome mostrato, simbolo Yahoo Finance). Per cambiare un titolo basta modificare questa lista.
# Titoli in dollari. Gli ETF UCITS (Amundi Nasdaq 100, iShares S&P 500 IT) non sono negoziati
# negli USA: si usano le loro quotazioni in USD alla Borsa di Londra (ANXU.L, IUIT.L).
# Eni e' l'ADR di New York.
STOCKS = [
    ("SpaceX", "SPCX"),
    ("Humana", "HUM"),
    ("Amundi Nasdaq 100", "ANXU.L"),
    ("iShares S&P 500 IT", "IUIT.L"),
    ("AMD", "AMD"),
    ("Alphabet", "GOOGL"),
    ("Taiwan Semiconductor", "TSM"),
    ("NVIDIA", "NVDA"),
    ("Oracle", "ORCL"),
    ("Adobe", "ADBE"),
    ("Seagate", "STX"),
    ("Novartis", "NVS"),
    ("Eni", "E"),
    ("Palantir", "PLTR"),
    ("Take-Two", "TTWO"),
]
CURRENCY = {"USD": "$", "EUR": "€", "CHF": "CHF", "GBP": "£"}
STOCKS_ROTATE = int(os.environ.get("STOCKS_ROTATE", "90"))  # gradi antiorari; 270 = verso opposto


def fetch_quote(sym):
    """Ultima quotazione e chiusura del giorno di borsa precedente, da Yahoo Finance."""
    q = urllib.parse.urlencode({"range": "1mo", "interval": "1d"})
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{urllib.parse.quote(sym)}?{q}"
    res = get_json(url, timeout=15, tries=3)["chart"]["result"][0]
    meta = res["meta"]
    closes = res["indicators"]["quote"][0]["close"]
    bars = [(t, c) for t, c in zip(res["timestamp"], closes) if c is not None]
    if len(bars) < 2:
        raise ValueError("storico insufficiente")
    last = float(meta.get("regularMarketPrice") or bars[-1][1])
    prev = float(bars[-2][1])
    tz = ZoneInfo(meta.get("exchangeTimezoneName") or "UTC")
    when = datetime.fromtimestamp(meta.get("regularMarketTime") or bars[-1][0], tz)
    series = [float(c) for _, c in bars[-10:]]
    series[-1] = last
    return {"last": last, "prev": prev, "pct": (last / prev - 1) * 100, "cur": meta.get("currency", "USD"),
            "date": when.strftime("%Y-%m-%d"), "series": series}


def fetch_quotes(site_url=None):
    """Scarica tutti i titoli; per quelli che falliscono riusa l'ultimo dato pubblicato."""
    out, failed, streak = {}, [], 0
    for name, sym in STOCKS:
        if streak >= 3:  # Yahoo non risponde: inutile insistere
            failed.append(sym)
            continue
        try:
            out[sym] = fetch_quote(sym)
            streak = 0
        except Exception as e:  # noqa: BLE001
            print("titolo", sym, type(e).__name__, e, file=sys.stderr)
            failed.append(sym)
            streak += 1
        time.sleep(0.3)
    if failed and site_url:
        try:
            old = get_json(site_url.rstrip("/") + "/quotes.json", tries=1)
            for sym in failed:
                if sym in old:
                    out[sym] = dict(old[sym], stale=True)
        except Exception as e:  # noqa: BLE001
            print("titoli cache:", e, file=sys.stderr)
    return out


def _spark(d, series, x0, x1, yc, h):
    lo, hi = min(series), max(series)
    span = (hi - lo) or 1.0
    pts = [(x0 + (x1 - x0) * i / (len(series) - 1), yc + h / 2 - h * (v - lo) / span)
           for i, v in enumerate(series)]
    d.line(pts, fill=DARK, width=2)
    x, y = pts[-1]
    d.ellipse([x - 3, y - 3, x + 3, y + 3], fill=BLACK)


def draw_stocks(quotes, now):
    """Schermata titoli in orizzontale (1024x758)."""
    LW, LH = H, W
    img = Image.new("L", (LW, LH), WHITE)
    d = ImageDraw.Draw(img)
    text(d, (30, 16), "Titoli", font(34, True))
    got = [quotes[s] for _, s in STOCKS if s in quotes]
    up = sum(1 for q in got if q["pct"] >= 0)
    text(d, (LW - 30, 18), f"▲ {up}   ▼ {len(got) - up}", font(26, True), anchor="ra")
    sub = "variazione rispetto al giorno precedente"
    if got:
        last_day = max(q["date"] for q in got)
        if last_day != now.strftime("%Y-%m-%d"):
            sub += f"  ·  ultima seduta {last_day[8:10]}/{last_day[5:7]}"
    text(d, (30 + d.textlength("Titoli", font=font(34, True)) + 22, 28), sub, font(19), DARK)
    d.line([30, 66, LW - 30, 66], fill=BLACK, width=3)

    top, rh = 72, 43
    for i, (name, sym) in enumerate(STOCKS):
        y = top + i * rh
        cy = y + rh / 2
        if i:
            d.line([30, y, LW - 30, y], fill=LIGHT, width=1)
        fs = 24
        while fs > 16 and d.textlength(name, font=font(fs, True)) > 270:
            fs -= 1
        text(d, (30, cy), name, font(fs, True), anchor="lm")
        text(d, (312, cy + 1), sym.split(".")[0], font(17), MID, "lm")
        q = quotes.get(sym)
        if q is None:
            text(d, (LW - 30, cy), "n.d.", font(24), MID, "rm")
            continue
        cur = CURRENCY.get(q["cur"], q["cur"])
        text(d, (640, cy), f"{num(q['last'], 2)} {cur}", font(23), BLACK, "rm")
        if len(q["series"]) > 2:
            _spark(d, q["series"], 668, 790, cy, 24)
        pct = q["pct"]
        label = f"{'▲' if pct >= 0 else '▼'} {'+' if pct >= 0 else '−'}{num(abs(pct), 2)}%"
        if q.get("stale"):
            label += "*"
        f = font(26, True)
        if abs(pct) >= 3:  # movimento forte: testo bianco su sfondo nero
            tw = d.textlength(label, font=f)
            d.rounded_rectangle([LW - 30 - tw - 14, y + 5, LW - 30 + 2, y + rh - 5], radius=6, fill=BLACK)
            text(d, (LW - 36, cy), label, f, WHITE, "rm")
        else:
            text(d, (LW - 32, cy), label, f, BLACK, "rm")
    foot = f"Aggiornato alle {now.strftime('%H:%M')}  ·  ultima quotazione vs chiusura precedente"
    if any(q.get("stale") for q in got):
        foot += "  ·  * dato non aggiornato"
    text(d, (LW / 2, LH - 8), foot, font(16), MID, "ms")
    return img.rotate(STOCKS_ROTATE, expand=True)


# ---------------------------------------------------------------- icone meteo
def wmo(code):
    """Codice WMO -> (tipo icona, descrizione)."""
    table = {
        0: ("sun", "Sereno"), 1: ("sun", "Quasi sereno"), 2: ("partly", "Poco nuvoloso"),
        3: ("cloud", "Coperto"), 45: ("fog", "Nebbia"), 48: ("fog", "Nebbia e brina"),
        51: ("rain", "Pioviggine"), 53: ("rain", "Pioviggine"), 55: ("rain", "Pioviggine fitta"),
        56: ("rain", "Pioviggine gelata"), 57: ("rain", "Pioviggine gelata"),
        61: ("rain", "Pioggia debole"), 63: ("rain", "Pioggia"), 65: ("rain", "Pioggia forte"),
        66: ("rain", "Pioggia gelata"), 67: ("rain", "Pioggia gelata"),
        71: ("snow", "Neve debole"), 73: ("snow", "Neve"), 75: ("snow", "Neve forte"),
        77: ("snow", "Nevischio"), 80: ("rain", "Rovesci"), 81: ("rain", "Rovesci"),
        82: ("rain", "Rovesci forti"), 85: ("snow", "Rovesci di neve"), 86: ("snow", "Rovesci di neve"),
        95: ("storm", "Temporale"), 96: ("storm", "Temporale, grandine"), 99: ("storm", "Temporale, grandine"),
    }
    return table.get(code, ("cloud", "Variabile"))


def _sun(d, cx, cy, r, lw):
    d.ellipse([cx - r, cy - r, cx + r, cy + r], outline=BLACK, width=lw)
    for k in range(8):
        a = k * math.pi / 4
        d.line([cx + math.cos(a) * r * 1.35, cy + math.sin(a) * r * 1.35,
                cx + math.cos(a) * r * 1.8, cy + math.sin(a) * r * 1.8], fill=BLACK, width=lw)


def _cloud(d, cx, cy, s, lw, fill=WHITE):
    parts = [(-0.55, 0.05, 0.38), (-0.12, -0.22, 0.48), (0.38, 0.0, 0.42)]
    box = [cx - s * 0.75, cy + s * 0.05, cx + s * 0.75, cy + s * 0.45]
    for pas, col in ((lw, BLACK), (0, fill)):
        for dx, dy, r in parts:
            rr = s * r + pas
            d.ellipse([cx + dx * s - rr, cy + dy * s - rr, cx + dx * s + rr, cy + dy * s + rr], fill=col)
        d.rounded_rectangle([box[0] - pas, box[1] - pas, box[2] + pas, box[3] + pas],
                            radius=int(s * 0.2) + pas, fill=col)


def icon(d, kind, cx, cy, s):
    """Disegna un'icona meteo centrata in (cx, cy), dimensione di riferimento s."""
    lw = max(2, int(s / 14))
    if kind == "sun":
        _sun(d, cx, cy, s * 0.42, lw)
    elif kind == "partly":
        _sun(d, cx + s * 0.3, cy - s * 0.3, s * 0.3, lw)
        _cloud(d, cx - s * 0.1, cy + s * 0.1, s * 0.75, lw)
    elif kind == "cloud":
        _cloud(d, cx, cy, s * 0.9, lw, fill=LIGHT)
    elif kind == "fog":
        _cloud(d, cx, cy - s * 0.25, s * 0.8, lw)
        for i in range(3):
            y = cy + s * 0.35 + i * s * 0.2
            d.line([cx - s * 0.6, y, cx + s * 0.6, y], fill=DARK, width=lw)
    else:
        _cloud(d, cx, cy - s * 0.25, s * 0.85, lw, fill=LIGHT)
        y0 = cy + s * 0.35
        if kind == "rain":
            for i in (-1, 0, 1):
                x = cx + i * s * 0.35
                d.line([x + s * 0.08, y0, x - s * 0.08, y0 + s * 0.4], fill=BLACK, width=lw + 1)
        elif kind == "snow":
            for i in (-1, 0, 1):
                x, y, r = cx + i * s * 0.35, y0 + s * 0.2, s * 0.09
                d.ellipse([x - r, y - r, x + r, y + r], fill=BLACK)
        elif kind == "storm":
            d.polygon([(cx + s * 0.1, y0 - s * 0.05), (cx - s * 0.2, y0 + s * 0.28), (cx, y0 + s * 0.28),
                       (cx - s * 0.12, y0 + s * 0.62), (cx + s * 0.25, y0 + s * 0.18),
                       (cx + s * 0.05, y0 + s * 0.18)], fill=BLACK)


# ---------------------------------------------------------------- utilita'
def text(d, xy, s, f, fill=BLACK, anchor="la"):
    d.text(xy, s, font=f, fill=fill, anchor=anchor)


def num(v, dec=1):
    return f"{v:.{dec}f}".replace(".", ",")


def energy(kwh):
    return (num(kwh / 1000, 2), "MWh") if kwh >= 1000 else (num(kwh, 1), "kWh")


# ---------------------------------------------------------------- sezioni
def draw_weather(d, w, y0, now):
    cur, daily, hourly = w["current"], w["daily"], w["hourly"]
    kind, desc = wmo(cur["weather_code"])
    icon(d, kind, 135, y0 + 105, 118)
    text(d, (255, y0 + 20), f"{round(cur['temperature_2m'])}°", font(130, True))
    fs = 32
    while fs > 20 and d.textlength(desc, font=font(fs, True)) > 235:
        fs -= 2
    text(d, (258, y0 + 172), desc, font(fs, True))
    x = 510
    rows = [
        ("Percepita", f"{round(cur['apparent_temperature'])}°"),
        ("Min/Max", f"{round(daily['temperature_2m_min'][0])}°/{round(daily['temperature_2m_max'][0])}°"),
        ("Pioggia", f"{daily['precipitation_probability_max'][0] or 0}%"),
        ("Vento", f"{round(cur['wind_speed_10m'])} km/h"),
        ("Umidità", f"{cur['relative_humidity_2m']}%"),
    ]
    for i, (k, v) in enumerate(rows):
        yy = y0 + 28 + i * 38
        text(d, (x, yy), k, font(22), DARK)
        text(d, (W - 30, yy), v, font(24, True), anchor="ra")

    # prossime ore
    y1 = y0 + 235
    times = [datetime.fromisoformat(t).replace(tzinfo=TZ) for t in hourly["time"]]
    start = next((i for i, t in enumerate(times) if t >= now), 0)
    idx = [start + k * 3 for k in range(6) if start + k * 3 < len(times)]
    cw = (W - 60) / max(1, len(idx))
    for n, i in enumerate(idx):
        cx = 30 + cw * n + cw / 2
        text(d, (cx, y1 - 8), times[i].strftime("%H:%M"), font(20), DARK, "ma")
        icon(d, wmo(hourly["weather_code"][i])[0], cx, y1 + 55, 46)
        text(d, (cx, y1 + 92), f"{round(hourly['temperature_2m'][i])}°", font(24, True), anchor="ma")
        p = hourly["precipitation_probability"][i] or 0
        text(d, (cx, y1 + 122), f"{p}%", font(18), DARK if p >= 30 else MID, "ma")

    # prossimi giorni
    y2 = y1 + 165
    d.line([30, y2 - 10, W - 30, y2 - 10], fill=LIGHT, width=2)
    days = list(range(1, min(5, len(daily["time"]))))
    cw = (W - 60) / max(1, len(days))
    for n, i in enumerate(days):
        cx = 30 + cw * n + cw / 2
        day = datetime.fromisoformat(daily["time"][i])
        text(d, (cx - 78, y2 + 8), GIORNI[day.weekday()], font(24, True))
        icon(d, wmo(daily["weather_code"][i])[0], cx + 58, y2 + 26, 36)
        text(d, (cx - 78, y2 + 44),
             f"{round(daily['temperature_2m_min'][i])}° / {round(daily['temperature_2m_max'][i])}°", font(21), DARK)
    y3 = y2 + 85
    air = w.get("_air")
    if air and air.get("european_aqi") is not None:
        d.line([30, y3, W - 30, y3], fill=LIGHT, width=2)
        text(d, (30, y3 + 12), "ARIA", font(20, True), DARK)
        lab = f"{aqi_label(air['european_aqi'])} ({round(air['european_aqi'])})"
        text(d, (100, y3 + 9), lab, font(24, True))
        det = "  ".join(f"{n} {round(air[k])}" for n, k in (("PM2.5", "pm2_5"), ("PM10", "pm10"), ("NO₂", "nitrogen_dioxide"))
                        if air.get(k) is not None) + " µg/m³"
        text(d, (W - 30, y3 + 13), det, font(19), DARK, "ra")
        y3 += 48
    return y3


def draw_solar(d, s, y0):
    text(d, (30, y0), "FOTOVOLTAICO", font(22, True), DARK)
    if s is None:
        text(d, (W / 2, y0 + 150), "Dati impianto non disponibili", font(26), MID, "ma")
        return
    k = s["realKpi"]
    text(d, (30, y0 + 38), num(float(k["realTimePower"]), 2), font(92, True))
    wv = d.textlength(num(float(k["realTimePower"]), 2), font=font(92, True))
    text(d, (42 + wv, y0 + 92), "kW", font(34, True), DARK)
    text(d, (32, y0 + 142), "produzione attuale", font(20), DARK)

    cells = [("Oggi", k["dailyEnergy"]), ("Mese", k["monthEnergy"]),
             ("Anno", k["yearEnergy"]), ("Totale", k["cumulativeEnergy"])]
    gx, gy, cw, ch = 400, y0 + 36, 165, 66
    for i, (lab, val) in enumerate(cells):
        x, y = gx + (i % 2) * cw, gy + (i // 2) * ch
        v, u = energy(float(val))
        text(d, (x, y), lab, font(19), DARK)
        text(d, (x, y + 22), v, font(30, True))
        text(d, (x + d.textlength(v, font=font(30, True)) + 6, y + 31), u, font(18), DARK)

    # curva di potenza di oggi
    pc = s.get("powerCurve") or {}
    vals = []
    for v in pc.get("activePower", []):
        try:
            vals.append(float(v))
        except (TypeError, ValueError):
            vals.append(None)
    x0, x1, yb, yt = 70, W - 30, H - 62, y0 + 204
    d.line([x0, yb, x1, yb], fill=BLACK, width=2)
    peak = max([v for v in vals if v is not None] or [0])
    top = max(1.0, math.ceil(peak))
    for frac in (0.5, 1.0):
        y = yb - (yb - yt) * frac
        d.line([x0, y, x1, y], fill=LIGHT, width=1)
        text(d, (x0 - 8, y), num(top * frac, 1), font(17), DARK, "rm")
    text(d, (x0 - 8, yt - 22), "kW", font(17), DARK, "rm")
    n = len(vals) or 288
    for hh in (0, 6, 12, 18, 24):
        x = x0 + (x1 - x0) * hh / 24
        d.line([x, yb, x, yb + 6], fill=BLACK, width=2)
        text(d, (x, yb + 10), f"{hh:02d}", font(17), DARK, "ma")
    pts = [(x0 + (x1 - x0) * i / (n - 1), yb - (yb - yt) * min(v, top) / top)
           for i, v in enumerate(vals) if v is not None]
    if len(pts) > 1:
        d.polygon([(pts[0][0], yb)] + pts + [(pts[-1][0], yb)], fill=LIGHT)
        d.line(pts, fill=BLACK, width=3)
    text(d, (x1, yt - 22), f"picco {num(peak, 2)} kW", font(18), DARK, "ra")


def render(weather, solar, place, now, errors):
    img = Image.new("L", (W, H), WHITE)
    d = ImageDraw.Draw(img)
    text(d, (30, 22), f"{GIORNI_L[now.weekday()]} {now.day} {MESI[now.month - 1]}", font(34, True))
    text(d, (W - 30, 28), place, font(26), DARK, "ra")
    d.line([30, 76, W - 30, 76], fill=BLACK, width=3)
    if weather:
        y = draw_weather(d, weather, 90, now)
    else:
        text(d, (W / 2, 280), "Meteo non disponibile", font(26), MID, "ma")
        y = 575
    d.line([30, y, W - 30, y], fill=BLACK, width=3)
    draw_solar(d, solar, y + 16)
    foot = f"Aggiornato alle {now.strftime('%H:%M')}"
    if errors:
        foot += "  ·  " + ", ".join(errors)
    text(d, (W / 2, H - 14), foot, font(16), MID, "ms")
    return img


# ---------------------------------------------------------------- fotovoltaico (schermata dedicata)
HIST_KEEP_DAYS = 400


def curve_of(s):
    """Potenza di oggi, un valore ogni 5 minuti (None dove manca)."""
    out = []
    for v in (s.get("powerCurve") or {}).get("activePower", []):
        try:
            out.append(float(v))
        except (TypeError, ValueError):
            out.append(None)
    return out


def load_history(site_url):
    """Storico pubblicato in precedenza: primo avvio -> vuoto, errore di rete -> eccezione.

    Pages sostituisce tutto a ogni pubblicazione: se lo storico non si legge, meglio non pubblicare."""
    empty = {"days": {}, "months": {}, "years": {}, "curves": {}}
    url = site_url.rstrip("/") + "/pv-history.json"
    last = None
    for n in range(3):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 kobo-deck"})
            with urllib.request.urlopen(req, timeout=20) as r:
                h = json.loads(r.read().decode("utf-8"))
            for k in empty:
                h.setdefault(k, {})
            return h
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return empty
            last = e
        except Exception as e:  # noqa: BLE001
            last = e
        time.sleep(3 * (n + 1))
    raise last


def update_history(h, s, now):
    k = s["realKpi"]
    h["months"][now.strftime("%Y-%m")] = round(float(k["monthEnergy"]), 1)
    h["years"][now.strftime("%Y")] = round(float(k["yearEnergy"]), 1)
    if now.hour < 5:  # di notte il Kiosk puo' ancora mostrare i dati del giorno prima
        return
    day = now.strftime("%Y-%m-%d")
    vals = curve_of(s)
    peak = max([v for v in vals if v is not None] or [0.0])
    d = h["days"].setdefault(day, {"kwh": 0.0, "peak": 0.0})
    d["kwh"] = round(max(d["kwh"], float(k["dailyEnergy"])), 2)
    d["peak"] = round(max(d["peak"], peak), 2)
    h["curves"][day] = [None if v is None else round(v, 2) for v in vals]
    for old in sorted(h["curves"])[:-3]:
        del h["curves"][old]
    for old in sorted(h["days"])[:-HIST_KEEP_DAYS]:
        del h["days"][old]


def intit(n):
    return f"{int(round(n)):,}".replace(",", ".")


def draw_pv(s, hist, now, errors):
    """Schermata fotovoltaico, verticale 758x1024."""
    img = Image.new("L", (W, H), WHITE)
    d = ImageDraw.Draw(img)
    text(d, (30, 20), "Fotovoltaico", font(34, True))
    text(d, (W - 30, 28), f"{GIORNI[now.weekday()]} {now.day} {MESI[now.month - 1][:3]}", font(26), DARK, "ra")
    d.line([30, 76, W - 30, 76], fill=BLACK, width=3)
    foot = f"Aggiornato alle {now.strftime('%H:%M')}" + ("  ·  " + ", ".join(errors) if errors else "")
    text(d, (W / 2, H - 14), foot, font(16), MID, "ms")
    if s is None:
        text(d, (W / 2, 420), "Dati impianto non disponibili", font(26), MID, "ma")
        return img
    k = s["realKpi"]
    power, daily = float(k["realTimePower"]), float(k["dailyEnergy"])
    vals = curve_of(s)
    peak = max([v for v in vals if v is not None] or [0.0])
    today = now.strftime("%Y-%m-%d")
    yday = (now - timedelta(days=1)).strftime("%Y-%m-%d")
    days = hist.get("days", {})
    yd = days.get(yday)

    # --- adesso / oggi / picco
    text(d, (30, 90), num(power, 2), font(88, True))
    wv = d.textlength(num(power, 2), font=font(88, True))
    text(d, (42 + wv, 146), "kW", font(32, True), DARK)
    text(d, (32, 192), "produzione attuale", font(20), DARK)
    text(d, (420, 92), "OGGI", font(18, True), DARK)
    text(d, (420, 112), num(daily, 1), font(40, True))
    text(d, (420 + d.textlength(num(daily, 1), font=font(40, True)) + 8, 130), "kWh", font(20), DARK)
    if yd:
        text(d, (W - 30, 126), f"ieri {num(yd['kwh'], 1)}", font(20), DARK, "ra")
    text(d, (420, 162), "PICCO", font(18, True), DARK)
    pt = ""
    if peak > 0:
        i = next(i for i, v in enumerate(vals) if v == peak)
        pt = f"alle {i * 5 // 60:02d}:{i * 5 % 60:02d}"
    text(d, (420, 180), f"{num(peak, 2)} kW", font(30, True))
    text(d, (W - 30, 190), pt, font(20), DARK, "ra")

    # --- andamento di oggi (con la curva di ieri come riferimento)
    d.line([30, 226, W - 30, 226], fill=LIGHT, width=2)
    text(d, (30, 236), "ANDAMENTO DI OGGI", font(18, True), DARK)
    ycurve = hist.get("curves", {}).get(yday)
    x0, x1, yt, yb = 70, W - 30, 292, 494
    top = max(1.0, math.ceil(max([peak] + [v for v in (ycurve or []) if v is not None])))
    d.line([x0, yb, x1, yb], fill=BLACK, width=2)
    for frac in (0.5, 1.0):
        y = yb - (yb - yt) * frac
        d.line([x0, y, x1, y], fill=LIGHT, width=1)
        text(d, (x0 - 8, y), num(top * frac, 1), font(17), DARK, "rm")
    text(d, (x0 - 8, yt - 16), "kW", font(17), DARK, "rm")
    for hh in (0, 6, 12, 18, 24):
        x = x0 + (x1 - x0) * hh / 24
        d.line([x, yb, x, yb + 6], fill=BLACK, width=2)
        text(d, (x, yb + 9), f"{hh:02d}", font(17), DARK, "ma")

    def pts_of(v):
        n = len(v) or 288
        return [(x0 + (x1 - x0) * i / (n - 1), yb - (yb - yt) * min(c, top) / top)
                for i, c in enumerate(v) if c is not None]

    if ycurve:
        yp = pts_of(ycurve)
        if len(yp) > 1:
            d.line(yp, fill=MID, width=2)
        text(d, (W - 30, 238), "— oggi    ·· ieri", font(16), DARK, "ra")
    tp = pts_of(vals)
    if len(tp) > 1:
        d.polygon([(tp[0][0], yb)] + tp + [(tp[-1][0], yb)], fill=LIGHT)
        d.line(tp, fill=BLACK, width=3)

    # --- ultimi 14 giorni
    d.line([30, 524, W - 30, 524], fill=LIGHT, width=2)
    text(d, (30, 534), "ULTIMI 14 GIORNI (kWh)", font(18, True), DARK)
    seq = [(now - timedelta(days=13 - i)) for i in range(14)]
    series = []
    for dt in seq:
        key = dt.strftime("%Y-%m-%d")
        v = daily if key == today else (days.get(key) or {}).get("kwh")
        series.append((dt, v))
    known = [v for _, v in series if v is not None]
    cx0, cx1, cyt, cyb = 40, W - 30, 580, 722
    d.line([cx0, cyb, cx1, cyb], fill=BLACK, width=2)
    month_days = {kk: vv["kwh"] for kk, vv in days.items() if kk.startswith(now.strftime("%Y-%m"))}
    month_days[today] = max(daily, month_days.get(today, 0))
    bk = max(month_days, key=month_days.get)
    if month_days[bk] > 0:
        text(d, (W - 30, 536), f"migliore del mese: {bk[8:10]}/{bk[5:7]} · {num(month_days[bk], 1)}", font(18), DARK, "ra")
    if len(known) < 2:
        text(d, ((cx0 + cx1) / 2, (cyt + cyb) / 2), "storico in raccolta da oggi", font(20), MID, "mm")
    cmax = max(known + [1.0])
    slot = (cx1 - cx0) / 14
    for i, (dt, v) in enumerate(series):
        cx = cx0 + slot * i + slot / 2
        text(d, (cx, cyb + 6), str(dt.day), font(15, dt.strftime("%Y-%m-%d") == today), DARK, "ma")
        if v is None:
            continue
        hgt = max((cyb - cyt) * v / cmax, 2)  # almeno 2 px, anche con produzione zero
        is_today = dt.strftime("%Y-%m-%d") == today
        d.rectangle([cx - slot * 0.3, cyb - hgt, cx + slot * 0.3, cyb - 1], fill=BLACK if is_today else MID)
        text(d, (cx, cyb - hgt - 3), num(v, 0 if v >= 100 else 1), font(14), DARK, "ms")

    # --- mese / anno / totale
    d.line([30, 758, W - 30, 758], fill=LIGHT, width=2)
    months, years = hist.get("months", {}), hist.get("years", {})
    prev_month = (now.replace(day=1) - timedelta(days=1)).strftime("%Y-%m")
    mval, yval = float(k["monthEnergy"]), float(k["yearEnergy"])
    cells = [
        ("MESE", energy(mval), f"media {num(mval / max(now.day, 1), 1)} kWh/giorno",
         f"mese scorso {energy(months[prev_month])[0]} {energy(months[prev_month])[1]}" if prev_month in months else ""),
        ("ANNO", energy(yval), f"media {num(yval / max(now.timetuple().tm_yday, 1), 1)} kWh/giorno",
         (f"anno scorso {energy(years[str(now.year - 1)])[0]} {energy(years[str(now.year - 1)])[1]}"
          if str(now.year - 1) in years else "")),
        ("TOTALE", energy(float(k["cumulativeEnergy"])), "dall'installazione", ""),
    ]
    cw = (W - 60) / 3
    for i, (lab, (v, u), sub1, sub2) in enumerate(cells):
        x = 30 + cw * i
        text(d, (x, 770), lab, font(18, True), DARK)
        text(d, (x, 792), v, font(36, True))
        text(d, (x + d.textlength(v, font=font(36, True)) + 6, 808), u, font(18), DARK)
        text(d, (x, 840), sub1, font(16), DARK)
        if sub2:
            text(d, (x, 862), sub2, font(16), DARK)

    # --- ambiente (da inizio impianto)
    sc = s.get("socialContribution") or {}
    if sc.get("co2Reduction") is not None:
        d.line([30, 890, W - 30, 890], fill=LIGHT, width=2)
        text(d, (30, 898), "DA INIZIO IMPIANTO", font(18, True), DARK)
        cum = max(float(k["cumulativeEnergy"]), 1.0)
        co2, coal = float(sc["co2Reduction"]), float(sc.get("standardCoalSavings") or 0)
        if co2 / cum < 0.01:  # il Kiosk li da' in tonnellate
            co2, coal = co2 * 1000, coal * 1000
        items = [("CO₂ evitata", f"{intit(co2)} kg"), ("alberi equivalenti", intit(float(sc.get("equivalentTreePlanting") or 0))),
                 ("carbone risparmiato", f"{intit(coal)} kg")]
        for i, (lab, val) in enumerate(items):
            x = 30 + cw * i
            text(d, (x, 924), val, font(28, True))
            text(d, (x, 960), lab, font(16), DARK)
    return img


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sample", help="cartella con weather.json e solar.json (test offline)")
    ap.add_argument("--out", default="public/deck.png")
    a = ap.parse_args()
    now = datetime.now(TZ)
    weather = solar = None
    errors = []
    status = {"generato": now.isoformat(timespec="minutes")}
    if a.sample:
        weather = json.load(open(os.path.join(a.sample, "weather.json")))
        solar = json.load(open(os.path.join(a.sample, "solar.json")))
        now = datetime.fromisoformat(weather["current"]["time"]).replace(tzinfo=TZ)
    else:
        try:
            weather = fetch_weather(os.environ.get("LAT", "45.4905"), os.environ.get("LON", "9.2953"))
            status["meteo"] = "ok"
            try:
                weather["_air"] = fetch_air(os.environ.get("LAT", "45.4905"), os.environ.get("LON", "9.2953"))
            except Exception as e:  # noqa: BLE001
                print("aria:", e, file=sys.stderr)
                status["aria"] = msg(e)
                try:  # riusa l'ultimo valore pubblicato
                    weather["_air"] = get_json(os.environ["SITE_URL"].rstrip("/") + "/weather.json", tries=1).get("_air")
                except Exception:  # noqa: BLE001
                    pass
        except Exception as e:  # noqa: BLE001
            print("meteo:", e, file=sys.stderr)
            status["meteo"] = "errore: " + msg(e)
            # riusa l'ultimo meteo pubblicato, se recente (meno di 3 ore)
            try:
                old = get_json(os.environ["SITE_URL"].rstrip("/") + "/weather.json", tries=1)
                age = now - datetime.fromisoformat(old["current"]["time"]).replace(tzinfo=TZ)
                if age.total_seconds() < 24 * 3600:
                    weather = old
                    errors.append("meteo delle " + old["current"]["time"][11:16])
                    status["meteo_cache"] = old["current"]["time"]
            except Exception as e2:  # noqa: BLE001
                print("meteo cache:", e2, file=sys.stderr)
                status["meteo_cache"] = "non disponibile: " + msg(e2)
            if weather is None:
                errors.append("meteo non aggiornato")
        try:
            solar = fetch_solar(os.environ["KIOSK_URL"])
            status["kiosk"] = "ok"
        except Exception as e:  # noqa: BLE001
            print("solare:", type(e).__name__, file=sys.stderr)
            status["kiosk"] = "errore: " + msg(e)
            errors.append("impianto non aggiornato")
    img = render(weather, solar, os.environ.get("PLACE", "Segrate"), now, errors)
    outdir = os.path.dirname(a.out) or "."
    os.makedirs(outdir, exist_ok=True)
    img.save(a.out, optimize=True)

    # schermata titoli (un errore qui non deve mai bloccare il deck meteo/fotovoltaico)
    quotes = {}
    try:
        if a.sample:
            qpath = os.path.join(a.sample, "quotes.json")
            quotes = json.load(open(qpath)) if os.path.exists(qpath) else {}
        else:
            quotes = fetch_quotes(os.environ.get("SITE_URL"))
        stocks_img = draw_stocks(quotes, now)
        stocks_img.save(os.path.join(outdir, "stocks.png"), optimize=True)
        status["titoli"] = f"{len(quotes)}/{len(STOCKS)} quotazioni, " + ", ".join(
            s_ for _, s_ in STOCKS if s_ not in quotes or quotes[s_].get("stale")) + " mancanti o vecchie"
        if quotes and not a.sample:
            with open(os.path.join(outdir, "quotes.json"), "w") as f:
                json.dump(quotes, f)
    except Exception as e:  # noqa: BLE001
        print("titoli:", type(e).__name__, e, file=sys.stderr)
        status["titoli"] = "errore: " + msg(e, True)
        quotes = {}

    # schermata fotovoltaico, con lo storico raccolto a ogni esecuzione
    site_url = os.environ.get("SITE_URL")
    hist = {"days": {}, "months": {}, "years": {}, "curves": {}}
    if site_url and not a.sample:
        try:
            hist = load_history(site_url)
        except Exception as e:  # noqa: BLE001
            # senza lo storico non si pubblica: Pages sostituirebbe tutto e lo storico andrebbe perso
            print("storico non leggibile, pubblicazione annullata:", type(e).__name__, e, file=sys.stderr)
            sys.exit(3)
        if solar is not None:
            try:
                update_history(hist, solar, now)
            except Exception as e:  # noqa: BLE001
                print("storico:", type(e).__name__, e, file=sys.stderr)
                status["storico"] = "errore: " + msg(e, True)
        with open(os.path.join(outdir, "pv-history.json"), "w") as f:
            json.dump(hist, f, separators=(",", ":"))
    try:
        draw_pv(solar, hist, now, errors).save(os.path.join(outdir, "solar.png"), optimize=True)
        status["solar_png"] = "ok"
    except Exception as e:  # noqa: BLE001
        print("fotovoltaico:", type(e).__name__, e, file=sys.stderr)
        status["solar_png"] = "errore: " + msg(e, True)

    if not a.sample:
        # endpoint letto dal Kobo: indirizzo dell'immagine e secondi fino al prossimo aggiornamento.
        # Le due schermate si alternano a ogni quarto d'ora.
        site = os.environ.get("SITE_URL", "").rstrip("/")
        api = os.path.join(outdir, "api")
        os.makedirs(api, exist_ok=True)
        screen = "stocks" if quotes and int(now.timestamp() // 900) % 2 else "deck"
        refresh = 3600 if now.hour < 6 else 900

        def write_display(folder, shown):
            os.makedirs(folder, exist_ok=True)
            with open(os.path.join(folder, "display"), "w") as f:
                json.dump({"status": 0, "image_url": f"{site}/{shown}.png",
                           "filename": f"{shown}-{now:%Y%m%d-%H%M}", "refresh_rate": refresh}, f)

        write_display(api, screen)  # alternata
        write_display(os.path.join(outdir, "api-meteo"), "deck")  # solo meteo e fotovoltaico
        write_display(os.path.join(outdir, "api-titoli"), "stocks")  # solo titoli
        write_display(os.path.join(outdir, "api-fotovoltaico"), "solar")  # solo fotovoltaico
    if weather is not None and not a.sample:
        with open(os.path.join(outdir, "weather.json"), "w") as f:
            json.dump(weather, f)
    status["giorni_storico"] = len(hist.get("days", {}))
    if not a.sample:
        with open(os.path.join(outdir, "status.json"), "w") as f:
            json.dump(status, f, indent=1, ensure_ascii=False)
    print("scritto", a.out)
    if weather is None and solar is None:
        sys.exit(1)


if __name__ == "__main__":
    main()
