# Troubleshooting

## "Kraken LCD interface not accessible - is NZXT CAM running?"

NZXT CAM holds the USB device. Quit CAM completely (tray icon → **Exit**, not just closing the window),
then start the script again. The `CAMService` Windows service can keep running. It doesn't block the screen.

## The screen stays on the liquid temperature / nothing changes

Run the script with `python.exe` instead of `pythonw.exe` so you can see the log. Every refresh should print
a line like:

```
12:22:45 screen updated, 30 frames, render+upload 0.49s
```

## `OSError: read error` or `missing messages`

Another program is talking to the Kraken: usually CAM, an RGB tool like **SignalRGB**, or a second copy of
this script. Make sure only one copy is running:

```powershell
Get-Process python*, NZXT*, SignalRgb* | Select-Object ProcessName, Id
```

The script tolerates some traffic from RGB tools. If the errors keep coming, turn off the Kraken in that
tool's device list.

## The display stopped working after it was force-closed

If the display is killed in the middle of an upload, the Kraken LCD can stay busy for about a minute. Stop
it with `python tram_lcd.py stop`, or with the desktop launcher, which both wait for the current upload to finish.

## Text is cut off at the edge

The LCD is a 320 px circle. Long direction names are shortened with "…" automatically. If you change the
`tilt` or the code, check that everything stays inside the circle:

```powershell
.venv\Scripts\python.exe tram_lcd.py preview -o preview.png
```

## "No config found" / wrong station

Run `python tram_lcd.py setup`. To find a station's DIVA number by hand, use `python tram_lcd.py search <name>`.
To see which lines and directions you can choose, use `python tram_lcd.py lines <name>`.

## A row is missing

The `towards` text in `config.toml` must match what Wiener Linien reports. Check it with
`python tram_lcd.py lines <station>`, or remove `towards` to show every direction of that line.
Some lines also don't run at night, so they won't show up then.

## The countdown runs too fast or too slow

The Kraken may not play GIF frames at exactly the requested speed. Adjust `FRAME_S` (and with it the blink
speed) in `tram_lcd.py`.

---

## Background: liquidctl workarounds

On Windows with liquidctl 1.16.0 / WinUsbCDC 1.7, LCD uploads to a Kraken Z3 (firmware 5.11) do not work out
of the box. `tram_lcd.py` patches three things at runtime. Nothing is installed or changed system-wide.

1. **The LCD bulk interface is never found.** `WinUsbPy.list_usb_devices()` only returns devices when a
   vid/pid or name filter is passed, but liquidctl calls it without one. The script injects NZXT's
   vendor id and the product ids of every Kraken model liquidctl's LCD driver supports.
2. **`initialize()` fails.** The Z3 does not answer the lighting-info query (`0x20 0x03`). The script uses
   `light_init()`, which is liquidctl's initialize sequence without that query.
3. **"Failed to setup bucket for data transfer".** liquidctl's `_write_then_read()` treats the next HID
   message as the reply. The Kraken also sends periodic status reports, so it often reads the wrong
   message. The patched version waits for the matching reply id (`command id + 1`, same sub-command).

If a future liquidctl version fixes these, the patches can be removed.

## The screen flickers / a countdown goes back up for a moment

Fixed in the version after 1.3.0. There were two causes:

- **The GIF ran out before the next one arrived.** Each refresh uploads one GIF, and the Kraken loops a GIF
  when it reaches the end. The frames used to stop at the next refresh, but the next GIF only appears after
  the next fetch + render + upload (about 1 s, more on a slow network). Meanwhile the Kraken replayed the
  start of the cycle: the red blink again, and the countdown from 10 s earlier. Now each GIF runs past the
  moment the next one is expected (the time is learned while running, plus 2 s margin).
- **The live data wobbles.** Wiener Linien's real-time estimates move back and forth by up to about ±30 s
  between polls, so a tram ~5 min away showed 4' → 5' → 4'. A countdown now goes up only once the new time
  is at least 30 s into the higher minute (`HYSTERESIS_S` in `tram_lcd.py`). Real delays and the next
  vehicle after a departure still show right away.

To check the first one in `display.log`: each GIF lasts `frames × 0.25 s`, which should be longer than the
time until the next "screen updated" line.
