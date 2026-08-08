# InkyPi Detailed Installation

Two install paths. Pick based on hardware and patience.

## Option 1 — Pre-built image (Pi Zero 2 W only) (JTN-533)

Every tagged GitHub release ships a pre-installed `.img.xz` (`.github/workflows/build-pi-image.yml`): Pi OS Lite arm64 with InkyPi already installed and enabled as a systemd service. Nothing personal (hostname, Wi-Fi, SSH credentials) is baked in — Pi Imager's advanced options handle that at flash time. The qemu boot-verification proves the kernel reaches userspace but can't simulate real GPIO/SPI — treat it like any OS image and verify the display lights up on real hardware.

Scope: **Pi Zero 2 W only** (arm64, though `install.sh` itself runs 32-bit armv7l on this board). For Pi 4/5 or a Compute Module, use Option 2 — the wheelhouse (see below) still gets you a 2–3 minute on-device install.

1. Download `inkypi-<version>-pi-zero-2-w.img.xz` and its `.sha256` sidecar from the [releases page](https://github.com/florentineprinzessinzusachsen/InkyPi-modernize/releases).
2. Verify: `shasum -a 256 -c inkypi-<version>-pi-zero-2-w.img.xz.sha256`. If it fails, don't flash it.
3. Pi Imager → **Choose OS → Use custom** → select the `.img.xz` (Pi Imager handles `.xz` decompression).
4. **Critical:** click the gear icon and set hostname, SSH, Wi-Fi, locale, and a non-default user — the image intentionally carries none of this.
5. Flash, boot. cloud-init applies your settings on first boot; the web UI is available at `http://<hostname>.local/` within 30–60 seconds.

If the web UI never comes up, fall back to Option 2 and run `install.sh` by hand for a full log.

## Option 2 — Install from source

Use this for the latest `main`, contributing, a non-Zero-2-W board, or full visibility into the install.

### Flash Raspberry Pi OS

1. Install [Raspberry Pi Imager](https://www.raspberrypi.com/software/).
2. Choose your Pi model, the recommended OS, and the target SD card.
3. **Edit Settings** before writing:
   - General: hostname, username/password (never leave the Pi default), Wi-Fi, timezone.
   - Services: enable SSH with password auth.
4. Apply and write.

<img src="./images/raspberry_pi_imager.png" alt="Raspberry Pi Imager" width="500"/>
<p float="left">
  <img src="./images/raspberry_pi_imager_general.png" width="250" />
  <img src="./images/raspberry_pi_imager_options.png" width="250" />
  <img src="./images/raspberry_pi_imager_services.png" width="250" />
</p>

### Run the installer

SSH into the Pi (`ssh <username>@<hostname>.local`), then:

```bash
git clone https://github.com/florentineprinzessinzusachsen/InkyPi-modernize.git
cd InkyPi-modernize
sudo bash install/install.sh
```

If you're using a Waveshare display instead of the default Pimoroni Inky, pass its model name (without `.py`) with `-W`:

```bash
sudo bash install/install.sh -W epd7in3f
```

The script prompts for a reboot once it finishes — accept it, since the SPI/I2C interface changes it makes only take effect on boot. Afterwards the web UI is available at `http://<hostname>.local/`.

### Pi Zero 2 W notes

- **OS**: current default Pi OS is Trixie (Debian 13); InkyPi supports it natively. For an older Bookworm image, use "Raspberry Pi OS (Legacy, 64-bit) Lite" in Imager.
- **`arm_64bit=1`**: the current Trixie image sets this in `/boot/firmware/config.txt`. If upgrading an existing Pi and using a 64-bit kernel, confirm the line is present — without it the 64-bit `kernel8.img` won't boot on the Zero 2 W.
- **Install time**: a from-source `install.sh` run takes ~15 minutes end-to-end without a wheelhouse (compiling numpy/Pillow/playwright on one Cortex-A53 core) — don't assume it's hung. zramswap is auto-enabled on Bullseye/Bookworm/Trixie and is critical to avoid an OOM during the pip build.
- **Wheelhouse**: tagged releases ship a pre-built wheelhouse (per-arch compiled wheels for `linux_armv7l`/`linux_aarch64`) that `install.sh` fetches automatically based on `uname -m`, verifies via sha256, and feeds to pip/uv via `--find-links` — no on-device compilation. Drops install time to ~2–3 min and peak RAM to <200 MB on a Pi Zero 2 W. The `--require-hashes` guarantee still applies. If the wheelhouse is missing (dev branches, network failure, unsupported arch, checksum mismatch), `install.sh` logs a fallback message and does a normal source install — no manual action needed. Opt out with `sudo INKYPI_SKIP_WHEELHOUSE=1 ./install.sh`.
- **uv**: `install.sh` uses [uv](https://github.com/astral-sh/uv) instead of pip for the actual package install — lower peak resolver memory and 3–5x faster. Bootstrapped via `pip install uv` (same PyPI + hashes the venv already trusts, no third-party curl-pipe). `UV_HTTP_TIMEOUT=60` is set on every uv call so flaky Wi-Fi doesn't hang the install. Falls back cleanly to plain pip if uv can't be installed.
- **NTP**: the Pi Zero 2 W has no RTC battery — the clock starts at the last `fake-hwclock` value, which can predate a server's TLS cert `notBefore` date and cause "certificate is not yet valid" errors. `install.sh` waits up to 60s for `systemd-timesyncd` to report `NTPSynchronized` before starting package installs; if it times out, install proceeds with a warning (set the clock manually with `sudo date -u -s 'YYYY-MM-DD HH:MM:SS'` if needed).
- Watching an unattended cloud-init install: redirect output in your `runcmd:` block (`... > /var/log/inkypi-install.log 2>&1 && touch /var/log/inkypi-install.done || touch /var/log/inkypi-install.failed`), then `tail -f /var/log/inkypi-install.log` after boot.
- The "[Known Issues during Pi Zero W Installation](./troubleshooting.md#known-issues-during-pi-zero-w-installation)" section covers the **original** 32-bit Pi Zero W, not the Zero 2 W — the Zero 2 W (4× Cortex-A53, ARMv8) doesn't hit those issues as long as zramswap is active (automatic).

### Re-editing user-data after first boot: the cloud-init `runcmd` one-shot trap (JTN-591)

If you flash a card, boot the Pi once (even a failed Wi-Fi boot counts), then re-mount the card and add/change a `runcmd:` block in `/boot/firmware/user-data` before booting again — **`runcmd` silently never runs**. No error, no log line.

**Why:** `runcmd` is a per-instance one-shot cloud-init module. First boot records an instance ID at `/var/lib/cloud/data/instance-id`; every later boot of that same card matches the recorded ID and skips all per-instance modules, including `runcmd`, without any warning.

**Detect it:**

```bash
cat /var/lib/cloud/data/instance-id
cat /var/lib/cloud/instances/$(cat /var/lib/cloud/data/instance-id)/scripts/runcmd   # empty/missing InkyPi commands?
sudo journalctl -u cloud-init -n 100   # no runcmd output if it was skipped
```

**Recover** (pick one):

- On the Pi: `sudo cloud-init clean --logs && sudo reboot` (or run `scripts/install_testing/cloud_init_clean.sh`, copied to the Pi).
- Without SSH, from your computer with the card mounted: delete `.../rootfs/var/lib/cloud/instances/` and `.../rootfs/var/lib/cloud/data/instance-id`, then boot.
- On the Pi: `echo "fresh-instance-$(date +%s)" | sudo tee /var/lib/cloud/data/instance-id && sudo reboot`.

**Prevent it** while iterating on `user-data` — add at the top, then remove once the install is stable (it makes `runcmd` run on *every* boot, which you don't want long-term):

```yaml
#cloud-config
always_rerun_modules: [runcmd]
```
