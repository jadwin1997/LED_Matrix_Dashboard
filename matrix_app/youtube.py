"""Resolve YouTube links with yt-dlp and decode in a cancellable background worker."""

import json
from contextlib import ExitStack
import io
from pathlib import Path
import re
import shutil
import subprocess
import sys
import threading
import time
from urllib.parse import urlparse

import av
import requests


# Small, direct video-only streams avoid YouTube's often-restricted legacy
# combined stream (itag 18). Keep combined/HLS formats as fallbacks.
VIDEO_FORMATS = (
    "bestvideo[height<=360][protocol=https]",
    "best[height<=360][protocol=https]",
    "bestvideo[height<=360]",
    "best[height<=360]",
    "bestvideo",
    "best",
)


def playback_error(exc):
    # FFmpeg includes the entire signed media URL in errors. Don't expose it.
    return re.sub(r"https?://[^\s'\"]+", "[stream URL]", str(exc))[-1000:]


def access_denied(exc):
    if isinstance(exc, (av.error.HTTPForbiddenError, av.error.HTTPNotFoundError)):
        return True
    return (isinstance(exc, requests.HTTPError) and exc.response is not None
            and exc.response.status_code in (403, 404, 410))


class HTTPVideoReader(io.RawIOBase):
    """A non-seekable, bounded-memory HTTP source with cancellation checks."""

    def __init__(self, raw, cancelled):
        super().__init__()
        self.raw = raw
        self.cancelled = cancelled

    def readable(self):
        return True

    def read(self, size=-1):
        if self.cancelled():
            raise InterruptedError("Playback stopped")
        return self.raw.read(size)


def youtube_url(value):
    value = value.strip()
    parsed = urlparse(value)
    if parsed.scheme != "https" or parsed.hostname not in {
        "youtube.com", "www.youtube.com", "m.youtube.com", "youtu.be", "www.youtu.be",
    }:
        raise ValueError("Enter an https:// YouTube video or playlist link.")
    return value


class YouTubePlayer:
    def __init__(self):
        self.lock = threading.Lock()
        self.wake = threading.Event()
        self.closing = threading.Event()
        self.command = None
        self.version = 0
        self.image = None
        self.frame_time = 0.0
        self.status = "Stopped"
        self.error = ""
        threading.Thread(target=self._run, daemon=True, name="youtube-video").start()

    def configure(self, url="", repeat=True, token=0):
        command = (url, repeat, token) if url else None
        with self.lock:
            if self.closing.is_set() or command == self.command:
                return
            self.command = command
            self.version += 1
            self.image = None
            self.error = ""
            self.status = "Loading YouTube…" if command else "Stopped"
            self.wake.set()

    def snapshot(self):
        with self.lock:
            image = self.image if time.monotonic() - self.frame_time < 2 else None
            return image, self.status, self.error

    def _cancelled(self, version):
        with self.lock:
            return self.closing.is_set() or version != self.version

    def close(self):
        with self.lock:
            self.closing.set()
            self.version += 1
            self.image = None
            self.status = "Stopped"
            self.wake.set()

    def _publish(self, version, **changes):
        with self.lock:
            if version == self.version:
                for key, value in changes.items():
                    setattr(self, key, value)

    def _extract(self, url, version, flat=False, exclude=()):
        args = [sys.executable, "-m", "yt_dlp", "--ignore-config", "--no-cache-dir",
                "--dump-single-json", "--skip-download", "--no-warnings",
                "--socket-timeout", "10", "--retries", "1"]
        # Find the venv's bundled runtime even when launched without activating it.
        runtime = shutil.which("deno", path=str(Path(sys.executable).parent)) or shutil.which("deno")
        if runtime:
            args += ["--js-runtimes", f"deno:{runtime}"]
        if flat:
            args += ["--flat-playlist", "--ignore-errors"]
        else:
            filters = "".join(f"[format_id!={format_id}]" for format_id in exclude)
            formats = "/".join(fmt + filters for fmt in VIDEO_FORMATS)
            # Validate with yt-dlp's HTTP client before handing a URL to FFmpeg.
            args += ["--no-playlist", "--check-formats", "-f", formats]
        process = subprocess.Popen(args + ["--", url], stdout=subprocess.PIPE,
                                   stderr=subprocess.PIPE, text=True)
        deadline = time.monotonic() + 120
        try:
            while not self._cancelled(version):
                try:
                    output, error = process.communicate(timeout=0.2)
                    if process.returncode:
                        raise RuntimeError(error.strip()[-1500:] or "YouTube could not resolve this link.")
                    return json.loads(output)
                except subprocess.TimeoutExpired:
                    if time.monotonic() > deadline:
                        raise RuntimeError("YouTube lookup timed out. Try again.")
            return None
        finally:
            if process.poll() is None:
                process.kill()
            process.communicate()

    def _play(self, url, version):
        excluded = []
        for attempt in range(2):
            info = self._extract(url, version, exclude=tuple(excluded))
            if not info or self._cancelled(version):
                return False
            try:
                return self._decode(info, version)
            except Exception as exc:
                if not access_denied(exc) or self._cancelled(version):
                    raise
            # A valid browser/yt-dlp URL can be rejected when FFmpeg requests it.
            # Stream through Python's HTTP client instead; still no video files.
            self._publish(version, image=None, status="Retrying with browser-style HTTP…")
            try:
                return self._decode(info, version, python_http=True)
            except Exception as exc:
                if (attempt == 1 or self._cancelled(version)
                        or not isinstance(exc, (requests.RequestException, av.FFmpegError))):
                    raise
                if info.get("format_id"):
                    excluded.append(info["format_id"])
                self._publish(version, image=None, status="Refreshing YouTube link / trying another format…")
        return False

    def _decode(self, info, version, python_http=False):
        stream_url = info.get("url")
        if not stream_url:
            raise RuntimeError("No playable video stream found.")
        http_headers = info.get("http_headers", {})
        headers = "".join(f"{key}: {value}\r\n" for key, value in http_headers.items())
        self._publish(version, status=f"Playing: {info.get('title', 'YouTube video')}", error="")
        # Bounded network reads keep stopping/mode changes responsive even on stalls.
        with ExitStack() as stack:
            source, options = stream_url, {"headers": headers}
            if python_http:
                response = stack.enter_context(requests.get(
                    stream_url, headers=http_headers, stream=True, timeout=(10, 5)))
                response.raise_for_status()
                response.raw.decode_content = True
                source = stack.enter_context(HTTPVideoReader(response.raw, lambda: self._cancelled(version)))
                options = {}
            container = stack.enter_context(av.open(source, options=options, timeout=(10, 5)))
            video = container.streams.video[0]
            first_pts = None
            started = time.monotonic()
            last_output = -1.0
            frame_index = 0
            source_fps = float(video.average_rate or 30)
            decoded = False
            for frame in container.decode(video):
                if self._cancelled(version):
                    return False
                pts = float(frame.time) if frame.time is not None else frame_index / source_fps
                frame_index += 1
                if first_pts is None:
                    first_pts = pts
                elapsed = pts - first_pts
                delay = started + elapsed - time.monotonic()
                if delay > 0 and self.wake.wait(delay):
                    return False
                # Decode at source speed, but only convert at most 20 frames/sec.
                if elapsed - last_output < 0.05:
                    continue
                last_output = elapsed
                image = frame.to_image()
                image.thumbnail((320, 320))
                self._publish(version, image=image, frame_time=time.monotonic())
                decoded = True
            return decoded

    def _run(self):
        while not self.closing.is_set():
            self.wake.wait()
            if self.closing.is_set():
                return
            with self.lock:
                self.wake.clear()
                command, version = self.command, self.version
            if not command:
                continue
            url, repeat, _ = command
            try:
                listing = self._extract(youtube_url(url), version, flat=True)
                if self._cancelled(version):
                    continue
                if not listing:
                    raise RuntimeError("No videos found. The link may be unavailable or private.")
                entries = listing.get("entries") if "entries" in listing else [listing]
                links = []
                for entry in entries or []:
                    if entry and entry.get("id"):
                        links.append("https://www.youtube.com/watch?v=" + entry["id"])
                if not links:
                    raise RuntimeError("No videos found. The playlist may be private or empty.")
                while not self._cancelled(version):
                    played = False
                    for index, link in enumerate(links, 1):
                        if self._cancelled(version):
                            break
                        self._publish(version, image=None, status=f"Loading video {index}/{len(links)}…")
                        try:
                            played = self._play(link, version) or played
                        except Exception as exc:
                            self._publish(version, error=f"Skipping video {index}: {playback_error(exc)}")
                    if not played:
                        self._publish(version, image=None, status="No playable videos. Press Play / restart to retry.")
                        break
                    if not repeat:
                        self._publish(version, image=None, status="Finished")
                        break
            except Exception as exc:
                self._publish(version, image=None, status="YouTube playback failed", error=playback_error(exc))
