# Moving the controller to a Raspberry Pi 5

## What changes—and what does not

```text
Phones/laptops -- Wi-Fi or Ethernet LAN --> Raspberry Pi 5 -- USB --> Nano --> panel DIN
                                                                    |
                                                  shared GND with external LED supply
```

- Use **64-bit Raspberry Pi OS**, with its supported Python 3.11+; Raspberry Pi OS Lite is sufficient. A desktop/monitor is not required. Provide proper Pi power and cooling for sustained video decoding.
- Pi Ethernet is recommended when convenient, but strong Wi-Fi is fine. Wireless phones can control a wired Pi. Devices must be on the same reachable LAN, not isolated guest Wi-Fi.
- Keep the existing Nano firmware, panel wiring, and external LED power supply. **Never power the LED panel from the Pi.** Review the [power warnings](../README.md#wiring-and-power--check-this-first).
- No WSL, usbipd, webcam passthrough, camera permissions, or HTTPS setup is needed.
- Playlists continue after browsers disconnect. Reboots/app restarts start black; playback and panel settings are not saved.

## 1. Prepare the Pi

In Raspberry Pi Imager, choose **Raspberry Pi OS (64-bit)**, create your own non-root user, enable SSH, and optionally set hostname **`ledmatrix`** and Wi-Fi credentials. SSH in after boot. Examples assume your project will be `/home/YOUR_USER/led-matrix`; the provided service uses that location.

On the Pi:

```bash
sudo apt update
sudo apt install python3-venv python3-pip avahi-daemon rsync
sudo usermod -aG dialout "$USER"
sudo systemctl enable --now avahi-daemon
```

Log out of SSH and reconnect so `dialout` membership takes effect. Check:

```bash
uname -m
python3 --version
id -nG
hostname -I
```

Architecture should be **`aarch64`**, not `armv7l`. Set the correct timezone, e.g. `sudo timedatectl set-timezone America/New_York`. A router DHCP reservation is useful for a stable Pi IP. `.local` hostnames depend on client mDNS support; the IP address is the reliable fallback.

## 2. Copy the project—not the old virtual environment

From the copied project directory on your PC/WSL host, replacing `YOUR_USER` and the hostname/IP:

```bash
rsync -av --exclude='.venv' --exclude='__pycache__' --exclude='deploy/matrix.env' ./ YOUR_USER@ledmatrix.local:~/led-matrix/
```

SFTP or another file-copy tool is also fine. Include hidden files such as `.streamlit/config.toml`. **Do not copy/reuse `.venv`**: PC binaries and absolute paths will not work on the Pi. Copy your gallery images separately. WSL paths like `/mnt/c/Users/…` do not exist on the Pi.

Before disconnecting the old host, record your rotation/flips, serpentine setting, row/column layout, channel correction, safe brightness, and baud rate; they are not automatically migrated. Stop the old app and move the Nano's USB cable to the Pi. No reflash is necessary.

## 3. Install a fresh environment

On the Pi, in `~/led-matrix`:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install --only-binary=:all: -r requirements.txt
.venv/bin/python -m unittest discover -s tests -v
```

Using binary wheels avoids a lengthy native video-library build. A binary-only dependency-resolution dry run succeeded for Linux ARM64/Python 3.13, including PyAV 16.1.0 and Deno 2.9.8; this is **not a claim that installation or performance has been tested on your Pi**. If pip reports no matching distribution, check 64-bit OS, Python version, pip version, network access, and the named package before attempting a source build. A system-wide FFmpeg installation is not needed with the PyAV wheels. Do not install dependencies with `sudo pip`.

## 4. Identify the Nano and test manually

With the Nano attached:

```bash
ls -l /dev/serial/by-id/
```

Use the full Nano symlink rather than `/dev/ttyUSB0`, which can change across boots. If `by-id` is missing, inspect `/dev/serial/by-path/` for a physical-USB-port-specific path. Identical cheap adapters may have non-unique identifiers; verify which device is your Nano before selecting a path.

Run manually, substituting the actual path:

```bash
MATRIX_SERIAL_PORT='/dev/serial/by-id/REPLACE_WITH_YOUR_NANO' .venv/bin/python -m matrix_app
```

It should connect while remaining **Blackout**, even without a browser. From a phone/laptop on the LAN, open:

```text
http://ledmatrix.local:8501
http://<Pi-LAN-IP>:8501
```

Do **not** use `localhost` on the phone/laptop; that refers to the phone/laptop itself. Use Wiring test at low brightness, then restore your orientation settings. Test a YouTube link, close every browser, and confirm the panel continues. Open another device and confirm it shows the current mode instead of resetting it. Use Blackout, then Ctrl+C in SSH to stop the manual process before enabling the service.

Without `MATRIX_SERIAL_PORT`, manual startup is preview-only; you can type the stable path in the sidebar and click Connect. `MATRIX_BAUD` defaults to 250000 and must match firmware. Network failures or USB protocol errors require an explicit retry; after an unplug/replug or failed initial connection, use Connect again.

## 5. Enable automatic startup

The template assumes **`/home/YOUR_USER/led-matrix`** and a normal user named `YOUR_USER`. If your project/home is elsewhere, edit `WorkingDirectory`, `EnvironmentFile`, and `ExecStart` in the service template before installing. Do not run this as root.

Copy and edit the environment file:

```bash
cp deploy/matrix.env.example deploy/matrix.env
nano deploy/matrix.env
```

Set, for example:

```ini
MATRIX_SERIAL_PORT=/dev/serial/by-id/REPLACE_WITH_YOUR_NANO
MATRIX_BAUD=250000
```

This file is loaded by systemd only. Keep the port empty if you want preview-only service startup.

Install and start the service **from your normal Pi user's shell**:

```bash
sudo install -m 644 deploy/led-matrix@.service /etc/systemd/system/led-matrix@.service
sudo systemctl daemon-reload
sudo systemctl enable --now "led-matrix@$USER.service"
systemctl status "led-matrix@$USER.service"
```

Read logs:

```bash
journalctl -u "led-matrix@$USER.service" -n 100 --no-pager
```

The service starts the worker before the first browser, optionally connects the Nano, serves the LAN UI on port 8501, and starts black. It runs in the background without an SSH session. It restarts after process failure; all restarts reset panel settings/playback. The boot service does not automatically resume a previous playlist. Reapply your orientation/settings before playing after a restart.

Maintenance commands:

```bash
sudo systemctl restart "led-matrix@$USER.service"
sudo systemctl stop "led-matrix@$USER.service"
sudo systemctl disable --now "led-matrix@$USER.service"
```

Use only the command you need. Stopping attempts a black frame; if the app/USB fails instead, the Nano's existing timeout clears the panel. Do not run the manual command and service simultaneously against the same Nano.

## 6. LAN access and security

- This app intentionally has **no authentication**. Anyone who can reach it can control brightness and the panel, change the serial connection, and load gallery images readable by the service user. Use only a trusted LAN and a non-root account. Keep port 8501 off the public internet; no router port forwarding is needed.
- `.streamlit/config.toml` and the service listen on `0.0.0.0:8501`. Browsers use the Pi's real IP/hostname, **not** `0.0.0.0`.
- Wi-Fi guest networks, AP/client isolation, VLAN rules, and host firewalls can block access even when both devices have internet access. Use the same trusted LAN or explicitly configured inter-network access.
- If you use a firewall, permit TCP 8501 from your trusted LAN subnet only. For UFW, an example is `sudo ufw allow from 192.168.1.0/24 to any port 8501 proto tcp`—**replace that subnet with yours**. Do not enable/reconfigure a firewall over SSH without preserving SSH access.
- The UI needs ordinary HTTP plus WebSockets on the same port, not WebRTC/UDP. Leave Streamlit's CORS/XSRF protections enabled.
- If a browser cannot connect, first try the Pi IP, verify service status/logs, then run `ss -ltn | grep ':8501'` on the Pi. If locally available, `curl http://127.0.0.1:8501/_stcore/health` should return `ok`. A healthy local server with failed remote access points to address/firewall/network issues.

## Updates and checks

Stop the service before replacing files or upgrading dependencies. In `~/led-matrix`:

```bash
sudo systemctl stop "led-matrix@$USER.service"
.venv/bin/python -m pip install --upgrade --only-binary=:all: -r requirements.txt
.venv/bin/python -m unittest discover -s tests -v
sudo systemctl start "led-matrix@$USER.service"
```

YouTube-specific fixes may only need `.venv/bin/python -m pip install -U "yt-dlp[default]"`, followed by a service restart. If private/age-restricted/region-blocked videos fail, that is not necessarily a Pi or LAN problem; no browser-cookie login is configured.

Before leaving it unattended, verify:

- Two different devices can load/control the UI without resetting output.
- Closing every tab leaves a repeating playlist playing.
- Stop video and Blackout work from another device.
- The Pi reboot starts the service and leaves the panel black.
- Correct orientation/settings are reapplied after restart.
- Unplugging USB makes the Nano clear the panel; reconnect explicitly afterward.
- Pi temperature/power and LED supply/wiring remain safe during sustained use. Expect roughly 10–13 panel fps at the current baud; changing hosts does not remove the Nano/LED timing limit.
