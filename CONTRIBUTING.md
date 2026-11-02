# Contributing

Thanks for wanting to help! This is a small hobby project, so the process is light.

## Ways to contribute

- **Report a bug:** open an issue using the *Bug report* template. Include your Kraken model, firmware and the log output.
- **Suggest a feature:** open an issue using the *Feature request* template.
- **Send a pull request:** for anything bigger than a small fix, please open an issue first so we can agree on the approach.

## Development setup

```powershell
git clone https://github.com/sorrovvs/kraken-tram-display.git
cd kraken-tram-display
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
```

Run `.venv\Scripts\python.exe tram_lcd.py setup` to create your `config.toml`. Then quit NZXT CAM and run
`.venv\Scripts\python.exe tram_lcd.py`.

### Working without the hardware

You can render a preview image without a Kraken connected:

```powershell
.venv\Scripts\python.exe tram_lcd.py preview -o preview.png
```

Check that everything stays inside the 320 px circle, including with a `tilt` set. Try a station with long
names and several line types (e.g. Praterstern or Karlsplatz) to test the layout.

## Code guidelines

- Keep it a **single script with no extra dependencies** unless there is a strong reason.
- User settings belong in `config.toml`: add the field to `Config` (load **and** save), to
  `config.example.toml` and to the README.
- Keep the setup wizard working with plain Enter presses, so every question needs a sensible default.
- Follow PEP 8. Use 4-space indentation and keep lines at most ~110 characters.
- Any liquidctl workaround must be explained in a comment **and** in `docs/TROUBLESHOOTING.md`.
- Add an entry under `## [Unreleased]` in `CHANGELOG.md`.

## Commit messages

Short imperative subject line, e.g. `Add brightness schedule` or `Fix row alignment for 1-digit minutes`.

## Pull request checklist

- [ ] Tested on real hardware (say which model/firmware), or explained why not
- [ ] Preview image checked for clipping
- [ ] README / CHANGELOG updated

By contributing you agree that your contributions are licensed under the [MIT License](LICENSE) and that you
follow the [Code of Conduct](CODE_OF_CONDUCT.md).
