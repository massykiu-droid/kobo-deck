#!/usr/bin/env python3
"""Genera l'immagine del deck Kobo: meteo (Open-Meteo) + fotovoltaico (FusionSolar Kiosk).

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
    text(d, (258, y0 + 172), desc, font(32, True))
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
        text(d, (cx, y1), times[i].strftime("%H:%M"), font(20), DARK, "ma")
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
    return y2 + 85


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
    x0, x1, yb, yt = 70, W - 30, H - 62, y0 + 200
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
    os.makedirs(os.path.dirname(a.out) or ".", exist_ok=True)
    img.save(a.out, optimize=True)
    if weather is not None and not a.sample:
        with open(os.path.join(os.path.dirname(a.out) or ".", "weather.json"), "w") as f:
            json.dump(weather, f)
    print("scritto", a.out)
    if weather is None and solar is None:
        sys.exit(1)


if __name__ == "__main__":
    main()
