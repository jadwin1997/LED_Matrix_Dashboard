"""One process-wide worker owns the serial port, independent of UI reruns."""

import atexit
import os
import threading
import time
from datetime import datetime

from PIL import Image

from .protocol import MatrixSerial
from .render import blank, calibration_image, clock_image, physical_bytes, resize_image
from .youtube import YouTubePlayer


class MatrixWorker:
    def __init__(self):
        self.lock = threading.Lock()
        self.config = dict(mode="Blackout", brightness=10, rotation=0, flip_x=False,
                           flip_y=False, serpentine=True, axis="Rows", color_order="RGB",
                           fps=12, clock_color="#00ccff", use_24h=True, blink=True,
                           fit="Crop", gallery=(), interval=5.0, youtube_url="",
                           youtube_repeat=True, youtube_token=0,
                           gallery_directory="", gallery_recursive=False)
        self.port = os.environ.get("MATRIX_SERIAL_PORT", "").strip()
        self.baud = int(os.environ.get("MATRIX_BAUD", "250000"))
        self.youtube = YouTubePlayer()
        self.connection_version = 0
        self.revision = 0
        self.drawing_revision = 0
        self.drawing = blank()
        self.preview = blank()
        self.status = "Disconnected — preview only"
        self.render_error = ""
        self.actual_fps = 0.0
        self.stop = threading.Event()
        self._gallery_key = None
        self._gallery_started = time.monotonic()
        self._cached_path, self._cached_image = None, None
        self.thread = threading.Thread(target=self._run, daemon=True, name="matrix-output")
        self.thread.start()

    def configure(self, **changes):
        with self.lock:
            if any(self.config.get(key) != value for key, value in changes.items()):
                self.config.update(changes)
                self.revision += 1

    def control_state(self):
        """One consistent snapshot for all browsers; reading never changes output."""
        with self.lock:
            pixels = self.drawing.tobytes()
            return dict(self.config, revision=self.revision, port=self.port, baud=self.baud,
                        drawing_revision=self.drawing_revision,
                        draw_pixels=["#" + pixels[i:i + 3].hex() for i in range(0, len(pixels), 3)])

    def play_youtube(self, url):
        with self.lock:
            self.config.update(mode="YouTube", youtube_url=url,
                               youtube_token=self.config["youtube_token"] + 1)
            self.revision += 1

    def connect(self, port, baud):
        with self.lock:
            self.port, self.baud = port, baud
            self.connection_version += 1
            self.revision += 1
            self.status = "Connecting…" if port else "Disconnecting…"

    def set_drawing(self, image, expected_revision):
        with self.lock:
            # An old browser/canvas result must not overwrite a newer drawing.
            if expected_revision != self.drawing_revision or self.config["mode"] != "Drawing":
                return False
            if image.tobytes() == self.drawing.tobytes():
                return False
            self.drawing = image.copy()
            self.drawing_revision += 1
            self.revision += 1
            return True

    def clear_drawing(self):
        with self.lock:
            self.drawing = blank()
            self.drawing_revision += 1
            self.revision += 1

    def snapshot(self):
        with self.lock:
            return self.preview.copy(), self.status, self.render_error, self.actual_fps

    def close(self):
        self.stop.set()
        self.youtube.close()
        if self.thread.is_alive() and self.thread is not threading.current_thread():
            self.thread.join(timeout=3)

    def _render(self, cfg, now):
        self.youtube.configure(cfg["youtube_url"] if cfg["mode"] == "YouTube" else "",
                               cfg["youtube_repeat"], cfg["youtube_token"])
        key = (cfg["mode"], cfg["gallery"])
        if key != self._gallery_key:
            self._gallery_started, self._gallery_key = now, key
            self._cached_path = None
        mode = cfg["mode"]
        if mode == "Blackout":
            return blank(), ""
        if mode == "Clock":
            return clock_image(datetime.now().astimezone(), cfg["clock_color"],
                               cfg["use_24h"], cfg["blink"]), ""
        if mode == "YouTube":
            video, _, error = self.youtube.snapshot()
            return resize_image(video, cfg["fit"]) if video is not None else blank(), error
        if mode == "Gallery":
            paths = cfg["gallery"]
            if paths:
                index = int((now - self._gallery_started) / cfg["interval"]) % len(paths)
                path = paths[index]
                if path != self._cached_path:
                    with Image.open(path) as source:
                        self._cached_image = source.copy()
                    self._cached_path = path
                return resize_image(self._cached_image, cfg["fit"]), ""
            return blank(), ""
        if mode == "Drawing":
            with self.lock:
                return self.drawing.copy(), ""
        return (calibration_image() if mode == "Wiring test" else blank()), ""

    def _run(self):
        device = None
        version = -1
        try:
            while not self.stop.is_set():
                device, version = self._output_frame(device, version)
        finally:
            self.youtube.close()
            if device:
                try:
                    device.send(bytes(22 * 22 * 3))
                except Exception:
                    pass
                device.close()

    def _output_frame(self, device, version):
        started = time.monotonic()
        with self.lock:
            cfg = self.config.copy()
            requested_version = self.connection_version
            port, baud = self.port, self.baud
        if version != requested_version:
            if device:
                try:
                    device.send(bytes(22 * 22 * 3))
                except Exception:
                    pass
                device.close()
                device = None
            version = requested_version
            try:
                if port:
                    device = MatrixSerial(port, baud)
                with self.lock:
                    self.status = f"Connected: {port}" if device else "Disconnected — preview only"
            except Exception as exc:
                with self.lock:
                    self.status = f"Connection failed: {exc}"
        try:
            image, error = self._render(cfg, started)
        except Exception as exc:
            image = blank()
            error = f"Cannot render frame: {exc}"
        with self.lock:
            self.preview = image
            self.render_error = error
        if device:
            try:
                device.send(physical_bytes(image, **{key: cfg[key] for key in (
                    "brightness", "rotation", "flip_x", "flip_y", "serpentine", "axis", "color_order")}))
            except Exception as exc:
                device.close()
                device = None
                with self.lock:
                    self.status = f"Disconnected: {exc}. Click Connect to retry."
        delay = max(0, 1 / cfg["fps"] - (time.monotonic() - started))
        self.stop.wait(delay)
        with self.lock:
            self.actual_fps = 1 / max(0.001, time.monotonic() - started)
        return device, version


_worker = None
_worker_lock = threading.Lock()


def get_worker():
    """Shared by the headless launcher and every Streamlit session in this process."""
    global _worker
    with _worker_lock:
        if _worker is None:
            _worker = MatrixWorker()
            atexit.register(_worker.close)
        return _worker
