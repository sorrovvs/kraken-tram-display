# Changelog

All notable changes to this project are documented here.
The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project follows
[Semantic Versioning](https://semver.org/).

## [1.1.0] - 2026-11-02

### Added
- **Any station, any line:** settings now live in `config.toml` instead of the code
- Interactive setup wizard: `python tram_lcd.py setup` (station search, line picker, language, tilt, footer)
- Commands `search`, `lines` and `preview` (preview renders a frame without a Kraken)
- `--config` option to switch between config files
- Line colours for U1–U6, plus colours by vehicle type for bus, night bus, S-Bahn and WLB
- English labels (`language = "en"`)
- Configurable title, footer text and clock/footer colours
- Rows stay centred when fewer than 4 are shown; long station names shrink to fit
- Support for the Kraken 2023 / 2024 models liquidctl knows (untested), with a static-image fallback for
  firmware that doesn't accept GIFs

### Changed
- Requires Python 3.11+ (uses `tomllib`)
- liquidctl is only imported when the display runs, so `setup`, `search`, `lines` and `preview` work
  without touching USB

## [1.0.0] - 2026-11-02

### Added
- Live departures for lines 42 and 9 at Vinzenzgasse on the NZXT Kraken Z3 LCD
- One animated GIF per refresh cycle (10 s), with frames drawn for the moment they are shown
- Red notification box when a countdown changes: one blink, then a 3 s fade
- Blinking "jetzt" on a green background
- "LIVE · NEU IN n S" refresh countdown
- Tilt compensation (`TILT_FIX`) for pumps mounted at an angle
- Runtime workarounds for liquidctl / WinUsbCDC on Windows (see `docs/TROUBLESHOOTING.md`)
- Automatic reconnect after USB or network errors
