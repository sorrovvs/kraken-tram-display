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

Another program is talking to the Kraken (usually CAM, or a second copy of this script). Make sure only one
copy is running:

```powershell
Get-Process python*, NZXT* | Select-Object ProcessName, Id
```

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
