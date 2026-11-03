"""
Kraken Tram Display - live Wiener Linien departures on an NZXT Kraken LCD.

Commands:
  python tram_lcd.py setup             interactive setup: pick station, lines, tilt, language
  python tram_lcd.py                   run the display (same as "run")
  python tram_lcd.py search NAME       find stations by name
  python tram_lcd.py lines STATION     list lines/directions departing at a station (name or DIVA)
  python tram_lcd.py preview [-o F]    render one frame to a PNG (no Kraken needed)
  python tram_lcd.py calibrate         measure how fast the Kraken plays GIF frames (stop the display first)

Options:
  --config PATH                        use another config file (default: config.toml next to this script)

NZXT CAM must NOT be running while the display runs (it locks the USB device).
"""

import argparse
import csv
import html
import io
import json
import math
import os
import re
import sys
import tempfile
import time
import tomllib
import traceback
import unicodedata
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from PIL import Image, ImageDraw, ImageFont

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_CONFIG = os.path.join(HERE, "config.toml")

MONITOR_API = "https://www.wienerlinien.at/ogd_realtime/monitor?diva={}"
STOPS_CSV = "https://www.wienerlinien.at/ogd_realtime/doku/ogd/wienerlinien-ogd-haltepunkte.csv"
USER_AGENT = "kraken-tram-display"
MAX_ROWS = 6  # more than 4 rows are drawn smaller


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
    now_text: str = ""                               # replaces "jetzt"/"now"; empty = language default
    tilt: float = 0.0
    brightness: int | None = None
    refresh_seconds: int = 10
    lcd_frame_ms: int = 250                          # how long the Kraken really shows a GIF frame (calibrate)
    footer: str = "© Sorrow"
    clock_color: tuple = (200, 170, 255)
    footer_color: tuple = (200, 170, 255)
    flash_color: tuple = (227, 0, 15)                # box behind a countdown that just changed
    flash_blink: bool = True                         # blink once before fading
    flash_fade_in: float = 0.0                       # seconds for the box to fade in (0 = at once)
    flash_fade_out: float = 3.0                      # seconds for the box to fade out
    badge_color: tuple | None = (255, 255, 255)      # one colour for all line badges; None = line colours
    soon_color: tuple = (255, 204, 0)                # countdowns up to soon_minutes, and the "now" dot
    soon_minutes: int = 2                            # countdowns up to this many minutes are shown in soon_color
    font: str = "arial"                              # see FONT_FILES
    now_style: str = "text"                          # "text" = green "jetzt"/"now" box, "dot" / "block" = yellow dot / square
    sign_font: str = ""                              # LED-style font file for title + directions (in fonts/)
    sign_color: tuple | None = None                  # colour for title + directions; None = white
    live_in_header: bool = True                      # "● LIVE ◔ 7s" next to the clock instead of the status line
    disruptions: bool = True                         # scroll the disruption text when a line can't run
    disruptions_all: bool = False                    # also minor ones (irregular intervals, delays)
    info_color: tuple = (255, 150, 90)               # colour of that text
    led_numbers: bool = True                         # minutes + line numbers as LED dot digits
    header: str = "columns"                          # "station" = name + clock, "columns" = clock + column labels
    badge_style: str = "outline"                      # "fill" = solid badge, "outline" = coloured frame, dark inside
    badge_text_color: tuple | None = (255, 255, 255) # line number colour; None = automatic (white/dark)

    @classmethod
    def load(cls, path):
        with open(path, "rb") as f:
            raw = tomllib.load(f)
        st, disp, col = raw.get("station", {}), raw.get("display", {}), raw.get("colors", {})
        c = cls(
            station=str(st.get("name", "")),
            diva=str(st.get("diva", "")),
            departures=[
                {"line": str(d["line"]), "towards": str(d.get("towards", "")),
                 "oebb": str(d.get("oebb_station", ""))}  # set = from ÖBB (S-Bahn) instead of Wiener Linien
                for d in raw.get("departures", []) if "line" in d
            ],
            title=str(disp.get("title", "")),
            language=str(disp.get("language", "de")).lower(),
            now_text=str(disp.get("now_text", "")),
            tilt=float(disp.get("tilt", 0)),
            refresh_seconds=max(5, int(disp.get("refresh_seconds", 10))),
            footer=str(disp.get("footer", "© Sorrow")),
        )
        b = disp.get("brightness", -1)
        c.brightness = int(b) if isinstance(b, int) and 0 <= b <= 100 else None
        c.clock_color = hex_rgb(col.get("clock"), c.clock_color)
        c.footer_color = hex_rgb(col.get("footer"), c.footer_color)
        c.flash_color = hex_rgb(col.get("flash"), c.flash_color)
        c.badge_color = None if col.get("badge") == "" else hex_rgb(col.get("badge"), c.badge_color)
        c.flash_blink = bool(disp.get("flash_blink", True))
        c.lcd_frame_ms = min(2000, max(50, int(disp.get("lcd_frame_ms", c.lcd_frame_ms))))
        c.flash_fade_in = max(0.0, float(disp.get("flash_fade_in", c.flash_fade_in)))
        c.flash_fade_out = max(FRAME_S, float(disp.get("flash_fade_out", c.flash_fade_out)))
        c.soon_color = hex_rgb(col.get("soon"), c.soon_color)
        c.soon_minutes = max(0, int(disp.get("soon_minutes", c.soon_minutes)))
        c.font = str(disp.get("font", c.font)).lower()
        c.now_style = str(disp.get("now_style", "")).lower()
        if c.now_style not in ("text", "dot", "block"):
            c.now_style = "text"
        c.sign_font = str(disp.get("sign_font", ""))
        c.sign_color = hex_rgb(col.get("sign"), None)
        c.live_in_header = bool(disp.get("live_in_header", c.live_in_header))
        c.led_numbers = bool(disp.get("led_numbers", c.led_numbers))
        dis = disp.get("disruptions", True)  # true, false or "all"
        c.disruptions_all = isinstance(dis, str) and dis.lower() == "all"
        c.disruptions = c.disruptions_all or (dis is True or str(dis).lower() == "true")
        c.info_color = hex_rgb(col.get("info"), c.info_color)
        c.header = "station" if str(disp.get("header", c.header)).lower() == "station" else "columns"
        c.badge_style = "fill" if str(disp.get("badge_style", c.badge_style)).lower() == "fill" else "outline"
        c.badge_text_color = (None if col.get("badge_text") == ""
                              else hex_rgb(col.get("badge_text"), c.badge_text_color))
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
            if d.get("oebb"):
                lines.append(f"oebb_station = {q(d['oebb'])}")
        lines += [
            "",
            "[display]",
            f"title = {q(self.title)}               # empty = station name",
            f"language = {q(self.language)}             # \"de\" or \"en\"",
            f"now_text = {q(self.now_text)}               # replaces \"jetzt\"/\"now\"; empty = language default",
            f"tilt = {self.tilt:g}                    # degrees counter-clockwise (negative = clockwise)",
            f"brightness = {self.brightness if self.brightness is not None else -1}"
            "             # 0-100, -1 = leave unchanged",
            f"refresh_seconds = {self.refresh_seconds}        # data refresh interval (min 5)",
            f"footer = {q(self.footer)}         # empty = no footer badge",
            f"lcd_frame_ms = {self.lcd_frame_ms}         # how long the Kraken really shows a frame: python tram_lcd.py calibrate",
            f"soon_minutes = {self.soon_minutes}            # countdowns up to this many minutes in the soon colour",
            f"flash_blink = {'true' if self.flash_blink else 'false'}        # blink once before the fade",
            f"flash_fade_in = {self.flash_fade_in:g}           # seconds for the box to fade in",
            f"flash_fade_out = {self.flash_fade_out:g}          # seconds for the box to fade out",
            f"font = {q(self.font)}            # \"segoe\" or \"arial\"",
            f"now_style = {q(self.now_style)}        # \"text\" = green box, \"dot\" / \"block\" = blinking yellow dot / square",
            f"sign_font = {q(self.sign_font)}            # LED-style font file (in fonts/) for title + directions",
            f"live_in_header = {'true' if self.live_in_header else 'false'}     # \"● LIVE ◔ 7s\" next to the clock",
            f"disruptions = {q('all') if self.disruptions_all else 'true' if self.disruptions else 'false'}"
            "       # scroll the disruption text when a line can't run; \"all\" = also delays/irregular intervals",
            f"led_numbers = {'true' if self.led_numbers else 'false'}       # minutes + line numbers as LED dot digits",
            f"header = {q(self.header)}          # \"station\" or \"columns\" (clock + Linie / Abfahrt in Minuten)",
            f"badge_style = {q(self.badge_style)}       # \"fill\" or \"outline\"",
            "",
            "[colors]",
            f"clock = {q('#%02X%02X%02X' % self.clock_color)}",
            f"footer = {q('#%02X%02X%02X' % self.footer_color)}",
            f"flash = {q('#%02X%02X%02X' % self.flash_color)}          # box behind a changed countdown",
            f"badge = {q('#%02X%02X%02X' % self.badge_color) if self.badge_color else q('')}"
            "               # all line badges in one colour; empty = line colours",
            f"soon = {q('#%02X%02X%02X' % self.soon_color)}           # countdowns up to soon_minutes, \"now\" dot",
            f"sign = {q('#%02X%02X%02X' % self.sign_color) if self.sign_color else q('')}"
            "                # title + directions; empty = white",
            f"badge_text = {q('#%02X%02X%02X' % self.badge_text_color) if self.badge_text_color else q('')}"
            "          # line numbers; empty = automatic",
            f"info = {q('#%02X%02X%02X' % self.info_color)}           # disruption text",
            "",
        ]
        with open(path, "w", encoding="utf-8", newline="\n") as f:
            f.write("\n".join(lines))


CFG = Config()

TEXT = {
    "de": {"now": "jetzt", "live": "live · neu in {n} s", "refresh": "neu in {n} s", "offline": "keine Verbindung",
           "stale": "keine Live-Daten seit {m} min", "line": "Linie", "departs": "Abfahrt in Minuten",
           "until": ": Dauer wsl. bis {t}"},
    "en": {"now": "now", "live": "live · refresh in {n} s", "refresh": "refresh in {n} s", "offline": "no connection",
           "stale": "no live data for {m} min", "line": "Line", "departs": "Departs in min",
           "until": ": expected until {t}"},
}


def tr(key, **kw):
    if key == "now" and CFG.now_text:
        return CFG.now_text
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
    return CFG.badge_color or LINE_COLORS.get(line) or TYPE_COLORS.get(typ, RED)


def text_on(bg):
    """White or near-black text, whichever reads better on `bg` (pastel colours need dark text)."""
    r, g, b = bg
    return (25, 20, 35) if 0.299 * r + 0.587 * g + 0.114 * b > 150 else (255, 255, 255)


FONT_DIRS = [os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts"),
             "/usr/share/fonts/truetype/dejavu", "/Library/Fonts"]
_font_cache = {}


# [display] font = ...: (regular files, bold files), first one found wins.
# "arial" is the closest Windows font to the Helvetica-style lettering on Wiener Linien displays.
FONT_FILES = {
    "segoe": (["segoeui.ttf", "DejaVuSans.ttf", "Arial.ttf"],
              ["segoeuib.ttf", "DejaVuSans-Bold.ttf", "Arial Bold.ttf"]),
    "arial": (["arial.ttf", "Arial.ttf", "LiberationSans-Regular.ttf", "DejaVuSans.ttf"],
              ["arialbd.ttf", "Arial Bold.ttf", "LiberationSans-Bold.ttf", "DejaVuSans-Bold.ttf"]),
}


def font(bold, size):
    return font_from(FONT_FILES.get(CFG.font, FONT_FILES["segoe"])[bool(bold)], size)


def font_from(names, size):
    """First of the font files `names` found in FONT_DIRS, at `size`."""
    key = (tuple(names), size)
    if key not in _font_cache:
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



# animation: one GIF per refresh cycle, every frame drawn for its own moment in time
FRAME_S = 0.25                             # GIF frame length
BLINK_FRAMES = 2                           # "now" on/off every 2 frames = 0.5 s
def lcd_frame_s():
    """Seconds the Kraken really shows each GIF frame. It plays slower than the FRAME_S written into the
    GIF, so frames are drawn this far apart in time - otherwise everything on screen falls behind the
    clock and the next upload makes the countdown jump (3s -> 9s). Measured by `calibrate`."""
    return CFG.lcd_frame_ms / 1000


LIVE_PULSE_S = 2.0                         # the red LIVE dot fades out and back in once per 2 s
OVERRUN_MARGIN_S = 2.0                     # extra frames past the expected next GIF
MAX_OVERRUN_S = 8.0                        # cap for the learned fetch+upload gap


def flash_steps():
    """Box intensity (0..1) for each frame after a countdown changes: fade in, optional blink, fade out."""
    n_in = round(CFG.flash_fade_in / lcd_frame_s())
    n_out = max(1, round(CFG.flash_fade_out / lcd_frame_s()))
    fade_in = tuple(i / n_in for i in range(1, n_in))  # the fade-out starts at full
    blink = (1.0, 0.0) if CFG.flash_blink else ()
    return fade_in + blink + tuple(1.0 - i / n_out for i in range(n_out))


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
    """All (line, direction) at a station, merged across platforms: dicts with line/towards/type/times.
    The feed's line-level `towards` is only the *next* vehicle's destination, so a single short
    run (e.g. "Winckelmannstraße, Betriebsbhf. Rudolfsheim" instead of "Westbahnhof S U") renamed
    the whole direction and the configured row vanished. Group by the direction code instead and
    label it with the destination most of the upcoming vehicles have."""
    data = http_json(MONITOR_API.format(urllib.parse.quote(diva)))
    merged = {}
    for mon in data.get("data", {}).get("monitors", []):
        for line in mon.get("lines", []):
            name, line_towards = line.get("name", ""), line.get("towards", "").strip()
            key = (name, line.get("direction") or line_towards)
            entry = merged.setdefault(key, {"line": name, "towards": line_towards,
                                            "type": line.get("type", ""), "times": [], "dests": {}})
            for dep in line.get("departures", {}).get("departure", []):
                t = dep.get("departureTime", {})
                iso = t.get("timeReal") or t.get("timePlanned")
                if iso:
                    entry["times"].append(datetime.strptime(iso, "%Y-%m-%dT%H:%M:%S.%f%z"))
                dest = ((dep.get("vehicle") or {}).get("towards") or line_towards).strip()
                if dest:
                    entry["dests"][dest] = entry["dests"].get(dest, 0) + 1
    for e in merged.values():
        e["times"].sort()
        dests = e.pop("dests")
        if dests:  # most common destination; ties go to the earliest-seen one
            e["towards"] = max(dests, key=dests.get)
    return list(merged.values())


def line_sort_key(line):
    """Natural order: U-Bahn first, then numbers (2 < 10 < 13A), then letters."""
    name = line["line"]
    num = "".join(ch for ch in name if ch.isdigit())
    return (0 if name.startswith("U") else 1, int(num) if num else 999, name, line["towards"])


OEBB_BOARD = ("https://fahrplan.oebb.at/bin/stboard.exe/dn?L=vs_scotty.vs_liveticker&evaId={}&boardType=dep"
              "&productsFilter=1011111111011&start=yes&eqstops=false&showJourneys=60&additionalTime=0"
              "&outputMode=tickerDataOnly")
_oebb_cache = {}  # station -> last good result, used when ÖBB doesn't answer


def oebb_lines(station):
    """Departures from ÖBB's live board (S-Bahn isn't in the Wiener Linien data), same shape as
    monitor_lines(). ÖBB gives minutes only; delays are already in the time."""
    req = urllib.request.Request(OEBB_BOARD.format(urllib.parse.quote(station)),
                                 headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=OEBB_TIMEOUT_S) as r:
        text = r.read().decode("utf-8", "replace")
    data = json.loads(text[text.index("{"):text.rindex("}") + 1])  # "journeysObj = {...};"
    merged = {}
    for j in data.get("journey", []):
        rt = j.get("rt") or {}
        if rt.get("status"):  # e.g. "Ausfall" = cancelled
            continue
        name = j.get("pr", "").replace(" ", "")
        towards = html.unescape(j.get("st", "")).removeprefix("Wien ").strip()
        when = datetime.strptime(f"{rt.get('dld') or j['da']} {rt.get('dlt') or j['ti']}", "%d.%m.%Y %H:%M")
        entry = merged.setdefault((name, towards), {"line": name, "towards": towards,
                                                    "type": "ptTrainS" if name.startswith("S") else "",
                                                    "times": []})
        entry["times"].append(when.astimezone())  # local (Vienna) time -> aware
    for e in merged.values():
        e["times"].sort()
    return list(merged.values())


def select_rows(lines, oebb=None):
    """Apply the [[departures]] filter and order from the config. oebb: {station: oebb_lines()}."""
    if not CFG.departures:
        return sorted(lines, key=line_sort_key)[:MAX_ROWS]
    def pool(want):
        src = (oebb or {}).get(want["oebb"], []) if want.get("oebb") else lines
        return [e for e in sorted(src, key=line_sort_key) if e["line"].casefold() == want["line"].casefold()]

    def exact(want, e):
        return not want["towards"] or norm(e["towards"]) == norm(want["towards"])

    # Wiener Linien rows whose destination isn't in the feed right now (e.g. late evening, when
    # only depot runs are coming) take the line's direction that no other configured row named
    claimed = [id(e) for w in CFG.departures for e in pool(w) if w["towards"] and exact(w, e)]
    rows = []
    for want in CFG.departures:
        found = [e for e in pool(want) if exact(want, e)]
        if not found and want["towards"] and not want.get("oebb"):
            found = [e for e in pool(want) if id(e) not in claimed][:1]
            claimed += [id(e) for e in found]
        rows += [e for e in found if e not in rows]
    return rows[:MAX_ROWS]


TRAFFIC_API = "https://www.wienerlinien.at/ogd_realtime/trafficInfoList?name=stoerunglang&name=stoerungkurz{}"
TRAFFIC_EVERY_S = 60  # disruptions change slowly; don't ask every refresh
# A disruption is shown only when the line can't run (or only part of its route). Reports that
# just mention irregular intervals or delays - typically after the problem is gone - are not.
CANT_RUN = ("unterbrochen", "kein betrieb", "eingestellt", "fährt nicht", "fahren nicht", "können nicht",
            "kann nicht", "betrieb nur zwischen", "kurzgeführt", "kurzführung", "umgeleitet", "umleitung",
            "gesperrt", "nicht befahren", "ersatzverkehr", "ersatzbus")
_traffic = {"at": 0.0, "lines": {}}  # line -> disruption text


def _api_time(s):
    try:
        return datetime.strptime(s, "%Y-%m-%dT%H:%M:%S.%f%z") if s else None
    except ValueError:
        return None


def disruption_reason(info):
    """Short cause of a disruption, e.g. "Falschparker" (from "42: Falschparker")."""
    reason = (info.get("reasonName") or "").strip()
    if reason and reason != "undefined":
        return reason
    title = " ".join((info.get("title") or "").split())
    title = re.sub(r"^[^:]{1,30}:\s*", "", title)  # drop the "42: " / "25, 26, 27: " line prefix
    return title or " ".join((info.get("description") or "").split())


def disruption_text(reason, until, now):
    """"Falschparker: Dauer wsl. bis 11:00" (a date instead of the time when it ends on another day)."""
    if not until:
        return reason
    local = until.astimezone()
    when = local.strftime("%H:%M") if local.date() == now.astimezone().date() else local.strftime("%d.%m.")
    return reason + tr("until", t=when)


def disruptions(lines):
    """{line: text} for lines that currently can't run, from Wiener Linien's disruption feed
    (lift and escalator outages are a different feed and never included). The text is the cause
    and, when known, the expected end. "stoerunglang" holds the disruption itself; its expected
    end is often only in the matching short reports ("stoerungkurz")."""
    if time.time() - _traffic["at"] < TRAFFIC_EVERY_S:
        return _traffic["lines"]
    url = TRAFFIC_API.format("".join(f"&relatedLine={urllib.parse.quote(l)}" for l in sorted(lines)))
    data = http_json(url).get("data", {})
    cat = {c.get("id"): c.get("name") for c in data.get("trafficInfoCategories", [])}
    now = datetime.now(timezone.utc)
    current, short = [], []
    for info in data.get("trafficInfos", []):
        t = info.get("time", {})
        start, end = _api_time(t.get("start")), _api_time(t.get("end"))
        if (start and start > now) or (end and end < now):
            continue
        kind = cat.get(info.get("refTrafficInfoCategoryId"), "stoerunglang")
        (short if kind == "stoerungkurz" else current).append((info, start, end))
    found = {}
    for info, start, end in current:
        text = " ".join((info.get("description") or info.get("title") or "").split())
        if not CFG.disruptions_all and not any(w in text.casefold() for w in CANT_RUN):
            continue
        related = set(info.get("relatedLines") or [])
        if not end:  # expected end from short reports for the same line, issued since it started
            ends = [e for i, s, e in short if e and related & set(i.get("relatedLines") or [])
                    and (not start or not s or s >= start - timedelta(minutes=10))]
            end = max(ends, default=None)
        shown = disruption_text(disruption_reason(info), end, now)
        for line in related & set(lines):
            if shown not in found.setdefault(line, []):
                found[line].append(shown)
    found = {line: "  +++  ".join(texts) for line, texts in found.items()}
    _traffic.update(at=time.time(), lines=found)
    return found


OEBB_TIMEOUT_S = 4  # ÖBB sometimes hangs; don't let it hold up the whole refresh
_pool = ThreadPoolExecutor(max_workers=4)


def fetch():
    # ÖBB in the background while Wiener Linien is fetched
    jobs = {s: _pool.submit(oebb_lines, s) for s in {w["oebb"] for w in CFG.departures if w.get("oebb")}}
    lines = monitor_lines(CFG.diva)
    oebb = {}
    for station, job in jobs.items():
        try:
            oebb[station] = _oebb_cache[station] = job.result(timeout=OEBB_TIMEOUT_S)
        except Exception as e:  # keep the Wiener Linien rows going; reuse the last ÖBB data
            log("ÖBB fetch failed:", repr(e))
            oebb[station] = _oebb_cache.get(station, [])
    rows = [{**r, "times": r["times"][:3]} for r in select_rows(lines, oebb)]
    if CFG.disruptions:
        wl_lines = {r["line"] for r in rows if not r["line"].startswith("S")}
        try:
            info = disruptions(wl_lines)
        except Exception as e:  # never let the disruption feed break the departures
            log("disruption feed failed:", repr(e))
            info = _traffic["lines"]
        rows = [{**r, "info": info.get(r["line"], "")} for r in rows]
    return rows


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


NOW_WINDOW_S = 20  # "now" shows this long before the tram really leaves (about its time at the stop)
OFFSETS_FILE = os.path.join(HERE, "departure_offsets.json")


class DepartureLearner:
    """Learns, per row, how far real departures are from the live estimate.

    Wiener Linien drops a vehicle from the data once it has left, so when the first departure
    disappears (and the second one moves up), we know to within one refresh when it really left.
    The average difference is kept in departure_offsets.json, so it survives restarts."""

    ALPHA = 0.25                    # weight of a new sample in the running average
    SAMPLE_RANGE = (-120.0, 90.0)   # outside: a cancellation or data glitch, not a departure

    def __init__(self, path=OFFSETS_FILE):
        self.path = path
        try:
            with open(path, encoding="utf-8") as f:
                self.offsets = {k: float(v) for k, v in json.load(f).items()}
        except (OSError, ValueError, AttributeError):
            self.offsets = {}
        self.last = {}  # row key -> (fetch time, first two estimated departures)

    @staticmethod
    def name(key):
        return f"{key[0]} {key[1]}"

    def offset(self, key):
        """Seconds to add to the estimate to get the real departure (negative = leaves early)."""
        return self.offsets.get(self.name(key), 0.0)

    def update(self, rows, fetched_at):
        """rows: the raw fetch() result, before steady()."""
        for r in rows:
            key, times = (r["line"], r["towards"]), r["times"][:2]
            prev = self.last.get(key)
            if prev and times and len(prev[1]) > 1:
                prev_at, (p_first, p_second) = prev
                if times[0] - p_first > timedelta(seconds=90) and abs(times[0] - p_second) < timedelta(seconds=90):
                    left_at = (prev_at + fetched_at) / 2  # it left between the two fetches
                    sample = left_at - p_first.timestamp()
                    if self.SAMPLE_RANGE[0] <= sample <= self.SAMPLE_RANGE[1]:
                        n = self.name(key)
                        old = self.offsets.get(n)
                        self.offsets[n] = sample if old is None else (1 - self.ALPHA) * old + self.ALPHA * sample
                        log(f"{n} left {abs(sample):.0f}s {'before' if sample < 0 else 'after'} the estimate, "
                            f"average now {self.offsets[n]:+.0f}s")
                        self.save()
            if times:
                self.last[key] = (fetched_at, times)

    def save(self):
        try:
            with open(self.path, "w", encoding="utf-8") as f:
                json.dump({k: round(v, 1) for k, v in self.offsets.items()}, f, indent=2)
        except OSError:
            pass


LEARNER = None  # set by cmd_run; preview and tests run without one


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


# --- sign font: optional LED-style font for the title and the directions ---------------
# Display fonts like Block Stock only have capitals and often no umlauts, so text is
# upper-cased and an umlaut the font lacks becomes its plain letter (Ü -> U).
SIGN_UMLAUTS = {"Ä": "A", "Ö": "O", "Ü": "U"}
SIGN_MIN_SIZE = 13  # below this the LED font gets unreadable; longer names are cut instead
_sign_cache = {}
_glyph_cache = {}


def _has_glyph(f, ch):
    key = (id(f), ch)
    if key not in _glyph_cache:
        notdef = f.getmask("￿")
        m = f.getmask(ch)
        _glyph_cache[key] = (m.size, bytes(m)) != (notdef.size, bytes(notdef))
    return _glyph_cache[key]


def sign_font(size):
    """The [display] sign_font at `size`, or None when none is set / it can't be loaded."""
    if not CFG.sign_font:
        return None
    if size not in _sign_cache:
        _sign_cache[size] = None
        for path in (CFG.sign_font, os.path.join(HERE, "fonts", CFG.sign_font), os.path.join(HERE, CFG.sign_font)):
            try:
                _sign_cache[size] = ImageFont.truetype(path, size)
                break
            except OSError:
                continue
    return _sign_cache[size]


def sign_text(text, f):
    """Upper-cased; umlauts the font lacks become plain letters, other missing characters
    (like the minute sign ') are left out."""
    out = []
    for c in text.upper():
        if c in SIGN_UMLAUTS and not _has_glyph(f, c):
            c = SIGN_UMLAUTS[c]
        if c == " " or _has_glyph(f, c):
            out.append(c)
    return "".join(out)


def fit_sign(draw, text, f, max_w):
    """sign_text() cut to `max_w` (LED displays just cut; these fonts have no ellipsis)."""
    text = sign_text(text, f)
    while text and draw.textlength(text, font=f) > max_w:
        text = text[:-1]
    return text.rstrip()


def fit_sign_font(draw, text, start, minimum, max_w):
    for size in range(start, minimum - 1, -1):
        f = sign_font(size)
        if draw.textlength(sign_text(text, f), font=f) <= max_w:
            return f
    return sign_font(minimum)


# --- LED numbers: tall, narrow digits on a dot grid, like the tram stop displays ----------
# 5 x 9 cells per character, drawn as small squares. Letters cover what line names use
# (13A, N25, U6, S45, D, O, WLB); anything else is rasterised from Arial Bold.
LED_GLYPHS = {
    "0": ".###.|#...#|#...#|#...#|#...#|#...#|#...#|#...#|.###.",
    "1": "...#.|..##.|.#.#.|#..#.|...#.|...#.|...#.|...#.|...#.",
    "2": ".###.|#...#|....#|....#|...#.|..#..|.#...|#....|#####",
    "3": ".###.|#...#|....#|....#|..##.|....#|....#|#...#|.###.",
    "4": "...#.|..##.|.#.#.|#..#.|#..#.|#####|...#.|...#.|...#.",
    "5": "#####|#....|#....|####.|....#|....#|....#|#...#|.###.",
    "6": "..##.|.#...|#....|#....|####.|#...#|#...#|#...#|.###.",
    "7": "#####|....#|....#|...#.|...#.|..#..|..#..|.#...|.#...",
    "8": ".###.|#...#|#...#|#...#|.###.|#...#|#...#|#...#|.###.",
    "9": ".###.|#...#|#...#|#...#|.####|....#|....#|...#.|.##..",
    "A": ".###.|#...#|#...#|#...#|#####|#...#|#...#|#...#|#...#",
    "B": "####.|#...#|#...#|#...#|####.|#...#|#...#|#...#|####.",
    "D": "####.|#...#|#...#|#...#|#...#|#...#|#...#|#...#|####.",
    "E": "#####|#....|#....|#....|####.|#....|#....|#....|#####",
    "L": "#....|#....|#....|#....|#....|#....|#....|#....|#####",
    "N": "#...#|##..#|##..#|#.#.#|#.#.#|#..##|#..##|#...#|#...#",
    "O": ".###.|#...#|#...#|#...#|#...#|#...#|#...#|#...#|.###.",
    "S": ".###.|#...#|#....|#....|.###.|....#|....#|#...#|.###.",
    "U": "#...#|#...#|#...#|#...#|#...#|#...#|#...#|#...#|.###.",
    "W": "#...#|#...#|#...#|#...#|#.#.#|#.#.#|#.#.#|##.##|#...#",
}
LED_W, LED_H = 5, 9
_led_cache = {}


def led_glyph(ch):
    """Rows of a character as strings of '#' and '.'."""
    if ch not in _led_cache:
        if ch in LED_GLYPHS:
            _led_cache[ch] = LED_GLYPHS[ch].split("|")
        else:  # rasterise from a bold font onto the grid
            im = Image.new("L", (LED_W * 8, LED_H * 8), 0)
            ImageDraw.Draw(im).text((LED_W * 4, LED_H * 4), ch, fill=255, anchor="mm",
                                    font=font_from(FONT_FILES["arial"][1], LED_H * 8))
            im = im.resize((LED_W, LED_H), Image.BOX)
            _led_cache[ch] = ["".join("#" if im.getpixel((x, y)) > 100 else "." for x in range(LED_W))
                              for y in range(LED_H)]
    return _led_cache[ch]


def led_chars(text):
    return [c for c in text.upper() if c not in "'’"]  # these displays show minutes without the ' sign


def led_width(text, pitch):
    cols = sum((3 if c == " " else LED_W) + 1 for c in led_chars(text)) - 1
    return max(0, cols) * pitch


def led_cell(pitch):
    return pitch - 1 if pitch >= 3 else pitch  # visible gaps between dots from 3 px on


def draw_led_h(img, xy, text, height, fill, anchor="rm"):
    """LED text at any height (pixels): drawn large on a solid grid, then scaled down smoothly."""
    if abs(height / LED_H - round(height / LED_H)) < 1e-6:
        draw_led(ImageDraw.Draw(img), xy, text, round(height / LED_H), fill, anchor)
        return
    big = 4
    w, h = led_width(text, big), LED_H * big
    if not w:
        return
    mask = Image.new("L", (w, h), 0)
    draw_led(ImageDraw.Draw(mask), (w, h / 2), text, big, 255, cell=big)
    mask = mask.resize((max(1, round(w * height / h)), max(1, round(height))), Image.LANCZOS)
    x, y = xy
    x -= mask.width if anchor == "rm" else mask.width / 2 if anchor == "mm" else 0
    img.paste(Image.new("RGB", mask.size, fill), (round(x), round(y - mask.height / 2)), mask)


def led_width_h(text, height):
    return led_width(text, 1) * height / LED_H


def draw_led(draw, xy, text, pitch, fill, anchor="rm", cell=None):
    """LED text vertically centred on xy[1]; anchor "rm", "mm" or "lm"."""
    x, y = xy
    w = led_width(text, pitch)
    x -= w if anchor == "rm" else w / 2 if anchor == "mm" else 0
    x, top, cell = round(x), round(y - LED_H * pitch / 2), cell or led_cell(pitch)
    for c in led_chars(text):
        if c == " ":
            x += 4 * pitch
            continue
        for r, row in enumerate(led_glyph(c)):
            for k, on in enumerate(row):
                if on == "#":
                    px, py = x + k * pitch, top + r * pitch
                    draw.rectangle((px, py, px + cell - 1, py + cell - 1), fill=fill)
        x += (LED_W + 1) * pitch


SCROLL_PX_S = 16     # ticker speed for names that don't fit: slow enough to read
SCROLL_PAUSE_S = 2.0  # rest at the start and at the end of each pass


def scroll_offset(overflow, at):
    """Pixels to shift a too-long name at time `at`: pause, scroll to the end, pause, jump back.
    Based on the clock, so it carries on smoothly from one GIF to the next."""
    run = overflow / SCROLL_PX_S
    t = at % (SCROLL_PAUSE_S + run + SCROLL_PAUSE_S)
    return 0 if t < SCROLL_PAUSE_S else min(overflow, (t - SCROLL_PAUSE_S) * SCROLL_PX_S)


INFO_DIR_S = 6.0  # with a disruption: show the direction this long, then scroll the disruption text
INFO_PX_S = 28    # disruption text speed (Arial reads well at this pace)


def disruption_offset(info, max_w, layout, at):
    """None while the direction is shown, else how far the disruption text has scrolled in.
    The text enters from the right and leaves completely on the left, then the direction returns.
    Based on the clock, so it carries on smoothly from one GIF to the next."""
    w = font_from(FONT_FILES["arial"][0], layout["f_info"]).getlength(info)
    period = INFO_DIR_S + (w + max_w) / INFO_PX_S
    phase = at % period
    return None if phase < INFO_DIR_S else (phase - INFO_DIR_S) * INFO_PX_S


def draw_marquee(img, xy, text, f, fill, max_w, offset):
    """`text` moving right to left through a slot of max_w at xy (left, vertical centre)."""
    x, y = round(xy[0]), round(xy[1])
    h = round(f.size * 1.4)
    strip = Image.new("RGB", (max(1, round(max_w)), h), BG)
    ImageDraw.Draw(strip).text((max_w - offset, h / 2), text, font=f, fill=fill, anchor="lm")
    img.paste(strip, (x, y - h // 2))


def draw_sign_ticker(img, xy, text, f, fill, max_w, at):
    """Sign text at xy (left, vertical centre); scrolls horizontally when wider than max_w."""
    draw = ImageDraw.Draw(img)
    text = sign_text(text, f)
    w = draw.textlength(text, font=f)
    if w <= max_w:
        draw_sign(draw, xy, text, f, fill)
        return
    x, y = round(xy[0]), round(xy[1])
    h = round(-f.getbbox("H", anchor="ls")[1]) + 8
    strip = Image.new("RGB", (max(1, round(max_w)), h), BG)  # clip to the space the name may use
    draw_sign(ImageDraw.Draw(strip), (-scroll_offset(w - max_w, at), h / 2), text, f, fill)
    img.paste(strip, (x, y - h // 2))


def draw_sign(draw, xy, text, f, fill, anchor="lm"):
    """Draw sign text (already fitted) vertically centred on its capitals; anchor "lm" or "mm"."""
    x, y = xy
    if anchor == "mm":
        x -= draw.textlength(text, font=f) / 2
    elif anchor == "rm":
        x -= draw.textlength(text, font=f)
    top = f.getbbox("H", anchor="ls")[1]  # negative: cap height above the baseline
    draw.text((x, y - top / 2), text, font=f, fill=fill, anchor="ls")


def mix(a, b, t):
    return tuple(round(x + (y - x) * t) for x, y in zip(a, b))


def build_view(rows, data_age, error, at=None):
    """Everything that is shown at time `at` (unix seconds), as plain values."""
    at = at or time.time()
    now = datetime.fromtimestamp(at, timezone.utc)
    out = []
    for r in rows[:MAX_ROWS]:
        key = (r["line"], r["towards"])
        mins = [max(0, int((t - now).total_seconds() // 60)) for t in r["times"]]
        if mins:
            # "now" only while the tram is expected at the stop; until then at least 1'
            leaves_in = (r["times"][0] - now).total_seconds() + (LEARNER.offset(key) if LEARNER else 0.0)
            nxt = tr("now") if leaves_in <= NOW_WINDOW_S else f"{max(1, mins[0])}'"
            col = CFG.soon_color if mins[0] <= CFG.soon_minutes else FG
        else:
            nxt, col = "–", DIM
        later = f"{mins[1]}'" if len(mins) > 1 else ""
        out.append((key, nxt, col, later, r.get("type", ""), r.get("info", "")))

    if error and not rows:
        status = (tr("offline"), RED)
    elif data_age > 90:
        status = (tr("stale", m=int(data_age // 60)), RED)
    else:
        status = ("live", DIM)
    return {"clock": datetime.fromtimestamp(at).strftime("%H:%M"), "rows": tuple(out), "status": status,
            "at": at}


# Row layouts: up to 4 rows at full size, 5-6 rows smaller so everything fits on the round screen.
LAYOUTS = {
    False: dict(title_y=44, title_size=27, clock_y=70, mid=171, row_h=46, badge_w=48, badge_h=17,
                f_dir=20, f_next=32, f_later=18, box_h=20, dot_r=9, dir_pad=10, gap=14, led_next=4, led_later=2, f_info=17),
    True: dict(title_y=40, title_size=24, clock_y=63, mid=169.5, row_h=31, badge_w=44, badge_h=13,
               f_dir=17, f_next=25, f_later=15, box_h=14, dot_r=7, dir_pad=7, gap=12, led_next=3, led_later=2, f_info=14),
}


def render(view, flash=None, blink_on=True, refresh_in=None, live_pulse=1.0):
    """flash: {row_key: 0..1} intensity of the box behind a next-departure number that just changed.
    blink_on=False hides every "now" (off phase of the blink).
    refresh_in: seconds until the next data refresh, shown in the status line.
    live_pulse: 0..1 brightness of the red LIVE dot (it slowly blinks)."""
    flash = flash or {}
    img = Image.new("RGB", (SIZE, SIZE), BG)
    d = ImageDraw.Draw(img)
    cx = SIZE // 2
    n = max(1, len(view["rows"]))
    L = LAYOUTS[n > 4]
    f_dir, f_next, f_later = font(False, L["f_dir"]), font(True, L["f_next"]), font(False, L["f_later"])

    sign_col = CFG.sign_color or FG
    title = CFG.title or short_dir(CFG.station) or CFG.station
    columns = CFG.header == "columns"  # clock at the top + "Linie" / "Abfahrt in Minuten" labels
    if not columns:
        if sign_font(20):
            f_title = fit_sign_font(d, title, L["title_size"], 14, 236)
            draw_sign(d, (cx, L["title_y"]), fit_sign(d, title, f_title, 236), f_title, sign_col, anchor="mm")
        else:
            f_title = fit_font(d, title, True, L["title_size"], 18, 236)
            d.text((cx, L["title_y"]), fit(d, title, f_title, 236), font=f_title, fill=sign_col, anchor="mm")

    f_clock = font(True, 22 if columns else 19)
    clock_y = L["title_y"] if columns else L["clock_y"]
    live = view["status"][0] == "live"
    if CFG.live_in_header and live:  # "16:30  ● LIVE  ◔ 7s"
        f_live = font(True, 14)
        r, rr = 4, 6  # live dot, refresh ring radius
        secs = f"{max(1, math.ceil(refresh_in))}s" if refresh_in is not None else ""
        w_clock, w_live = d.textlength(view["clock"], font=f_clock), d.textlength("LIVE", font=f_live)
        w_timer = (2 * rr + 4 + d.textlength(secs, font=f_live)) if secs else 0
        x = cx - (w_clock + 12 + 2 * r + 5 + w_live + (12 + w_timer if secs else 0)) / 2
        cy = clock_y
        d.text((x, cy), view["clock"], font=f_clock, fill=CFG.clock_color, anchor="lm")
        x += w_clock + 12
        d.ellipse((x, cy - r, x + 2 * r, cy + r), fill=mix(BG, RED, 0.15 + 0.85 * live_pulse))
        d.text((x + 2 * r + 5, cy), "LIVE", font=f_live, fill=CFG.clock_color, anchor="lm")
        if secs:  # loading ring: fills up until the next refresh
            x += 2 * r + 5 + w_live + 12
            ring = (x, cy - rr, x + 2 * rr, cy + rr)
            d.ellipse(ring, outline=mix(BG, CFG.clock_color, 0.35), width=2)
            done = 1 - min(1.0, refresh_in / CFG.refresh_seconds)
            if done > 0:
                d.arc(ring, -90, -90 + 360 * done, fill=CFG.clock_color, width=2)
            d.text((x + 2 * rr + 4, cy), secs, font=f_live, fill=CFG.clock_color, anchor="lm")
    else:
        d.text((cx, clock_y), view["clock"], font=f_clock, fill=CFG.clock_color, anchor="mm")

    left, right = 34, SIZE - 34
    if columns:  # column labels, like on the stop displays
        f_label = font_from(FONT_FILES["arial"][0], 12)
        ly = L["clock_y"] + 3
        d.text((left + L["badge_w"] / 2, ly), tr("line"), font=f_label, fill=DIM, anchor="mm")
        d.text((right, ly), tr("departs"), font=f_label, fill=DIM, anchor="rm")
    row_h, bw, bh = L["row_h"], L["badge_w"], L["badge_h"]
    y0 = round(L["mid"] - (n - 1) * row_h / 2)  # rows stay centred when fewer are shown
    now_word = tr("now")
    # minutes in the sign font too, when one is set (without the ' sign if the font lacks it)
    s_next, s_later = sign_font(L["f_next"] - 4), sign_font(L["f_later"] - 3)
    f_next, f_later = s_next or f_next, s_later or f_later

    def mins_text(s, f):
        return sign_text(s, f) if CFG.sign_font and f in (s_next, s_later) else s

    def draw_mins(xy, s, f, fill):
        if CFG.sign_font and f in (s_next, s_later):
            draw_sign(d, xy, s, f, fill, anchor="rm")
        else:
            d.text(xy, s, font=f, fill=fill, anchor="rm")

    led = CFG.led_numbers  # minutes and line numbers as LED dot digits

    def cap(f):  # height of the capitals
        return -f.getbbox("H", anchor="ls")[1]

    # minutes as tall as the direction text (a bit taller for the next departure)
    base = sign_font(L["f_dir"]) or f_dir
    h_next, h_later = cap(base) * 1.15, cap(base) * 0.85
    # one size for every line number: the largest at which the longest fits its badge
    widest = max((led_width(k[0], 1) for k, *_ in view["rows"]), default=1) or 1
    p_badge = max(1, min((2 * bh - 6) // LED_H, int((bw - 6) // widest)))

    def num_w(s, which):
        if led:
            return led_width_h(s, h_next if which == "next" else h_later)
        f = f_next if which == "next" else f_later
        return d.textlength(mins_text(s, f), font=f)

    def num_draw(xy, s, which, fill):
        if led:
            draw_led_h(img, xy, s, h_next if which == "next" else h_later, fill)
        else:
            f = f_next if which == "next" else f_later
            draw_mins((xy[0], xy[1] + (3 if which == "later" and not s_later else 0)), mins_text(s, f), f, fill)

    nx_right = right - num_w("88'", "later") - 8  # column for the small number
    f_sign = None
    if sign_font(L["f_dir"]):  # one size for all directions: the largest at which the longest fits
        room = nx_right - num_w("88'", "next") - L["gap"] - (left + bw + L["dir_pad"])
        longest = max((short_dir(k[1]) for k, *_ in view["rows"]),
                      key=lambda s: d.textlength(sign_text(s, sign_font(L["f_dir"])), font=sign_font(L["f_dir"])),
                      default="")
        f_sign = fit_sign_font(d, longest, L["f_dir"], SIGN_MIN_SIZE, room)
        h_next, h_later = cap(f_sign) * 1.15, cap(f_sign) * 0.85  # match the size actually used
    for i, (key, nxt, col, later, typ, info) in enumerate(view["rows"]):
        y = y0 + i * row_h
        line, towards = key

        # line badge (font shrinks for long names like "59A" or "S45")
        bcol = badge_color(line, typ)
        if CFG.badge_style == "outline":  # coloured frame, dark inside
            d.rounded_rectangle((left, y - bh, left + bw, y + bh), radius=7, fill=BG, outline=bcol, width=2)
            tcol = CFG.badge_text_color or bcol
        else:
            d.rounded_rectangle((left, y - bh, left + bw, y + bh), radius=7, fill=bcol)
            tcol = CFG.badge_text_color or text_on(bcol)
        if led:
            draw_led(d, (left + bw / 2, y), line, p_badge, tcol, anchor="mm")
        elif sign_font(12):
            f_badge = fit_sign_font(d, line, round(bh * 1.9), 9, bw - 8)
            draw_sign(d, (left + bw / 2, y), sign_text(line, f_badge), f_badge, tcol, anchor="mm")
        else:
            d.text((left + bw / 2, y), line, font=fit_font(d, line, True, round(bh * 1.4), 10, bw - 6),
                   fill=tcol, anchor="mm")

        if later:
            num_draw((right, y), later, "later", DIM)
        if nxt == now_word and CFG.now_style in ("dot", "block"):
            r = L["dot_r"]  # blinking dot / the classic square of the Wiener Linien displays: it's at the stop
            nx_w = 2 * r
            if blink_on:
                shape = d.ellipse if CFG.now_style == "dot" else d.rectangle
                shape((nx_right - 2 * r, y - r, nx_right, y + r), fill=CFG.soon_color)
        elif nxt == now_word:
            nx_w = d.textlength(nxt, font=f_next)
            if blink_on:  # blinking: green box + white text, then nothing
                d.rounded_rectangle((nx_right - nx_w - 7, y - L["box_h"], nx_right + 5, y + L["box_h"]),
                                    radius=7, fill=GREEN)
                d.text((nx_right, y), nxt, font=f_next, fill=(255, 255, 255), anchor="rm")
        else:
            nx_w = num_w(nxt, "next")
            f = flash.get(key, 0)
            if f:  # notification background, fading out; the number fades with it from dark to its colour
                d.rounded_rectangle((nx_right - nx_w - 7, y - L["box_h"], nx_right + 5, y + L["box_h"]),
                                    radius=7, fill=mix(BG, CFG.flash_color, f))
                if text_on(CFG.flash_color) != (255, 255, 255):  # light box: start with dark text
                    col = mix(col, text_on(CFG.flash_color), f)
            num_draw((nx_right, y), nxt, "next", col)

        dir_x = left + bw + L["dir_pad"]
        max_w = nx_right - nx_w - L["gap"] - dir_x
        # timing from a fixed slot width, so both directions of a line scroll in step and a
        # countdown going from 9 to 10 doesn't make the text jump
        slot = nx_right - num_w("88'", "next") - L["gap"] - (left + bw + L["dir_pad"])
        offset = disruption_offset(info, slot, L, view.get("at", 0)) if info else None
        if offset is not None:  # the line can't run: its disruption text scrolls through
            draw_marquee(img, (dir_x, y), info, font_from(FONT_FILES["arial"][0], L["f_info"]),
                         CFG.info_color, max_w, offset)
        elif f_sign:
            draw_sign_ticker(img, (dir_x, y), short_dir(towards), f_sign, sign_col, max_w, view.get("at", 0))
        else:
            d.text((dir_x, y), fit(d, short_dir(towards), f_dir, max_w), font=f_dir, fill=sign_col, anchor="lm")

    status, scol = view["status"]
    if status == "live" and CFG.live_in_header:
        status = ""  # live + refresh timer are in the header
    elif refresh_in is not None and status == "live":
        status = tr("live", n=max(1, math.ceil(refresh_in)))
    d.text((cx, 268), status.upper(), font=font(True, 16), fill=scol, anchor="mm")

    if CFG.footer:  # footer badge: text on a dark tinted pill with an outline
        f_credit = font(True, 14)
        fw = d.textlength(CFG.footer, font=f_credit)
        d.rounded_rectangle((cx - fw / 2 - 10, 280, cx + fw / 2 + 10, 302), radius=11,
                            fill=mix(BG, CFG.footer_color, 0.25), outline=CFG.footer_color, width=1)
        d.text((cx, 291), CFG.footer, font=f_credit, fill=CFG.footer_color, anchor="mm")

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

    def build(self, rows, last_ok, error, start, swap_at, next_at):
        """Frames from `start` until the next GIF is expected on screen at the latest (`next_at`, unix
        seconds) plus a safety margin. The refresh countdown runs to `swap_at`, when the next GIF
        usually appears. Returns the frames and
        the rows whose number changes before `next_at` (for the log)."""
        end = next_at + OVERRUN_MARGIN_S
        for at, prev, age in reversed(self.history):
            if at <= start:
                self.prev, self.flash_age = dict(prev), dict(age)
                break
        self.history = []

        step = lcd_frame_s()
        blink_frames = max(1, round(BLINK_FRAMES * FRAME_S / step))  # "now" blink stays ~0.5 s per phase
        n = max(1, round((end - start) / step))
        frames, flashed = [], set()
        for k in range(n):
            at = start + k * step
            view = build_view(rows, at - last_ok if last_ok else 1e9, error, at)

            for key, nxt, *_ in view["rows"]:
                if key in self.prev and self.prev[key] != nxt:
                    self.flash_age[key] = 0
                    if at < next_at:  # later frames are usually replaced before they show
                        flashed.add(f"{key[0]} {key[1]}")
                self.prev[key] = nxt
            steps = flash_steps()
            flash = {key: steps[a] for key, a in self.flash_age.items() if a < len(steps)}

            blink_on = (k // blink_frames) % 2 == 0
            # seconds until the next GIF is on screen, fractional (drives the refresh ring): the old GIF
            # reaches 0 when the new one appears, and the new one starts at refresh_seconds (1s -> 10s).
            # Frames past that are only shown if the upload is late; they start the next round
            refresh_in = swap_at - at
            if refresh_in <= 0:
                refresh_in = max(0.0, refresh_in + CFG.refresh_seconds)
            refresh_in = min(refresh_in, float(CFG.refresh_seconds))  # timing estimates are a bit off: never 11s
            # absolute time, so the blink carries on smoothly from one GIF to the next
            live_pulse = 0.5 + 0.5 * math.cos(2 * math.pi * (at % LIVE_PULSE_S) / LIVE_PULSE_S)
            frames.append(render(view, flash, blink_on, refresh_in, live_pulse))

            self.flash_age = {key: a + 1 for key, a in self.flash_age.items() if a + 1 < len(steps)}
            self.history.append((at, dict(self.prev), dict(self.flash_age)))
        return frames, flashed


# --- run ------------------------------------------------------------------------
# Creating this file asks a running display to exit cleanly between uploads (used by the
# desktop launcher and `tram_lcd.py stop`). Killing it mid-upload can leave the Kraken LCD
# waiting for the rest of a transfer for about a minute.
STOP_FILE = os.path.join(HERE, "stop.request")
GIF_OUT = os.path.join(tempfile.gettempdir(), "kraken_tram.gif")
PNG_OUT = os.path.join(tempfile.gettempdir(), "kraken_tram.png")


def shared_palette(frames):
    """All frames on one palette, without dithering. With a palette per frame (Pillow's default),
    the rotated, anti-aliased edges came out slightly different in every frame, so the GIF couldn't
    store just the changes: ~600 KB per upload, and the Kraken went blank for a moment while
    switching to it. On one palette, unchanged pixels stay identical: a fraction of the size."""
    step = max(1, len(frames) // 8)
    sample = frames[::step][:8]
    mosaic = Image.new("RGB", (SIZE, SIZE * len(sample) + 8), BG)
    for i, f in enumerate(sample):
        mosaic.paste(f, (0, SIZE * i))
    # colours that may not be in the sampled frames: the change box fading, "now" marks
    swatch = [mix(BG, CFG.flash_color, i / 8) for i in range(1, 9)] + [CFG.soon_color, GREEN, RED, FG, DIM]
    swatch += [mix(BG, RED, 0.15 + 0.85 * i / 6) for i in range(6)]  # the LIVE dot pulsing
    d = ImageDraw.Draw(mosaic)
    for i, c in enumerate(swatch):
        d.rectangle((i * 8, SIZE * len(sample), i * 8 + 7, SIZE * len(sample) + 7), fill=c)
    pal = mosaic.quantize(colors=128, method=Image.Quantize.MEDIANCUT, dither=Image.Dither.NONE)
    return [f.quantize(palette=pal, dither=Image.Dither.NONE) for f in frames]


def push(dev, frames, gif_ok):
    dev.device.clear_enqueued_reports()
    if gif_ok:
        frames = shared_palette(frames)
        # optimize=False: otherwise Pillow marks unchanged pixels with a "transparent" palette index.
        # The Kraken ignores GIF transparency and paints that index's real colour - often one of the
        # yellows - so parts of the screen flashed yellow for a frame. disposal=1: draw over the last frame.
        frames[0].save(GIF_OUT, save_all=True, append_images=frames[1:],
                       duration=int(FRAME_S * 1000), loop=0, optimize=False, disposal=1)
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
    swap_s = 1.5  # the same, typical value (gap_s is the cautious one), for the refresh countdown
    held = {}    # departure times shown so far, see steady()
    global LEARNER
    LEARNER = DepartureLearner()
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
                raw = fetch()
                LEARNER.update(raw, time.time())
                rows, last_ok, error = steady(raw, held), time.time(), None
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
            swap_at = refresh_at + swap_s  # countdown hits 0 when the next GIF usually appears
            frames, flashed = anim.build(rows, last_ok, error, show_at, swap_at, next_at)
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
            swap_s = 0.7 * swap_s + 0.3 * min(gap, MAX_OVERRUN_S)
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


CALIBRATE_FRAMES, CALIBRATE_MARK = 200, 100


def cmd_calibrate(args):
    """Shows a frame counter on the Kraken; the user presses Enter when it reaches CALIBRATE_MARK.
    Real time / frames = how long the Kraken really shows a frame -> lcd_frame_ms in the config."""
    import re

    dev = connect_kraken(_patch_liquidctl())
    try:
        frames = []
        f_big, f_small = font(True, 110), font(False, 18)
        for k in range(CALIBRATE_FRAMES):
            img = Image.new("RGB", (SIZE, SIZE), BG)
            d = ImageDraw.Draw(img)
            d.text((SIZE // 2, SIZE // 2), str(k), font=f_big, fill=CFG.soon_color if k >= CALIBRATE_MARK else FG,
                   anchor="mm")
            d.text((SIZE // 2, SIZE // 2 + 85), f"Enter at {CALIBRATE_MARK}", font=f_small, fill=DIM, anchor="mm")
            frames.append(img)
        push(dev, frames, True)
        start = time.time()
        input(f"The Kraken now counts frames. Press Enter the moment it shows {CALIBRATE_MARK}... ")
        took = time.time() - start
    finally:
        try:
            dev.set_screen("lcd", "liquid", None)
        except Exception:
            pass
        close_kraken(dev)

    ms = round(took / CALIBRATE_MARK * 1000)
    print(f"{CALIBRATE_MARK} frames took {took:.1f} s: the Kraken shows each frame for about {ms} ms "
          f"(requested: {int(FRAME_S * 1000)} ms).")
    if not 50 <= ms <= 2000:
        print("That doesn't look right - config not changed. Try again.")
        return 1
    with open(args.config, encoding="utf-8") as f:
        text = f.read()
    line = f"lcd_frame_ms = {ms}         # how long the Kraken really shows a frame: python tram_lcd.py calibrate"
    if re.search(r"(?m)^lcd_frame_ms\s*=.*$", text):
        text = re.sub(r"(?m)^lcd_frame_ms\s*=.*$", lambda _: line, text)
    elif re.search(r"(?m)^\[display\]\s*$", text):
        text = re.sub(r"(?m)^\[display\]\s*$", lambda m: m.group(0) + "\n" + line, text, count=1)
    else:
        text += f"\n[display]\n{line}\n"
    with open(args.config, "w", encoding="utf-8") as f:
        f.write(text)
    print(f"Saved lcd_frame_ms = {ms} in {args.config}. Restart the display.")


def cmd_stop(_args):
    open(STOP_FILE, "w").close()
    print("Stop requested - a running display exits within a few seconds.")


def main(argv=None):
    ap = argparse.ArgumentParser(description="Live Wiener Linien departures on an NZXT Kraken LCD.")
    ap.add_argument("--config", default=DEFAULT_CONFIG, help="config file (default: config.toml)")
    sub = ap.add_subparsers(dest="cmd")
    sub.add_parser("run", help="run the display (default)")
    sub.add_parser("stop", help="ask a running display to exit cleanly")
    sub.add_parser("calibrate", help="measure how fast the Kraken plays GIF frames (stop the display first)")
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
    if args.cmd == "calibrate":
        return cmd_calibrate(args)
    return cmd_run(args)


if __name__ == "__main__":
    sys.exit(main())
