# Kraken Tram Display

Live Vienna tram departures on the round LCD of an **NZXT Kraken Z3** AIO cooler.

The display shows lines **42** and **9** from the stop **Vinzenzgasse** with real-time countdowns from the
Wiener Linien open data API. It is easy to change it to any other stop and lines.

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

- **Real-time departures**: the next tram and the one after it, for every direction
- **Animated notifications**: when a countdown changes, a red box blinks once behind the number and then fades out over 3 s
- **Blinking "jetzt"**: the word blinks on a green background when a tram is arriving now
- **Refresh countdown**: the bottom line shows the seconds until the next data refresh
- **Tilt compensation**: the whole picture can be rotated to match a pump that is mounted at an angle
- **Self-healing**: the script reconnects on its own after USB or network errors

## Requirements

| What | Version / notes |
|---|---|
| NZXT Kraken **Z3** (Z53 / Z63 / Z73) | USB ID `1e71:3008`. Firmware 5.11 tested |
| Windows 10/11 | Linux should work, but the WinUSB workarounds are Windows-specific |
| Python | 3.10+ (3.13 tested) |
| NZXT CAM | **Must be closed.** CAM locks the USB device |

> **Note:** NZXT CAM's *Web Integration* only works on Kraken Elite / 2023 models. That is why this project
> drives the Z3 screen directly through [liquidctl](https://github.com/liquidctl/liquidctl).

## Installation

```powershell
git clone https://github.com/sorrovvs/kraken-tram-display.git
cd kraken-tram-display
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
```

## Usage

1. **Quit NZXT CAM** completely (tray icon → Exit).
2. Start the display:

   ```powershell
   .venv\Scripts\python.exe tram_lcd.py      # with log output
   .venv\Scripts\pythonw.exe tram_lcd.py     # without a console window
   ```

3. Stop it with `Ctrl+C`. The screen then switches back to the liquid temperature.

### Start automatically at login

`start-hidden.vbs` starts the script without a window. Put a shortcut to it in your Startup folder
(press `Win+R` and type `shell:startup`):

```powershell
$ws = New-Object -ComObject WScript.Shell
$l  = $ws.CreateShortcut("$([Environment]::GetFolderPath('Startup'))\Kraken Tram Display.lnk")
$l.TargetPath = "wscript.exe"
$l.Arguments  = "`"$PWD\start-hidden.vbs`""
$l.Save()
```

Also turn off "Start CAM on Windows startup" in the CAM settings.

## Configuration

All settings are constants at the top of [`tram_lcd.py`](tram_lcd.py):

| Setting | Default | Meaning |
|---|---|---|
| `DIVA` | `60201426` | Stop ID (Vinzenzgasse). See [Finding your stop](#finding-your-stop) |
| `LINES` | `["42", "9"]` | Lines to show, in this order |
| `FETCH_EVERY` | `10` | Seconds between data refreshes |
| `TILT_FIX` | `28` | Degrees to rotate the picture counter-clockwise (negative = clockwise) |
| `BRIGHTNESS` | `None` | Force the LCD brightness (0–100), or leave it unchanged |
| `FRAME_S` | `0.25` | Length of one animation frame in seconds |
| `FADE_SECONDS` | `3.0` | How long the red notification takes to fade |
| `BLINK_FRAMES` | `2` | How many frames "jetzt" stays on, then off |
| `CLOCK_COL`, `CREDIT_COL`, `GREEN`, `RED`, … | | Colours as RGB tuples |

### Finding your stop

Download the Wiener Linien stop list and search it for your stop name:

```powershell
Invoke-WebRequest https://www.wienerlinien.at/ogd_realtime/doku/ogd/wienerlinien-ogd-haltepunkte.csv -OutFile stops.csv
Select-String -Path stops.csv -Pattern "Vinzenzgasse"
```

The second column (`DIVA`) is the value to use.

## How it works

```
Wiener Linien API ──(every 10 s)──► tram_lcd.py ──► Pillow draws ~40 frames ──► GIF ──(liquidctl, USB)──► Kraken LCD
```

The Z3 cannot render web pages and only accepts static images or GIFs. On every refresh cycle the
script draws an animated GIF that covers the time until the next refresh. Each frame is drawn for the
exact moment it will be visible, so the countdowns, fades and blinking run on the cooler itself, with
only **one USB upload per cycle**.

Details on the liquidctl workarounds are in [docs/TROUBLESHOOTING.md](docs/TROUBLESHOOTING.md).

## Caveats

- **Cooling:** while CAM is closed, the pump and fans keep the last speed they were set to. They do
  not follow a temperature curve. You can set a curve with liquidctl, e.g.
  `liquidctl --match kraken set pump speed 30 50 40 80`.
- **LCD memory:** the screen is rewritten once per refresh cycle. It is not known whether the
  Kraken's image memory wears out from frequent writes. Raise `FETCH_EVERY` if you want fewer writes.

## Contributing

Contributions are welcome. Please read [CONTRIBUTING.md](CONTRIBUTING.md) and the
[Code of Conduct](CODE_OF_CONDUCT.md) first.

## License & data

Code: [MIT](LICENSE) © Sorrow

Departure data: [Wiener Linien Open Government Data](https://www.data.gv.at/katalog/dataset/wiener-linien-echtzeitdaten-via-datendrehscheibe-wien),
licensed [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/deed.de). Datenquelle: Stadt Wien – data.wien.gv.at.

Not affiliated with NZXT or Wiener Linien.
