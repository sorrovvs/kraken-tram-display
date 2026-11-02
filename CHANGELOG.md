# Changelog

All notable changes to this project are documented here.
The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project follows
[Semantic Versioning](https://semver.org/).

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
