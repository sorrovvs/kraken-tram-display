"""
Live Wiener Linien departures (lines 42 + 9, Vinzenzgasse) on an NZXT Kraken Z3 LCD.

Fetches realtime data, draws a 320x320 image with Pillow and uploads it to the
Kraken via liquidctl. NZXT CAM must NOT be running (it locks the USB device).

Run:  .venv\\Scripts\\pythonw.exe tram_lcd.py      (no console window)
      .venv\\Scripts\\python.exe  tram_lcd.py      (with log output)
"""

import json
import os
import sys
import tempfile
import time
import traceback
import urllib.request
from datetime import datetime, timezone

from PIL import Image, ImageDraw, ImageFont

# --- config -----------------------------------------------------------------
DIVA = "60201426"          # Vinzenzgasse
LINES = ["42", "9"]        # display order
FETCH_EVERY = 10          # seconds between API calls
BRIGHTNESS = None          # e.g. 60 to force LCD brightness, None = leave as is
TILT_FIX = 28          # degrees; rotates the picture counter-clockwise to cancel a
                           # clockwise-tilted pump. Use a negative value for the other way.
API = f"https://www.wienerlinien.at/ogd_realtime/monitor?diva={DIVA}"

SIZE = 320
BG = (0, 0, 0)
FG = (242, 242, 242)
DIM = (140, 140, 140)
RED = (227, 0, 15)
SOON = (255, 204, 0)

FONT_DIR = os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts")


def font(bold, size):
    name = "segoeuib.ttf" if bold else "segoeui.ttf"
    try:
        return ImageFont.truetype(os.path.join(FONT_DIR, name), size)
    except OSError:
        return ImageFont.load_default(size)


F_STOP = font(True, 27)
F_CLOCK = font(True, 19)
F_BADGE = font(True, 24)
F_DIR = font(False, 20)
F_NEXT = font(True, 32)
F_LATER = font(False, 18)
F_STATUS = font(True, 16)
F_CREDIT = font(True, 14)


# --- liquidctl / winusbcdc workaround ---------------------------------------
# winusbcdc 1.7 only returns devices when a vid/pid filter is passed, but
# liquidctl calls it without one, so the LCD bulk interface is never found on
# Windows. Inject the Kraken Z3 vid/pid.
from winusbcdc import WinUsbPy  # noqa: E402

_orig_list = WinUsbPy.list_usb_devices.__func__
WinUsbPy.list_usb_devices = classmethod(
    lambda cls, **kw: _orig_list(cls, **{"vid": 0x1E71, "pid": 0x3008, **kw})
)

from liquidctl import find_liquidctl_devices  # noqa: E402
from liquidctl.driver.kraken3 import KrakenZ3  # noqa: E402


# liquidctl reads "the next HID message" as the reply to an LCD command, but the
# Kraken also sends periodic status reports, so it often reads the wrong one and
# the bucket setup fails. Wait for the reply that matches the command instead
# (reply id = command id + 1, same sub-command).
def _write_then_read_matching(self, data):
    self._write(data)
    want = bytes([data[0] + 1, data[1]])
    msg = None
    for _ in range(40):
        msg = self._read()
        if bytes(msg[0:2]) == want:
            return msg
    raise RuntimeError(f"no reply {want.hex()} to command {bytes(data[:2]).hex()}")


KrakenZ3._write_then_read = _write_then_read_matching


def log(*a):
    print(datetime.now().strftime("%H:%M:%S"), *a, flush=True)


def light_init(dev):
    """liquidctl's initialize() minus the lighting query, which the Z3 never answers here."""
    dev.device.clear_enqueued_reports()
    interval = 1 + round((0.5 - 0.5) / 0.25)
    dev._write([0x70, 0x02, 0x01, 0xB8, interval])
    dev._write([0x70, 0x01])
    dev._status = []
    dev._get_fw_version(clear_reports=False)
    dev._write([0x30, 0x01])  # lcd info (brightness/orientation)
    dev._read_until({b"\x31\x01": dev.parse_lcd_info})


def close_kraken(dev):
    try:
        if dev.bulk_device:
            dev.bulk_device.close_winusb_device()
    except Exception:
        pass
    try:
        dev.disconnect()
    except Exception:
        pass


def connect_kraken():
    for dev in find_liquidctl_devices():
        if "Kraken Z" in dev.description:
            dev.connect()
            if dev.bulk_device is None:
                close_kraken(dev)
                raise RuntimeError("Kraken LCD interface not accessible - is NZXT CAM running?")
            light_init(dev)
            if BRIGHTNESS is not None:
                dev.set_screen("lcd", "brightness", BRIGHTNESS)
            log("connected:", dev.description)
            return dev
    raise RuntimeError("No Kraken Z found")


# --- data -------------------------------------------------------------------
def fetch():
    req = urllib.request.Request(API, headers={"User-Agent": "kraken-tram-lcd"})
    with urllib.request.urlopen(req, timeout=10) as r:
        data = json.load(r)
    rows = []
    for mon in data.get("data", {}).get("monitors", []):
        for line in mon.get("lines", []):
            if line.get("name") not in LINES:
                continue
            times = []
            for dep in line.get("departures", {}).get("departure", []):
                t = dep.get("departureTime", {})
                iso = t.get("timeReal") or t.get("timePlanned")
                if iso:
                    times.append(datetime.strptime(iso, "%Y-%m-%dT%H:%M:%S.%f%z"))
            rows.append({"line": line["name"], "towards": line.get("towards", ""), "times": times[:3]})
    rows.sort(key=lambda r: (LINES.index(r["line"]), r["towards"]))
    return rows


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


# --- render -----------------------------------------------------------------
# The screen gets one GIF per refresh cycle; every frame is drawn for its own
# moment in time, so countdowns, fades and blinking all run on the Kraken itself.
FRAME_S = 0.25                             # GIF frame length
# red background after a number changes: one blink (on, off), then fade out over 3 s
FADE_SECONDS = 3.0
_fade_n = round(FADE_SECONDS / FRAME_S)
FADE_STEPS = (1.0, 0.0) + tuple(1.0 - i / _fade_n for i in range(_fade_n))
CREDIT_COL = (200, 170, 255)               # pastel violet
CLOCK_COL = CREDIT_COL
BLINK_FRAMES = 2                           # "jetzt" on/off every 2 frames = 0.5 s
GREEN = (0, 150, 60)


def mix(a, b, t):
    return tuple(round(x + (y - x) * t) for x, y in zip(a, b))


def build_view(rows, data_age, error, at=None):
    """Everything that is shown at time `at` (unix seconds), as plain values."""
    at = at or time.time()
    now = datetime.fromtimestamp(at, timezone.utc)
    out = []
    for r in rows[:4]:
        mins = [max(0, int((t - now).total_seconds() // 60)) for t in r["times"]]
        if mins:
            nxt = "jetzt" if mins[0] == 0 else f"{mins[0]}'"
            col = SOON if mins[0] <= 2 else FG
        else:
            nxt, col = "–", DIM
        later = f"{mins[1]}'" if len(mins) > 1 else ""
        out.append(((r["line"], r["towards"]), nxt, col, later))

    if error and not rows:
        status = ("keine Verbindung", RED)
    elif data_age > 90:
        status = (f"keine Live-Daten seit {int(data_age // 60)} min", RED)
    else:
        status = ("live", DIM)
    return {"clock": datetime.fromtimestamp(at).strftime("%H:%M"), "rows": tuple(out), "status": status}


def render(view, flash=None, blink_on=True, refresh_in=None):
    """flash: {row_key: 0..1} red background intensity behind the next-departure number.
    blink_on=False hides every "jetzt" and its green box (off phase of the blink).
    refresh_in: seconds until the next data refresh, shown in the status line."""
    flash = flash or {}
    img = Image.new("RGB", (SIZE, SIZE), BG)
    d = ImageDraw.Draw(img)
    cx = SIZE // 2

    d.text((cx, 44), "Vinzenzgasse", font=F_STOP, fill=FG, anchor="mm")
    d.text((cx, 70), view["clock"], font=F_CLOCK, fill=CLOCK_COL, anchor="mm")

    left, right = 34, SIZE - 34
    row_h = 46
    y0 = 102
    for i, (key, nxt, col, later) in enumerate(view["rows"]):
        y = y0 + i * row_h
        line, towards = key

        # line badge
        d.rounded_rectangle((left, y - 17, left + 44, y + 17), radius=7, fill=RED)
        d.text((left + 22, y), line, font=F_BADGE, fill=(255, 255, 255), anchor="mm")

        # minutes (right aligned): next big, following one small & dim
        # fixed-width column for the small number, so the big numbers line up in every row
        if later:
            d.text((right, y + 4), later, font=F_LATER, fill=DIM, anchor="rm")
        nx_right = right - d.textlength("88'", font=F_LATER) - 8
        nx_w = d.textlength(nxt, font=F_NEXT)

        box = (nx_right - nx_w - 7, y - 20, nx_right + 5, y + 20)
        if nxt == "jetzt":
            # blinking: green box + white text, then nothing
            if blink_on:
                d.rounded_rectangle(box, radius=7, fill=GREEN)
                d.text((nx_right, y), nxt, font=F_NEXT, fill=(255, 255, 255), anchor="rm")
        else:
            # red notification background, fading out over several frames
            if flash.get(key):
                d.rounded_rectangle(box, radius=7, fill=mix(BG, RED, flash[key]))
            d.text((nx_right, y), nxt, font=F_NEXT, fill=col, anchor="rm")

        # direction, truncated to fit
        dir_x = left + 54
        max_w = nx_right - nx_w - 14 - dir_x  # leave room for the red/green box padding
        d.text((dir_x, y), fit(d, short_dir(towards), F_DIR, max_w), font=F_DIR, fill=FG, anchor="lm")

    status, scol = view["status"]
    if refresh_in is not None and status == "live":
        status = f"live · neu in {refresh_in} s"
    d.text((cx, 268), status.upper(), font=F_STATUS, fill=scol, anchor="mm")
    # credit: pastel violet text on a dark violet pill with a violet outline
    credit = "© Sorrow"
    cw = d.textlength(credit, font=F_CREDIT)
    d.rounded_rectangle((cx - cw / 2 - 10, 280, cx + cw / 2 + 10, 302), radius=11,
                        fill=(45, 30, 70), outline=CREDIT_COL, width=1)
    d.text((cx, 291), credit, font=F_CREDIT, fill=CREDIT_COL, anchor="mm")
    if TILT_FIX:
        img = img.rotate(TILT_FIX, resample=Image.BICUBIC, center=(cx, cx), fillcolor=BG)
    return img


# --- main loop --------------------------------------------------------------
PNG_OUT = os.path.join(tempfile.gettempdir(), "kraken_tram.png")
GIF_OUT = os.path.join(tempfile.gettempdir(), "kraken_tram.gif")


class Animator:
    """Builds the GIF for one refresh cycle, remembering state across cycles."""

    def __init__(self):
        self.prev = {}        # row key -> last shown next-departure text
        self.flash_age = {}   # row key -> frames since its number changed

    def build(self, rows, last_ok, error, start, cycle_s):
        n = max(1, round(cycle_s / FRAME_S))
        frames, flashed = [], set()
        for k in range(n):
            at = start + k * FRAME_S
            view = build_view(rows, at - last_ok if last_ok else 1e9, error, at)

            for key, nxt, _, _ in view["rows"]:
                if key in self.prev and self.prev[key] != nxt:
                    self.flash_age[key] = 0
                    flashed.add(f"{key[0]} {key[1]}")
                self.prev[key] = nxt
            flash = {key: FADE_STEPS[a] for key, a in self.flash_age.items() if a < len(FADE_STEPS)}

            blink_on = (k // BLINK_FRAMES) % 2 == 0
            refresh_in = max(0, int(cycle_s - k * FRAME_S + 0.999))
            frames.append(render(view, flash, blink_on, refresh_in))

            self.flash_age = {key: a + 1 for key, a in self.flash_age.items() if a + 1 < len(FADE_STEPS)}
        return frames, flashed


def push_gif(dev, frames):
    frames[0].save(GIF_OUT, save_all=True, append_images=frames[1:],
                   duration=int(FRAME_S * 1000), loop=0)
    dev.device.clear_enqueued_reports()
    dev.set_screen("lcd", "gif", GIF_OUT)


def main():
    dev = None
    rows, last_ok = [], 0.0
    error = None
    anim = Animator()
    lead_s = 2.0  # expected render+upload time

    while True:
        try:
            cycle_start = time.time()
            try:
                rows, last_ok, error = fetch(), time.time(), None
            except Exception as e:  # network/API problem: keep old data
                error = e
                log("fetch failed:", e)

            if dev is None:
                dev = connect_kraken()
            # frames are drawn for the moment the GIF will actually appear:
            # now + (render + upload time), learned from previous cycles
            t = time.time()
            show_at = t + lead_s
            cycle_s = cycle_start + FETCH_EVERY - show_at
            frames, flashed = anim.build(rows, last_ok, error, show_at, cycle_s)
            push_gif(dev, frames)
            took = time.time() - t
            lead_s = 0.6 * lead_s + 0.4 * took
            log(f"screen updated, {len(frames)} frames, render+upload {took:.2f}s"
                + (f", flash: {', '.join(sorted(flashed))}" if flashed else ""))
            time.sleep(max(0, cycle_start + FETCH_EVERY - time.time()))
        except KeyboardInterrupt:
            break
        except Exception:
            log("error:\n" + traceback.format_exc())
            if dev:
                close_kraken(dev)
            dev = None
            time.sleep(10)

    if dev:
        try:
            dev.set_screen("lcd", "liquid", None)  # back to liquid temperature
        except Exception:
            pass
        close_kraken(dev)


if __name__ == "__main__":
    sys.exit(main())
