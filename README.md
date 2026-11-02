# Kraken Tram Display

Live **Wiener Linien** departures for **any station in Vienna** on the round LCD of an **NZXT Kraken** AIO cooler.
Trams, buses, U-Bahn, night buses and the Badner Bahn are all supported. A setup wizard lets you pick
your station and lines in under a minute.

```
        Vinzenzgasse
           12:22
 [42] Antonigasse     4'  14'
 [42] Schottentor     6'  16'
 [9]  Gersthof    [jetzt]  10'
 [9]  Westbahnhof     4'  11'
     LIVE · NEU IN 8 S
        ( © Sorrow )
```

## Features

- **Any station, any line**: choose up to 4 rows (line + direction) with the setup wizard
- **Real-time departures**: the next vehicle and the one after it
- **Line colours**: U1–U6 in their official colours, plus trams, buses, S-Bahn and WLB
- **Animated notifications**: when a countdown changes, a red box blinks once and then fades over 3 s
- **Blinking "jetzt" / "now"** on a green background when a vehicle is arriving
- **Refresh countdown**: the bottom line shows the seconds until the next data refresh
- **German or English** labels
- **Tilt compensation** for pumps mounted at an angle
- **Self-healing**: reconnects on its own after USB or network errors

## Requirements

| What | Notes |
|---|---|
| NZXT Kraken with LCD | **Kraken Z3** (Z53/Z63/Z73) is tested. Kraken 2023 / 2024 (Elite, Plus) should work through the same liquidctl driver but are untested |
| Windows 10/11 | Linux may work, but the USB workarounds are written for Windows |
| Python | **3.11+** (3.13 tested) |
| NZXT CAM | **Must be closed while the display runs.** CAM locks the USB device |

> **Note:** NZXT CAM's *Web Integration* only works on newer Krakens. This project drives the LCD directly
> through [liquidctl](https://github.com/liquidctl/liquidctl), so it also works on the Z3.

## Quick start

```powershell
git clone https://github.com/sorrovvs/kraken-tram-display.git
cd kraken-tram-display
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt

.venv\Scripts\python.exe tram_lcd.py setup     # pick your station and lines
.venv\Scripts\python.exe tram_lcd.py           # quit NZXT CAM first, then run
```

### The setup wizard

```
=== Kraken Tram Display setup ===

Station name (e.g. Karlsplatz, Schwedenplatz): praterstern
   1) Praterstern S U  (DIVA 60201040)
Number [1]:

Lines departing at Praterstern S U right now:
   1) U1    → Alaudagasse                  next: 2 min
   2) U1    → Leopoldau                    next: 1 min
   3) U2    → Karlsplatz                   next: 4 min
   ...
Pick up to 4 rows, comma separated (e.g. 1,3) - Enter = first 4: 1,2,5

Display options (Enter keeps the value in brackets):
Language de/en [de]: en
Tilt in degrees, counter-clockwise (0 = straight) [0]:
Refresh every N seconds [10]:
Footer text (type - for none) [© Sorrow]:

Saved config.toml
Preview image: preview.png
```

The wizard saves `config.toml` and renders `preview.png`, so you can see the result before running it on
the cooler. Run `setup` again at any time to change station.

## Commands

| Command | What it does |
|---|---|
| `python tram_lcd.py` | Run the display (same as `run`) |
| `python tram_lcd.py setup [name]` | Interactive setup |
| `python tram_lcd.py search <name>` | Find stations and their DIVA numbers |
| `python tram_lcd.py lines <name or DIVA>` | List every line and direction departing at a station |
| `python tram_lcd.py preview [-o file.png]` | Render one frame from live data, without a Kraken |
| `python tram_lcd.py stop` | Ask a running display to exit cleanly |
| `--config <path>` | Use another config file, e.g. to switch between home and work stations |

Use `pythonw.exe` instead of `python.exe` to run without a console window.

## Configuration (`config.toml`)

The setup wizard writes this file for you. You can also edit it by hand (see
[`config.example.toml`](config.example.toml)):

```toml
[station]
name = "Vinzenzgasse"
diva = "60201426"

# up to 4 rows, shown in this order; `towards` is optional (leave it out = every direction)
[[departures]]
line = "42"
towards = "Antonigasse"

[[departures]]
line = "9"

[display]
title = ""               # empty = station name
language = "de"          # "de" or "en"
tilt = 0                 # degrees counter-clockwise (negative = clockwise)
brightness = -1          # 0-100, -1 = leave unchanged
refresh_seconds = 10     # data refresh interval (min 5)
footer = "© Sorrow"      # empty = no footer badge

[colors]
clock = "#C8AAFF"
footer = "#C8AAFF"
```

With no `[[departures]]` at all, the display shows the first 4 lines of the station. Restart the display
after editing.

## Desktop launcher (`RunWienerLinienDisplay.exe` / `ExitWienerLinienDisplay.exe`)

Double-click buttons for Windows to start and stop the display. Build them once. They use the C#
compiler that ships with Windows, so there's nothing extra to install:

```powershell
powershell -ExecutionPolicy Bypass -File launcher\build.ps1 -Desktop
```

This creates both `.exe` files in the project folder and copies them to your desktop.

Each double-click on **`RunWienerLinienDisplay.exe`**:

1. closes NZXT CAM,
2. stops a display that is already running, cleanly between two uploads (so clicking again = restart),
3. starts the display in the background, without a window, and logs to `display.log`.

If there's no `config.toml` yet, it opens the setup wizard instead.

**`ExitWienerLinienDisplay.exe`** (same icon, with a cross) stops the display cleanly between two
uploads, and the Kraken LCD goes back to showing the liquid temperature. If the display doesn't exit
within 15 s, it is force-closed. From a terminal, `python tram_lcd.py stop` does the same.

The exit icon is generated from `launcher\icon.ico` by `launcher\make_exit_icon.py` (needs Pillow, which
is in the venv already).

## Start automatically at login

`start-hidden.vbs` starts the display without a window. Put a shortcut to it (or to the
desktop launcher) in your Startup folder (press `Win+R` and type `shell:startup`):

```powershell
$ws = New-Object -ComObject WScript.Shell
$l  = $ws.CreateShortcut("$([Environment]::GetFolderPath('Startup'))\Kraken Tram Display.lnk")
$l.TargetPath = "wscript.exe"
$l.Arguments  = "`"$PWD\start-hidden.vbs`""
$l.Save()
```

Also turn off "Start CAM on Windows startup" in the CAM settings.

## How it works

```
Wiener Linien API ──(every N s)──► tram_lcd.py ──► Pillow draws the frames ──► GIF ──(liquidctl, USB)──► Kraken LCD
```

The Kraken LCD cannot render web pages and only accepts static images or GIFs. On every refresh cycle the
script draws an animated GIF that covers the time until the next refresh. Each frame is drawn for the
exact moment it will be visible, so the countdowns, fades and blinking run on the cooler itself, with
only **one USB upload per cycle**. On Kraken 2023 firmware 2.x, which doesn't accept GIFs, the script
automatically falls back to static images.

Details on the liquidctl workarounds are in [docs/TROUBLESHOOTING.md](docs/TROUBLESHOOTING.md).

## Caveats

- **Cooling:** while CAM is closed, the pump and fans keep the last speed they were set to. They do
  not follow a temperature curve. You can set a curve with liquidctl, e.g.
  `liquidctl --match kraken set pump speed 30 50 40 80`.
- **LCD memory:** the screen is rewritten once per refresh cycle. It is not known whether the
  Kraken's image memory wears out from frequent writes. Raise `refresh_seconds` if you want fewer writes.

## Contributing

Contributions are welcome. Please read [CONTRIBUTING.md](CONTRIBUTING.md) and the
[Code of Conduct](CODE_OF_CONDUCT.md) first.

## License & data

Code: [MIT](LICENSE) © Sorrow

Departure and stop data: [Wiener Linien Open Government Data](https://www.data.gv.at/katalog/dataset/wiener-linien-echtzeitdaten-via-datendrehscheibe-wien),
licensed [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/deed.de). Datenquelle: Stadt Wien – data.wien.gv.at.

Not affiliated with NZXT or Wiener Linien.
