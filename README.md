# 22×22 LED matrix controller

Streamlit controller and Arduino firmware for a **classic ATmega328P Nano**, **484 WS2812B LEDs**, and **DIN on D3**. A Raspberry Pi 5 renders frames and sends RGB pixels to the Nano over USB. Phones and laptops control the same panel through a LAN browser; the Pi keeps running playback after every browser closes.

**Moving from the PC/WSL version? Follow [the Raspberry Pi transition guide](docs/raspberry-pi.md)** for copying the project, fresh ARM dependencies, USB setup, LAN access, and automatic startup.

## Features

- YouTube video/playlist streaming with optional repeat; no manually downloaded or saved video files, no audio.
- Clock: Pi local time, color, 12/24-hour display, blinking colon.
- Gallery: local PNG/JPEG/WebP/BMP/GIF directory, crop/fit, adjustable interval. GIFs show their first frame.
- Shared 22×22 drawing canvas with brush, eraser, and clear; no JavaScript build tools.
- Brightness, blackout, logical preview, wiring test, orientation and wiring transforms.
- Shared controls across browsers: opening a page does not reset playback. The latest deliberate setting change wins; other pages refresh automatically.
- Preview-only operation without a Nano.

Camera/WebRTC support has been removed. LAN HTTP is sufficient; no camera permissions or HTTPS certificates are needed.

## Wiring and power — check this first

- Nano **D3 → panel DIN** (not DOUT), ideally through a **330–470 Ω series resistor** near the data input.
- External **regulated 5V** supply → panel 5V/GND. Verify the panel's voltage label and connector pinout; do not rely on wire colors.
- **Nano GND → supply/panel GND**. A common ground is essential.
- Power the Nano from the Pi's USB. **Do not power the panel through the Pi, Nano, USB port, or a breadboard.** Avoid tying the external supply's +5V to the Nano's USB-powered 5V rail unless you have designed appropriate power isolation.
- A roughly **1000 µF capacitor rated at least 6.3V** across the panel's supply input is commonly recommended; observe polarity.
- Use short data wiring, correctly rated supply wires/connectors, appropriate fusing, and additional power injection as required by your panel. Power down before changing wiring; do not drive DIN into an unpowered panel.

484 older-style WS2812Bs can theoretically approach **29 A at full white** (60 mA/pixel). Actual revisions vary. Default brightness is 10%, but **software brightness is not a guaranteed current limit**. Choose a brightness limit that your supply, wiring, connectors, and panel can safely handle. Do not test full white at high brightness casually.

**Closing browsers no longer blacks out the panel.** Use **Blackout** to turn it off. The firmware still clears the panel after approximately three seconds without valid frames if the Pi/app/USB fails. Shutdown also attempts a black frame. These are convenience safeguards, not electrical protection.

## Firmware

The existing firmware does not need changing for the Pi. If uploading for the first time:

1. Install Arduino IDE and the **FastLED** library on your development PC.
2. Open `firmware/led_matrix/led_matrix.ino`.
3. Select **Arduino Nano**, processor **ATmega328P**, and its serial port. Some clones need **ATmega328P (Old Bootloader)** and/or a CH340 driver.
4. Upload, close Serial Monitor and other serial tools, then attach the Nano to the Pi.

Defaults: **D3**, **250000 baud**, WS2812B **GRB** wire order. The host sends RGB; leave channel correction at RGB unless the wiring test shows incorrect colors. Firmware and app baud must match.

## Run manually

Use Python **3.11 or newer** on 64-bit Raspberry Pi OS or another supported host. From the project directory:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
python -m matrix_app
```

The included `.streamlit/config.toml` listens on all interfaces, port **8501**. Visit **`http://<host-LAN-IP>:8501`** from another device. `localhost` only works on the host itself. For strictly local testing, override with `python -m matrix_app --server.address=127.0.0.1`.

Set `MATRIX_SERIAL_PORT` to the Nano's stable `/dev/serial/by-id/…` path for automatic connection; `MATRIX_BAUD` defaults to 250000. Without a port, the app starts in preview-only mode and you can connect through the sidebar. The `deploy/matrix.env` file is loaded by the systemd service only, not automatically by manual launches.

`python -m matrix_app` starts the worker before any browser connects. The older `python -m streamlit run app.py` command also works, but does not initialize the worker until the first browser visits. **Run one process only per panel.** Do not run a manual instance alongside the service.

## Shared controls and unattended operation

All connected browsers operate one shared panel with no accounts, ownership locks, or per-user queues. Only explicit user actions change the worker; page loads, previews, and refreshes do not. Setting changes are synchronized to open pages approximately every half-second. Link/path/serial-port drafts are local until Play, Load, or Connect is pressed. Stale drawings are rejected rather than overwriting another user's newer canvas.

Playlists, clock, gallery, and drawings continue without any browser. **Stop video** stops YouTube; **Blackout** turns the panel off; changing modes switches output. Settings and drawings remain in memory only. **After an app restart or Pi reboot the panel starts black**, brightness resets to 10%, and orientation/settings must be reapplied; playlists do not automatically resume. The environment-configured serial port is the exception: it is reconnected at startup.

**Security:** this is a trusted-LAN tool, not an internet service. There is no authentication; anyone who can reach port 8501 can change the lights, brightness, serial connection, and read supported gallery images accessible to the service account. Do not port-forward it, disable Streamlit's security protections, or use it on an untrusted/public network. Use a non-root service user and host/network firewall restrictions where appropriate.

### YouTube

Select **YouTube**, enter an `https://` YouTube video or playlist link, and press **Play / restart**. Repeat defaults to on. A video link containing `list=…` plays the playlist; omit `list` to play only one video. **Crop** fills the panel; **Fit** preserves the whole image with black bars.

yt-dlp resolves links; PyAV decodes in memory. Dependencies include a venv-local Deno runtime and yt-dlp challenge-solver scripts; no separate system FFmpeg or JavaScript installation is required when using supported wheels. Small video-only streams are preferred and checked for access. HTTP 403/404 failures retry through Python HTTP, then refresh the link and try another format once. Signed media URLs are hidden from errors. Unavailable entries are skipped; if all entries fail, playback stops rather than retrying forever. Stream URLs refresh for each video and repeat.

Switching away stops playback; returning to YouTube restarts from the beginning. Changing Repeat also restarts playback. Network operations can take several seconds to cancel, but do not block panel blackout. Closing browsers has no effect on playback. The Pi, app, network, and power must remain available.

YouTube changes frequently. Update with `python -m pip install -U "yt-dlp[default]"` inside the venv, then restart the app/service. Private, age-restricted, region-blocked, members-only videos, and bot/sign-in checks may prevent access. Browser cookies/account login are not configured. Use only content you are authorized to stream and comply with YouTube's terms.

### Other modes

- **Clock:** uses the host timezone, not the browser timezone. Check `date`; set with `sudo timedatectl set-timezone America/New_York` (substitute yours). A bottom-right dot indicates PM in 12-hour mode.
- **Gallery:** enter a directory on the **Pi**, e.g. `/home/youruser/Pictures`; files on another device must be copied there first. Press **Load / refresh directory** after changes. Corrupt/unreadable images show black and a warning; the slideshow advances at the next interval. Large recursive directories may take time to scan.
- **Drawing:** drag or touch to paint. The shared drawing stays in the Pi process when browsers close, but is not saved across restarts. Export/save is not included.
- **Wiring test:** start at low brightness, adjust orientation/wiring until the physical panel matches the preview. Preview shows the logical image **before brightness and physical wiring transforms**.

## Performance and protocol

The Nano has **2 KB SRAM**; the firmware keeps one 1,452-byte LED buffer, never two. Compiled with Arduino AVR core 1.8.8 and FastLED 3.10.9, the sketch uses **5,588 bytes flash and 1,760 bytes static RAM**, leaving **288 bytes for the stack**. Arduino therefore warns about low memory. Avoid adding another image buffer or Arduino `String` objects.

At 250000 baud, the RGB payload takes about 58 ms; updating 484 WS2812Bs takes about 15 ms. Practical output is around **10–13 fps**, with a default target of 12. A Pi 5 does not remove this USB/Nano/LED limit. Higher baud requires firmware changes and adapter testing.

Packets: `MX22` + 1,452 RGB bytes + little-endian CRC-16/CCITT-FALSE (initial `0xFFFF`). The Nano prints `READY MX22 1` on startup or when idle and queried with `?`; it prints `OK` after LED updating or `ERR …` for an invalid frame. Python waits for each acknowledgment, avoiding traffic while FastLED disables interrupts. CRC failures leave the prior frame untouched. Transport errors disconnect rather than blindly retrying; use **Connect** after fixing the device.

## Tests

```bash
.venv/bin/python -m unittest discover -s tests -v
```

Tests cover protocol/rendering, shared browser controls, non-mutating page loads, stale-session updates, browser-independent output, safe startup/autoconnection, shutdown, and YouTube streaming/cancellation/retry behavior with mocked external access. Actual Pi installation, LAN access, video performance, power, wiring, and USB timing must still be verified on your hardware.
