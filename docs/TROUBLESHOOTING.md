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

The LCD is a 320 px circle. If you change font sizes or `TILT_FIX`, check that everything stays inside it:

```powershell
.venv\Scripts\python.exe -c "import tram_lcd as t; t.render(t.build_view(t.fetch(), 0, None), refresh_in=8).save('preview.png')"
```

## The countdown runs too fast or too slow

The Kraken may not play GIF frames at exactly the requested speed. Adjust `FRAME_S` (and with it the blink
speed) in `tram_lcd.py`.

---

## Background: liquidctl workarounds

On Windows with liquidctl 1.16.0 / WinUsbCDC 1.7, LCD uploads to a Kraken Z3 (firmware 5.11) do not work out
of the box. `tram_lcd.py` patches three things at runtime. Nothing is installed or changed system-wide.

1. **The LCD bulk interface is never found.** `WinUsbPy.list_usb_devices()` only returns devices when a
   vid/pid or name filter is passed, but liquidctl calls it without one. The script injects
   `vid=0x1E71, pid=0x3008`.
2. **`initialize()` fails.** The Z3 does not answer the lighting-info query (`0x20 0x03`). The script uses
   `light_init()`, which is liquidctl's initialize sequence without that query.
3. **"Failed to setup bucket for data transfer".** liquidctl's `_write_then_read()` treats the next HID
   message as the reply. The Kraken also sends periodic status reports, so it often reads the wrong
   message. The patched version waits for the matching reply id (`command id + 1`, same sub-command).

If a future liquidctl version fixes these, the patches can be removed.
