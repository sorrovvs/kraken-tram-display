"""
Kraken Tram Display - live Wiener Linien departures on an NZXT Kraken LCD.

Commands:
  python tram_lcd.py setup             interactive setup: pick station, lines, tilt, language
  python tram_lcd.py                   run the display (same as "run")
  python tram_lcd.py search NAME       find stations by name
  python tram_lcd.py lines STATION     list lines/directions departing at a station (name or DIVA)
  python tram_lcd.py preview [-o F]    render one frame to a PNG (no Kraken needed)

Options:
  --config PATH                        use another config file (default: config.toml next to this script)

NZXT CAM must NOT be running while the display runs (it locks the USB device).
"""

import argparse
import csv
import io
import json
import os
import sys
import tempfile
import time
import tomllib
import traceback
import unicodedata
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from PIL import Image, ImageDraw, ImageFont

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_CONFIG = os.path.join(HERE, "config.toml")

MONITOR_API = "https://www.wienerlinien.at/ogd_realtime/monitor?diva={}"
STOPS_CSV = "https://www.wienerlinien.at/ogd_realtime/doku/ogd/wienerlinien-ogd-haltepunkte.csv"
USER_AGENT = "kraken-tram-display"
MAX_ROWS = 4


# --- configuration ------------------------------------------------------------
def hex_rgb(value, default):
    try:
        v = value.lstrip("#")
        return tuple(int(v[i:i + 2], 16) for i in (0, 2, 4))
    except (AttributeError, ValueError, IndexError):
        return default


@dataclass
class Config:
    station: str = ""
    diva: str = ""
    departures: list = field(default_factory=list)  # [{"line": "42", "towards": "Antonigasse"}], towards optional
    title: str = ""                                  # shown at the top; empty = station name
    language: str = "de"
    tilt: float = 0.0
    brightness: int | None = None
    refresh_seconds: int = 10
    footer: str = "© Sorrow"
    clock_color: tuple = (200, 170, 255)
    footer_color: tuple = (200, 170, 255)

    @classmethod
    def load(cls, path):
        with open(path, "rb") as f:
            raw = tomllib.load(f)
        st, disp, col = raw.get("station", {}), raw.get("display", {}), raw.get("colors", {})
        c = cls(
            station=str(st.get("name", "")),
            diva=str(st.get("diva", "")),
            departures=[
                {"line": str(d["line"]), "towards": str(d.get("towards", ""))}
                for d in raw.get("departures", []) if "line" in d
            ],
            title=str(disp.get("title", "")),
            language=str(disp.get("language", "de")).lower(),
            tilt=float(disp.get("tilt", 0)),
            refresh_seconds=max(5, int(disp.get("refresh_seconds", 10))),
            footer=str(disp.get("footer", "© Sorrow")),
        )
        b = disp.get("brightness", -1)
        c.brightness = int(b) if isinstance(b, int) and 0 <= b <= 100 else None
        c.clock_color = hex_rgb(col.get("clock"), c.clock_color)
        c.footer_color = hex_rgb(col.get("footer"), c.footer_color)
        if not c.diva:
            raise ValueError("config has no station.diva - run: python tram_lcd.py setup")
        if c.language not in TEXT:
            c.language = "de"
        return c

    def save(self, path):
        def q(s):  # JSON strings are valid TOML basic strings
            return json.dumps(s, ensure_ascii=False)
        lines = [
            "# Kraken Tram Display configuration - created by `python tram_lcd.py setup`.",
            "# Edit freely; restart the display afterwards.",
            "",
            "[station]",
            f"name = {q(self.station)}",
            f"diva = {q(self.diva)}          # find others with: python tram_lcd.py search <name>",
            "",
            f"# Rows to show (max {MAX_ROWS}), in this order. `towards` is optional - leave it out to",
            "# show every direction of that line. No [[departures]] at all = show everything.",
            "# List what departs here with: python tram_lcd.py lines <diva>",
        ]
        for d in self.departures:
            lines += ["", "[[departures]]", f"line = {q(d['line'])}"]
            if d.get("towards"):
                lines.append(f"towards = {q(d['towards'])}")
        lines += [
            "",
            "[display]",
            f"title = {q(self.title)}               # empty = station name",
            f"language = {q(self.language)}             # \"de\" or \"en\"",
            f"tilt = {self.tilt:g}                    # degrees counter-clockwise (negative = clockwise)",
            f"brightness = {self.brightness if self.brightness is not None else -1}"
            "             # 0-100, -1 = leave unchanged",
            f"refresh_seconds = {self.refresh_seconds}        # data refresh interval (min 5)",
            f"footer = {q(self.footer)}         # empty = no footer badge",
            "",
            "[colors]",
            f"clock = {q('#%02X%02X%02X' % self.clock_color)}",
            f"footer = {q('#%02X%02X%02X' % self.footer_color)}",
            "",
        ]
        with open(path, "w", encoding="utf-8", newline="\n") as f:
            f.write("\n".join(lines))


CFG = Config()

TEXT = {
    "de": {"now": "jetzt", "live": "live · neu in {n} s", "offline": "keine Verbindung",
           "stale": "keine Live-Daten seit {m} min"},
    "en": {"now": "now", "live": "live · refresh in {n} s", "offline": "no connection",
           "stale": "no live data for {m} min"},
}


def tr(key, **kw):
    return TEXT[CFG.language][key].format(**kw)


# --- look ----------------------------------------------------------------------
SIZE = 320
BG = (0, 0, 0)
FG = (242, 242, 242)
DIM = (140, 140, 140)
RED = (227, 0, 15)
SOON = (255, 204, 0)
GREEN = (0, 150, 60)

# badge colours: U-Bahn lines by line, everything else by vehicle type
LINE_COLORS = {"U1": (227, 0, 15), "U2": (167, 98, 163), "U3": (238, 125, 0),
               "U4": (49, 159, 73), "U5": (0, 150, 160), "U6": (157, 105, 48)}
TYPE_COLORS = {"ptTram": RED, "ptBusCity": (20, 50, 110), "ptBusNight": (20, 50, 110),
               "ptTramWLB": (0, 82, 147), "ptTrainS": (0, 137, 196), "ptMetro": (90, 90, 90)}


def badge_color(line, typ):
    return LINE_COLORS.get(line) or TYPE_COLORS.get(typ, RED)


FONT_DIRS = [os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts"),
             "/usr/share/fonts/truetype/dejavu", "/Library/Fonts"]
_font_cache = {}


def font(bold, size):
    key = (bold, size)
    if key not in _font_cache:
        names = ["segoeuib.ttf", "DejaVuSans-Bold.ttf", "Arial Bold.ttf"] if bold else \
                ["segoeui.ttf", "DejaVuSans.ttf", "Arial.ttf"]
        for d in FONT_DIRS:
            for n in names:
                try:
                    _font_cache[key] = ImageFont.truetype(os.path.join(d, n), size)
                    break
                except OSError:
                    continue
            if key in _font_cache:
                break
        else:
            _font_cache[key] = ImageFont.load_default(size)
    return _font_cache[key]


F_CLOCK = font(True, 19)
F_DIR = font(False, 20)
F_NEXT = font(True, 32)
F_LATER = font(False, 18)
F_STATUS = font(True, 16)
F_CREDIT = font(True, 14)

# animation: one GIF per refresh cycle, every frame drawn for its own moment in time
FRAME_S = 0.25                             # GIF frame length
FADE_SECONDS = 3.0                         # red notification: one blink, then fade
_fade_n = round(FADE_SECONDS / FRAME_S)
FADE_STEPS = (1.0, 0.0) + tuple(1.0 - i / _fade_n for i in range(_fade_n))
BLINK_FRAMES = 2                           # "now" on/off every 2 frames = 0.5 s
OVERRUN_MARGIN_S = 2.0                     # extra frames past the expected next GIF
MAX_OVERRUN_S = 8.0                        # cap for the learned fetch+upload gap


# --- liquidctl / winusbcdc workarounds -------------------------------------------
# See docs/TROUBLESHOOTING.md for details. Imported lazily so setup/search/preview
# work without liquidctl talking to any hardware.
def _patch_liquidctl():
    import liquidctl.driver.kraken3 as kraken3
    from liquidctl.driver.kraken3 import KrakenZ3

    # Other programs (e.g. SignalRGB) also talk to the Kraken; it answers them with
    # ff01 messages that land in our queue. liquidctl gives up after 12 messages,
    # which isn't enough when that traffic is busy.
    kraken3._MAX_READ_ATTEMPTS = 100

    if sys.platform == "win32":
        # winusbcdc 1.7 only returns devices when a vid/pid filter is passed, but
        # liquidctl calls it without one, so the LCD bulk interface is never found.
        from winusbcdc import WinUsbPy

        pids = sorted({m[1] for m in KrakenZ3._MATCHES})
        orig = WinUsbPy.list_usb_devices.__func__
        WinUsbPy.list_usb_devices = classmethod(
            lambda cls, **kw: orig(cls, **{"vid": [0x1E71], "pid": pids, **kw}))

    # liquidctl treats the next HID message as the reply to an LCD command, but the
    # Kraken also sends periodic status reports. Wait for the matching reply instead.
    def write_then_read_matching(self, data):
        self._write(data)
        want = bytes([data[0] + 1, data[1]])
        for _ in range(100):
            msg = self._read()
            if bytes(msg[0:2]) == want:
                return msg
        raise RuntimeError(f"no reply {want.hex()} to command {bytes(data[:2]).hex()}")

    KrakenZ3._write_then_read = write_then_read_matching
    return KrakenZ3


def log(*a):
    print(datetime.now().strftime("%H:%M:%S"), *a, flush=True)


def light_init(dev):
    """liquidctl's initialize() minus the lighting query, which the Z3 never answers."""
    dev.device.clear_enqueued_reports()
    dev._write([0x70, 0x02, 0x01, 0xB8, 1])  # status report interval
    dev._write([0x70, 0x01])
    dev._status = []
    dev._get_fw_version(clear_reports=False)
    dev._write([0x30, 0x01])  # lcd info (brightness/orientation)
    dev._read_until({b"\x31\x01": dev.parse_lcd_info})


def close_kraken(dev):
    for step in (lambda: dev.bulk_device and dev.bulk_device.close_winusb_device(), dev.disconnect):
        try:
            step()
        except Exception:
            pass


def connect_kraken(kraken_cls):
    from liquidctl import find_liquidctl_devices

    for dev in find_liquidctl_devices():
        if isinstance(dev, kraken_cls):
            dev.connect()
            if dev.bulk_device is None:
                close_kraken(dev)
                raise RuntimeError("Kraken LCD interface not accessible - is NZXT CAM running?")
            light_init(dev)
            if CFG.brightness is not None:
                dev.set_screen("lcd", "brightness", CFG.brightness)
            log("connected:", dev.description)
            return dev
    raise RuntimeError("No Kraken with an LCD found (supported: Kraken Z3, 2023, 2024)")


# --- Wiener Linien data ---------------------------------------------------------
def http_json(url):
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.load(r)


def norm(s):
    s = unicodedata.normalize("NFKD", s.casefold().replace("ß", "ss"))
    return "".join(ch for ch in s if not unicodedata.combining(ch)).strip()


def monitor_lines(diva):
    """All (line, towards) at a station, merged across platforms: dicts with line/towards/type/times."""
    data = http_json(MONITOR_API.format(urllib.parse.quote(diva)))
    merged = {}
    for mon in data.get("data", {}).get("monitors", []):
        for line in mon.get("lines", []):
            key = (line.get("name", ""), line.get("towards", "").strip())
            entry = merged.setdefault(key, {"line": key[0], "towards": key[1],
                                            "type": line.get("type", ""), "times": []})
            for dep in line.get("departures", {}).get("departure", []):
                t = dep.get("departureTime", {})
                iso = t.get("timeReal") or t.get("timePlanned")
                if iso:
                    entry["times"].append(datetime.strptime(iso, "%Y-%m-%dT%H:%M:%S.%f%z"))
    for e in merged.values():
        e["times"].sort()
    return list(merged.values())


def line_sort_key(line):
    """Natural order: U-Bahn first, then numbers (2 < 10 < 13A), then letters."""
    name = line["line"]
    num = "".join(ch for ch in name if ch.isdigit())
    return (0 if name.startswith("U") else 1, int(num) if num else 999, name, line["towards"])


def select_rows(lines):
    """Apply the [[departures]] filter and order from the config."""
    if not CFG.departures:
        return sorted(lines, key=line_sort_key)[:MAX_ROWS]
    rows = []
    for want in CFG.departures:
        for e in sorted(lines, key=line_sort_key):
            if e["line"].casefold() == want["line"].casefold() and \
                    (not want["towards"] or norm(e["towards"]) == norm(want["towards"])) and e not in rows:
                rows.append(e)
    return rows[:MAX_ROWS]


def fetch():
    return [{**r, "times": r["times"][:3]} for r in select_rows(monitor_lines(CFG.diva))]


HYSTERESIS_S = 30  # a countdown goes up only once the new time is this far into the higher minute


def steady(rows, held, now=None):
    """The real-time estimates wobble by up to about ±30 s between polls, which made countdowns
    go 4' -> 5' -> 4' -> 5'. A countdown may now go up only when the new time is at least
    HYSTERESIS_S into the higher minute (a real delay, or the next vehicle once this one has
    left); until then the time is held at the end of the minute already shown. Going down is
    always immediate. `held` keeps the times shown so far, per row."""
    now = now or datetime.now(timezone.utc)
    out = []
    for r in rows:
        key = (r["line"], r["towards"])
        old = held.get(key, [])
        times = []
        for i, t in enumerate(r["times"]):
            if i < len(old) and t > old[i]:
                shown = max(0, int((old[i] - now).total_seconds() // 60))
                new = (t - now).total_seconds()
                if new // 60 > shown and new < (shown + 1) * 60 + HYSTERESIS_S:
                    t = now + timedelta(seconds=(shown + 1) * 60 - 1)  # latest time still showing `shown`
            times.append(t)
        held[key] = times
        out.append({**r, "times": times})
    return out


def stops_csv_path():
    base = os.environ.get("LOCALAPPDATA") or os.path.join(os.path.expanduser("~"), ".cache")
    folder = os.path.join(base, "kraken-tram-display")
    os.makedirs(folder, exist_ok=True)
    return os.path.join(folder, "stops.csv")


def load_stations():
    """{diva: name} from the Wiener Linien stop list (cached for 30 days)."""
    path = stops_csv_path()
    if not os.path.exists(path) or time.time() - os.path.getmtime(path) > 30 * 86400:
        req = urllib.request.Request(STOPS_CSV, headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(req, timeout=30) as r, open(path, "wb") as f:
            f.write(r.read())
    stations = {}
    with open(path, encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f, delimiter=";"):
            if row.get("DIVA") and row.get("StopText"):
                stations.setdefault(row["DIVA"], row["StopText"])
    return stations


def search_stations(query):
    q = norm(query)
    hits = [(diva, name) for diva, name in load_stations().items() if q in norm(name)]
    return sorted(hits, key=lambda h: (not norm(h[1]).startswith(q), len(h[1]), h[1]))


# --- view / render --------------------------------------------------------------
def short_dir(name):
    for suffix in (" S U", " U", " S"):
        if name.endswith(suffix):
            return name[: -len(suffix)]
    return name


def fit(draw, text, f, max_w):
    if draw.textlength(text, font=f) <= max_w:
        return text
    while text and draw.textlength(text + "…", font=f) > max_w:
        text = text[:-1]
    return text + "…"


def fit_font(draw, text, bold, start, minimum, max_w):
    for size in range(start, minimum - 1, -1):
        if draw.textlength(text, font=font(bold, size)) <= max_w:
            return font(bold, size)
    return font(bold, minimum)


def mix(a, b, t):
    return tuple(round(x + (y - x) * t) for x, y in zip(a, b))


def build_view(rows, data_age, error, at=None):
    """Everything that is shown at time `at` (unix seconds), as plain values."""
    at = at or time.time()
    now = datetime.fromtimestamp(at, timezone.utc)
    out = []
    for r in rows[:MAX_ROWS]:
        mins = [max(0, int((t - now).total_seconds() // 60)) for t in r["times"]]
        if mins:
            nxt = tr("now") if mins[0] == 0 else f"{mins[0]}'"
            col = SOON if mins[0] <= 2 else FG
        else:
            nxt, col = "–", DIM
        later = f"{mins[1]}'" if len(mins) > 1 else ""
        out.append(((r["line"], r["towards"]), nxt, col, later, r.get("type", "")))

    if error and not rows:
        status = (tr("offline"), RED)
    elif data_age > 90:
        status = (tr("stale", m=int(data_age // 60)), RED)
    else:
        status = ("live", DIM)
    return {"clock": datetime.fromtimestamp(at).strftime("%H:%M"), "rows": tuple(out), "status": status}


def render(view, flash=None, blink_on=True, refresh_in=None):
    """flash: {row_key: 0..1} red background intensity behind the next-departure number.
    blink_on=False hides every "now" and its green box (off phase of the blink).
    refresh_in: seconds until the next data refresh, shown in the status line."""
    flash = flash or {}
    img = Image.new("RGB", (SIZE, SIZE), BG)
    d = ImageDraw.Draw(img)
    cx = SIZE // 2

    title = CFG.title or short_dir(CFG.station) or CFG.station
    f_title = fit_font(d, title, True, 27, 18, 236)
    d.text((cx, 44), fit(d, title, f_title, 236), font=f_title, fill=FG, anchor="mm")
    d.text((cx, 70), view["clock"], font=F_CLOCK, fill=CFG.clock_color, anchor="mm")

    left, right = 34, SIZE - 34
    row_h = 46
    n = max(1, len(view["rows"]))
    y0 = round(171 - (n - 1) * row_h / 2)  # rows stay centred when fewer than 4 are shown
    now_word = tr("now")
    nx_right = right - d.textlength("88'", font=F_LATER) - 8  # fixed column for the small number
    for i, (key, nxt, col, later, typ) in enumerate(view["rows"]):
        y = y0 + i * row_h
        line, towards = key

        # line badge (font shrinks for long names like "59A" or "WLB")
        d.rounded_rectangle((left, y - 17, left + 48, y + 17), radius=7, fill=badge_color(line, typ))
        d.text((left + 24, y), line, font=fit_font(d, line, True, 24, 12, 42), fill=(255, 255, 255), anchor="mm")

        if later:
            d.text((right, y + 4), later, font=F_LATER, fill=DIM, anchor="rm")
        nx_w = d.textlength(nxt, font=F_NEXT)
        box = (nx_right - nx_w - 7, y - 20, nx_right + 5, y + 20)
        if nxt == now_word:
            if blink_on:  # blinking: green box + white text, then nothing
                d.rounded_rectangle(box, radius=7, fill=GREEN)
                d.text((nx_right, y), nxt, font=F_NEXT, fill=(255, 255, 255), anchor="rm")
        else:
            if flash.get(key):  # red notification background, fading out
                d.rounded_rectangle(box, radius=7, fill=mix(BG, RED, flash[key]))
            d.text((nx_right, y), nxt, font=F_NEXT, fill=col, anchor="rm")

        dir_x = left + 58
        max_w = nx_right - nx_w - 14 - dir_x
        d.text((dir_x, y), fit(d, short_dir(towards), F_DIR, max_w), font=F_DIR, fill=FG, anchor="lm")

    status, scol = view["status"]
    if refresh_in is not None and status == "live":
        status = tr("live", n=refresh_in)
    d.text((cx, 268), status.upper(), font=F_STATUS, fill=scol, anchor="mm")

    if CFG.footer:  # footer badge: text on a dark tinted pill with an outline
        fw = d.textlength(CFG.footer, font=F_CREDIT)
        d.rounded_rectangle((cx - fw / 2 - 10, 280, cx + fw / 2 + 10, 302), radius=11,
                            fill=mix(BG, CFG.footer_color, 0.25), outline=CFG.footer_color, width=1)
        d.text((cx, 291), CFG.footer, font=F_CREDIT, fill=CFG.footer_color, anchor="mm")

    if CFG.tilt:
        img = img.rotate(CFG.tilt, resample=Image.BICUBIC, center=(cx, cx), fillcolor=BG)
    return img


class Animator:
    """Builds the frames for one refresh cycle, remembering state across cycles.

    Each GIF runs a bit past the moment the next one is expected (see cmd_run), because the
    Kraken loops a GIF that runs out - which would replay the start of the cycle: old, higher
    countdowns and a second red blink. Since the next GIF usually replaces this one before
    its last frames are shown, the state for the next cycle is taken from the frame that was
    on screen when the new GIF appeared, not from the last frame drawn."""

    def __init__(self):
        self.prev = {}        # row key -> last shown next-departure text
        self.flash_age = {}   # row key -> frames since its number changed
        self.history = []     # (frame time, prev, flash_age) for each frame of the last GIF

    def build(self, rows, last_ok, error, start, refresh_at, next_at):
        """Frames from `start` until the next GIF is expected on screen (`next_at`, unix seconds)
        plus a safety margin. The status line counts down to `refresh_at`. Returns the frames and
        the rows whose number changes before `next_at` (for the log)."""
        end = next_at + OVERRUN_MARGIN_S
        for at, prev, age in reversed(self.history):
            if at <= start:
                self.prev, self.flash_age = dict(prev), dict(age)
                break
        self.history = []

        n = max(1, round((end - start) / FRAME_S))
        frames, flashed = [], set()
        for k in range(n):
            at = start + k * FRAME_S
            view = build_view(rows, at - last_ok if last_ok else 1e9, error, at)

            for key, nxt, *_ in view["rows"]:
                if key in self.prev and self.prev[key] != nxt:
                    self.flash_age[key] = 0
                    if at < next_at:  # later frames are usually replaced before they show
                        flashed.add(f"{key[0]} {key[1]}")
                self.prev[key] = nxt
            flash = {key: FADE_STEPS[a] for key, a in self.flash_age.items() if a < len(FADE_STEPS)}

            blink_on = (k // BLINK_FRAMES) % 2 == 0
            refresh_in = max(0, int(refresh_at - at + 0.999))
            frames.append(render(view, flash, blink_on, refresh_in))

            self.flash_age = {key: a + 1 for key, a in self.flash_age.items() if a + 1 < len(FADE_STEPS)}
            self.history.append((at, dict(self.prev), dict(self.flash_age)))
        return frames, flashed


# --- run ------------------------------------------------------------------------
# Creating this file asks a running display to exit cleanly between uploads (used by the
# desktop launcher and `tram_lcd.py stop`). Killing it mid-upload can leave the Kraken LCD
# waiting for the rest of a transfer for about a minute.
STOP_FILE = os.path.join(HERE, "stop.request")
GIF_OUT = os.path.join(tempfile.gettempdir(), "kraken_tram.gif")
PNG_OUT = os.path.join(tempfile.gettempdir(), "kraken_tram.png")


def push(dev, frames, gif_ok):
    dev.device.clear_enqueued_reports()
    if gif_ok:
        frames[0].save(GIF_OUT, save_all=True, append_images=frames[1:],
                       duration=int(FRAME_S * 1000), loop=0)
        dev.set_screen("lcd", "gif", GIF_OUT)
    else:
        frames[0].save(PNG_OUT)
        dev.set_screen("lcd", "static", PNG_OUT)


def cmd_run(_args):
    from liquidctl.error import NotSupportedByDriver

    kraken_cls = _patch_liquidctl()
    log(f"station: {CFG.station} (DIVA {CFG.diva}), refresh every {CFG.refresh_seconds}s")
    dev, rows, last_ok, error = None, [], 0.0, None
    anim, lead_s, gif_ok = Animator(), 2.0, True
    gap_s = 3.0  # cycle start -> new GIF on screen (fetch + render + upload), learned below
    held = {}    # departure times shown so far, see steady()
    if os.path.exists(STOP_FILE):
        os.remove(STOP_FILE)  # stale request from an earlier stop

    def wait_until(deadline):
        """Sleep until `deadline`; returns True if a stop was requested meanwhile."""
        while time.time() < deadline:
            if os.path.exists(STOP_FILE):
                return True
            time.sleep(0.2)
        return os.path.exists(STOP_FILE)

    while True:
        try:
            cycle_start = time.time()
            try:
                rows, last_ok, error = steady(fetch(), held), time.time(), None
            except Exception as e:  # network/API problem: keep old data
                error = e
                log("fetch failed:", e)

            if dev is None:
                dev = connect_kraken(kraken_cls)
            # frames are drawn for the moment the GIF will actually appear:
            # now + (render + upload time), learned from previous cycles
            t = time.time()
            show_at = t + lead_s
            refresh_at = cycle_start + CFG.refresh_seconds
            # keep drawing until the next GIF is on screen (+ margin), so this one never loops
            next_at = refresh_at + min(gap_s, MAX_OVERRUN_S)
            frames, flashed = anim.build(rows, last_ok, error, show_at, refresh_at, next_at)
            try:
                push(dev, frames, gif_ok)
            except NotSupportedByDriver:  # Kraken 2023 firmware 2.x: no GIFs, static only
                gif_ok = False
                log("GIFs not supported by this firmware - falling back to static images")
                push(dev, frames, gif_ok)
            took = time.time() - t
            lead_s = 0.6 * lead_s + 0.4 * took
            gap = time.time() - cycle_start
            gap_s = max(gap, 0.8 * gap_s + 0.2 * gap)  # rises at once on a slow cycle, decays slowly
            log(f"screen updated, {len(frames)} frames, render+upload {took:.2f}s"
                + (f", flash: {', '.join(sorted(flashed))}" if flashed else ""))
            if wait_until(cycle_start + CFG.refresh_seconds):
                break
        except KeyboardInterrupt:
            break
        except Exception:
            log("error:\n" + traceback.format_exc())
            if dev:
                close_kraken(dev)
            dev = None
            if wait_until(time.time() + 10):
                break

    log("stopping")
    if os.path.exists(STOP_FILE):
        os.remove(STOP_FILE)
    if dev:
        try:
            dev.set_screen("lcd", "liquid", None)  # back to liquid temperature
        except Exception:
            pass
        close_kraken(dev)


# --- setup / helper commands -------------------------------------------------------
def ask(prompt, default=""):
    shown = f" [{default}]" if default != "" else ""
    try:
        answer = input(f"{prompt}{shown}: ").replace("﻿", "").strip()  # BOM from piped input
    except EOFError:
        answer = ""
    return answer or str(default)


def pick_station(query=None):
    while True:
        query = query or ask("Station name (e.g. Karlsplatz, Schwedenplatz)")
        hits = search_stations(query)
        if not hits:
            print(f"  No station matches '{query}'. Try part of the name.")
            query = None
            continue
        for i, (diva, name) in enumerate(hits[:15], 1):
            print(f"  {i:>2}) {name}  (DIVA {diva})")
        if len(hits) > 15:
            print(f"  ... {len(hits) - 15} more - type a longer name to narrow it down")
        choice = ask("Number", 1)
        if choice.isdigit() and 1 <= int(choice) <= min(15, len(hits)):
            return hits[int(choice) - 1]
        query = choice  # treat anything else as a new search


def print_lines(lines):
    now = datetime.now(timezone.utc)
    for i, e in enumerate(lines, 1):
        nxt = f"{max(0, int((e['times'][0] - now).total_seconds() // 60))} min" if e["times"] else "-"
        print(f"  {i:>2}) {e['line']:<5} → {e['towards']:<28} next: {nxt}")


def cmd_setup(args):
    path = args.config
    old = None
    if os.path.exists(path):
        try:
            old = Config.load(path)
        except Exception:
            pass
    cfg = old or Config()

    print("\n=== Kraken Tram Display setup ===\n")
    diva, name = pick_station(args.station)
    print(f"\nLines departing at {name} right now:")
    lines = sorted(monitor_lines(diva), key=line_sort_key)
    if not lines:
        print("  (no live departures found - the station may be closed right now)")
    print_lines(lines)

    chosen = []
    while lines and not chosen:
        raw = ask(f"\nPick up to {MAX_ROWS} rows, comma separated (e.g. 1,3) - Enter = first {MAX_ROWS}")
        if not raw.strip():
            chosen = lines[:MAX_ROWS]
            break
        try:
            idx = [int(x) for x in raw.replace(" ", "").split(",") if x]
            chosen = [lines[i - 1] for i in idx if 1 <= i <= len(lines)][:MAX_ROWS]
        except ValueError:
            pass
        if not chosen:
            print("  Please enter numbers from the list above.")

    cfg.station, cfg.diva = name, diva
    cfg.departures = [{"line": e["line"], "towards": e["towards"]} for e in chosen]
    print("\nDisplay options (Enter keeps the value in brackets):")
    cfg.language = ask("Language de/en", cfg.language).lower()
    if cfg.language not in TEXT:
        cfg.language = "de"
    try:
        cfg.tilt = float(ask("Tilt in degrees, counter-clockwise (0 = straight)", f"{cfg.tilt:g}"))
    except ValueError:
        pass
    try:
        cfg.refresh_seconds = max(5, int(ask("Refresh every N seconds", cfg.refresh_seconds)))
    except ValueError:
        pass
    footer = ask("Footer text (type - for none)", cfg.footer or "-")
    cfg.footer = "" if footer == "-" else footer

    cfg.save(path)
    print(f"\nSaved {path}")

    global CFG
    CFG = cfg
    preview = os.path.join(HERE, "preview.png")
    try:
        render_preview(preview)
        print(f"Preview image: {preview}")
    except Exception as e:
        print(f"(could not render preview: {e})")
    print("\nStart the display with:  python tram_lcd.py   (quit NZXT CAM first)\n")


def render_preview(out):
    render(build_view(fetch(), 0, None), refresh_in=CFG.refresh_seconds).save(out)


def cmd_preview(args):
    render_preview(args.output)
    print(f"Saved {args.output}")


def cmd_search(args):
    hits = search_stations(" ".join(args.name))
    if not hits:
        print("No matching station.")
    for diva, name in hits[:30]:
        print(f"{diva}  {name}")


def cmd_lines(args):
    query = " ".join(args.station)
    if query.isdigit():
        diva, name = query, load_stations().get(query, query)
    else:
        hits = search_stations(query)
        if not hits:
            print("No matching station.")
            return 1
        diva, name = hits[0]
    print(f"{name} (DIVA {diva}):")
    print_lines(sorted(monitor_lines(diva), key=line_sort_key))


def cmd_stop(_args):
    open(STOP_FILE, "w").close()
    print("Stop requested - a running display exits within a few seconds.")


def main(argv=None):
    ap = argparse.ArgumentParser(description="Live Wiener Linien departures on an NZXT Kraken LCD.")
    ap.add_argument("--config", default=DEFAULT_CONFIG, help="config file (default: config.toml)")
    sub = ap.add_subparsers(dest="cmd")
    sub.add_parser("run", help="run the display (default)")
    sub.add_parser("stop", help="ask a running display to exit cleanly")
    p = sub.add_parser("setup", help="interactive setup")
    p.add_argument("station", nargs="?", help="station name to start the search with")
    p = sub.add_parser("search", help="find stations by name")
    p.add_argument("name", nargs="+")
    p = sub.add_parser("lines", help="list lines departing at a station")
    p.add_argument("station", nargs="+", help="station name or DIVA")
    p = sub.add_parser("preview", help="render one frame to a PNG")
    p.add_argument("-o", "--output", default=os.path.join(HERE, "preview.png"))
    args = ap.parse_args(argv)

    if args.cmd == "setup":
        return cmd_setup(args)
    if args.cmd == "search":
        return cmd_search(args)
    if args.cmd == "lines":
        return cmd_lines(args)
    if args.cmd == "stop":
        return cmd_stop(args)

    global CFG
    if not os.path.exists(args.config):
        print(f"No config found at {args.config}.\nRun:  python tram_lcd.py setup")
        return 1
    CFG = Config.load(args.config)
    if args.cmd == "preview":
        return cmd_preview(args)
    return cmd_run(args)


if __name__ == "__main__":
    sys.exit(main())
