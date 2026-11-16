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
import os
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime
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


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sample", help="cartella con weather.json e solar.json (test offline)")
    ap.add_argument("--out", default="public/deck.png")
    a = ap.parse_args()
    now = datetime.now(TZ)
    weather = solar = None
    errors = []
    if a.sample:
        weather = json.load(open(os.path.join(a.sample, "weather.json")))
        solar = json.load(open(os.path.join(a.sample, "solar.json")))
        now = datetime.fromisoformat(weather["current"]["time"]).replace(tzinfo=TZ)
    else:
        try:
            weather = fetch_weather(os.environ.get("LAT", "45.4905"), os.environ.get("LON", "9.2953"))
            try:
                weather["_air"] = fetch_air(os.environ.get("LAT", "45.4905"), os.environ.get("LON", "9.2953"))
            except Exception as e:  # noqa: BLE001
                print("aria:", e, file=sys.stderr)
                try:  # riusa l'ultimo valore pubblicato
                    weather["_air"] = get_json(os.environ["SITE_URL"].rstrip("/") + "/weather.json", tries=1).get("_air")
                except Exception:  # noqa: BLE001
                    pass
        except Exception as e:  # noqa: BLE001
            print("meteo:", e, file=sys.stderr)
            # riusa l'ultimo meteo pubblicato, se recente (meno di 3 ore)
            try:
                old = get_json(os.environ["SITE_URL"].rstrip("/") + "/weather.json", tries=1)
                age = now - datetime.fromisoformat(old["current"]["time"]).replace(tzinfo=TZ)
                if age.total_seconds() < 3 * 3600:
                    weather = old
                    errors.append("meteo delle " + old["current"]["time"][11:16])
            except Exception as e2:  # noqa: BLE001
                print("meteo cache:", e2, file=sys.stderr)
            if weather is None:
                errors.append("meteo non aggiornato")
        try:
            solar = fetch_solar(os.environ["KIOSK_URL"])
        except Exception as e:  # noqa: BLE001
            print("solare:", type(e).__name__, file=sys.stderr)
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
        if quotes and not a.sample:
            with open(os.path.join(outdir, "quotes.json"), "w") as f:
                json.dump(quotes, f)
    except Exception as e:  # noqa: BLE001
        print("titoli:", type(e).__name__, e, file=sys.stderr)
        quotes = {}

    if not a.sample:
        # endpoint letto dal Kobo: indirizzo dell'immagine e secondi fino al prossimo aggiornamento.
        # Le due schermate si alternano a ogni quarto d'ora.
        site = os.environ.get("SITE_URL", "").rstrip("/")
        api = os.path.join(outdir, "api")
        os.makedirs(api, exist_ok=True)
        screen = "stocks" if quotes and int(now.timestamp() // 900) % 2 else "deck"
        with open(os.path.join(api, "display"), "w") as f:
            json.dump({"status": 0, "image_url": f"{site}/{screen}.png",
                       "filename": f"{screen}-{now:%Y%m%d-%H%M}",
                       "refresh_rate": 3600 if now.hour < 6 else 900}, f)
    if weather is not None and not a.sample:
        with open(os.path.join(outdir, "weather.json"), "w") as f:
            json.dump(weather, f)
    print("scritto", a.out)
    if weather is None and solar is None:
        sys.exit(1)


if __name__ == "__main__":
    main()
