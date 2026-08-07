# Troubleshooting

## InkyPi service not running

```bash
sudo systemctl status inkypi.service   # look for "Active: active (running)"
journalctl -u inkypi -n 100            # recent logs
journalctl -u inkypi -f                # tail
sudo systemctl restart inkypi.service
sudo /usr/local/bin/inkypi -d          # run manually to see errors directly in the terminal
```

If the journal shows `Install in progress — refusing to start`, an earlier `install.sh` run left `/var/lib/inkypi/.install-in-progress` in place — rerun `install.sh` to let it complete and clear the lockfile, or remove it manually with `sudo rm /var/lib/inkypi/.install-in-progress` if you're certain no install is running.

## Log rotation

On a long-running Pi, the systemd journal can grow large enough to fill an SD card.

```bash
journalctl --disk-usage          # check current usage
sudo journalctl --vacuum-size=50M  # one-off cleanup
```

Persistent cap — add to `/etc/systemd/journald.conf`:

```ini
SystemMaxUse=50M
RuntimeMaxUse=50M
```

Then `sudo systemctl restart systemd-journald`. `install.sh`/`update.sh` apply these caps automatically on a fresh install/update if no explicit journald settings already exist. The in-memory log buffer used in `--dev` mode (max 1,000 entries) is never written to disk and doesn't affect journal size.

## Intermittent Wi-Fi / SSH drops

Check whether Wi-Fi power saving is enabled on `wlan0` (`2` = disabled, recommended for an always-on Pi):

```bash
nmcli -g 802-11-wireless.powersave connection show "$(nmcli -g GENERAL.CONNECTION device show wlan0 | head -n 1)"
nmcli -f GENERAL.STATE,GENERAL.CONNECTION,IP4.ADDRESS dev show wlan0
cat /proc/net/wireless
journalctl -b | grep -Ei 'wlan0|brcmfmac|CTRL-EVENT|deauth|disassoc'
```

InkyPi's install/update hardens NetworkManager-based systems by disabling Wi-Fi powersave on the active `wlan0` profile automatically. If drops persist, compare signal strength across nearby APs with the same SSID and pin to the strongest BSSID.

## API key not configured

Some plugins require API keys in `.env` at the project root — see [api_keys.md](api_keys.md).

## Clock/sunrise/sunset time is wrong

Set the correct timezone on the Settings page of the web UI.

## Failed to retrieve weather data (OpenWeatherMap 401)

```
ERROR - root - Failed to retrieve weather data: b'{"cod":401, ...requires a separate subscription to the One Call by Call plan...}'
```

The Weather plugin uses OpenWeatherMap's One Call API 3.0, which needs its own (free-tier) subscription — see [api_keys.md](api_keys.md).

## No EEPROM detected (Inky displays)

```
RuntimeError: No EEPROM detected! You must manually initialise your Inky board.
```

InkyPi uses the [inky](https://github.com/pimoroni/inky) library's auto-detect (`inky.auto.auto()` in `src/display/inky_display.py`), which doesn't work on some boards. See Pimoroni's [manual setup instructions](https://github.com/pimoroni/inky?tab=readme-ov-file#manual-setup) and replace the `auto()` call in `InkyDisplay.initialize_display()` with a direct import of your panel's Inky module (e.g. `from inky.inky_ac073tc1a import Inky` for the 7.3" Inky Impression), then restart the service.

## Waveshare e-Paper devices

### Missing modules

In addition to the libraries used for Inky screens, Waveshare needs `gpiozero`, `lgpio`, `RPi.GPIO`.

### Screen not updating

`ls /dev/sp*` should show both `spidev0.0` and `spidev0.1`. If only the first is present, check `/boot/firmware/config.txt` for `dtoverlay=spi0-0cs` — the standard InkyPi install adds this. Delete it for default behavior, or replace it with `dtoverlay=spi0-2cs`.

### Failed to download Waveshare driver

`install.sh` fetches the EPD driver from the [Waveshare e-Paper GitHub repo](https://github.com/waveshareteam/e-Paper/tree/master/RaspberryPi_JetsonNano/python/lib/waveshare_epd) based on the `-W` argument. Double-check the display model is correct and that a driver file exists at that path.

Some displays (e.g. `epd4in0e`) live under [`E-paper_Separate_Program`](https://github.com/waveshareteam/e-Paper/tree/master/E-paper_Separate_Program) instead. If yours is there, manually copy `epdXinX.py` and `epdconfig.py` into `InkyPi/src/display/waveshare_epd/`, plus the matching `DEV_config*` file for your board (grab all of them if unsure which applies). Example for `epd13in3E` on a Pi Zero 2 W:

```bash
cd InkyPi/src/display/waveshare_epd/
curl -L -O https://raw.githubusercontent.com/waveshareteam/e-Paper/refs/heads/master/E-paper_Separate_Program/13.3inch_e-Paper_E/RaspberryPi/python/lib/epd13in3E.py
curl -L -O https://raw.githubusercontent.com/waveshareteam/e-Paper/refs/heads/master/E-paper_Separate_Program/13.3inch_e-Paper_E/RaspberryPi/python/lib/epdconfig.py
curl -L -O https://raw.githubusercontent.com/waveshareteam/e-Paper/refs/heads/master/E-paper_Separate_Program/13.3inch_e-Paper_E/RaspberryPi/python/lib/DEV_Config_64_b.so
```

Rerun the install script once the files are in place — it detects the local driver and skips the download.

## Today's newspaper not found

The Newspaper plugin sources front pages from [Freedom Forum](https://frontpages.freedomforum.org/gallery); their list of available newspapers changes periodically and InkyPi's copy can lag. Open an issue with the newspaper name if you hit this.

## Known issues during Pi Zero W installation

The **original** 32-bit Pi Zero W (not the Zero 2 W) has known install issues — see this [GitHub issue](https://github.com/fatihak/InkyPi/issues/5) for community discussion.

**Pip connection errors** (`RemoteDisconnected('Remote end closed connection without response')`):

```bash
source "/usr/local/inkypi/venv_inkypi/bin/activate"
pip install -r install/requirements.txt
deactivate
sudo systemctl restart inkypi.service
```

**Numpy ImportError** (`should not try to import numpy from its source directory`):

```bash
sudo su
source "/usr/local/inkypi/venv_inkypi/bin/activate"
pip uninstall Pillow
pip install Pillow
deactivate
sudo systemctl restart inkypi.service
```

## Plugin development troubleshooting

> See also: [building_plugins.md](building_plugins.md).

### API key validation failures

**Symptom:** error toast like `"OPEN_WEATHER_MAP_SECRET API key not configured"`. Display keeps the previous image or shows blank.

**Cause:** the secret is missing from `.env`, or the file doesn't exist. API-backed plugins call `device_config.load_env_key("<KEY_NAME>")` and raise `RuntimeError` when it's falsy.

**Verify:**
```bash
grep -E 'OPEN_WEATHER_MAP_SECRET|GITHUB_SECRET|GOOGLE_AI_SECRET|OPEN_AI_SECRET|NASA_SECRET' /usr/local/inkypi/.env
```

**Fix:** add the key (see [api_keys.md](api_keys.md)), restart the service.

### Plugin fetch timeouts (Newspaper, Comic, RSS)

**Symptom:** `requests.exceptions.ReadTimeout`/`ConnectionError` in the journal; plugin-specific error toasts.

**Cause:** upstream source is slow/unreachable, or DNS resolution failed. Default HTTP timeout is 20s.

**Verify:** `journalctl -u inkypi -n 50 | grep -E 'Timeout|ConnectionError|Failed to'`, then `curl -I <feed_url>` from the Pi.

**Fix:** retry after a few minutes; confirm network access (`ping 8.8.8.8`); raise `INKYPI_HTTP_TIMEOUT_DEFAULT_S` in `.env` if a feed is reliably slow (see [http.md](http.md)).

### Image dimension mismatch

**Symptom:** journal shows `dimension_mismatch | plugin_id=... expected=800x480 actual=480x800 — skipping display push`. Display isn't updated.

**Cause:** `generate_image()` returned an image whose size doesn't match the device resolution (`OutputDimensionMismatch` in `src/utils/output_validator.py`). A 90° transposition auto-corrects; anything else raises.

**Fix:** call `self.get_oriented_dimensions(device_config)` for the correct `(width, height)` and use that when creating the image.

### Memory pressure on Pi Zero

**Symptom:** service killed silently, or `MemoryError`/`Killed` in the journal. Chromium-based plugins (Weather, Calendar, AI Text, and other HTML-rendered plugins) are most affected — headless Chromium costs ~150–200 MB on a 512 MB device.

**Verify:** `journalctl -u inkypi -n 50 | grep -E 'Killed|MemoryError|OOM'`, `free -m`.

**Fix:** `sudo systemctl enable --now zramswap` if not active; increase refresh interval; avoid back-to-back Chromium-heavy plugins in one playlist; consider a Pi Zero 2 W over the original Zero W.

### Screenshot plugin failures (Chromium not found / sandbox error)

**Symptom:** `"Failed to take screenshot, please check logs."`; journal shows `"No supported browser found"` or a Chromium exit code like `status=127`.

**Verify:** `which chromium chromium-headless-shell google-chrome`, `journalctl -u inkypi -n 50 | grep -i 'screenshot\|chromium\|browser'`.

**Fix:** `sudo apt-get install -y chromium-browser; sudo systemctl restart inkypi.service`. If Chromium crashes rather than being missing, check `/dev/shm` is writable — InkyPi already sets `--disable-dev-shm-usage` to route temp files to `/tmp`, so ensure `/tmp` has ≥64 MB free.

### Jinja2 template render errors

**Symptom:** `UndefinedError` (`'dict object' has no attribute 'foo'`) on the settings page, or blank/garbled HTML. HTML-escaped text (`&lt;b&gt;` instead of `<b>`) where markup was expected.

**Cause:** a template variable wasn't added to `generate_settings_template()`'s return dict (or `render_image()`'s `template_params`); or autoescape is on for `.html` templates and a value with intentional markup wasn't wrapped in `{{ value | safe }}`.

**Verify:** `journalctl -u inkypi -n 50 | grep -i 'UndefinedError\|TemplateSyntaxError\|jinja'`, or run the dev server and hit the settings page for the full traceback.

**Fix:** add the missing key before rendering; use `{{ value | safe }}` only for trusted, intentionally-markup values; validate a template offline with:
```bash
python -c "from jinja2 import Environment; Environment().parse(open('src/plugins/<id>/render/<file>.html').read())"
```

## Colors look washed out or incorrect

Expected to some degree on e-ink, especially multi-color panels with dithering. The Settings page exposes Saturation/Contrast/Sharpness/Brightness (applied via Pillow's `ImageEnhance`) — experiment to find what suits your panel.

For Pimoroni Inky displays, there's also an `Inky Driver Saturation` setting controlling the palette dithering saturation in the `inky` library — try `0` first, per [this note from Pimoroni](https://github.com/pimoroni/inky/issues/225#issuecomment-3213935144).
