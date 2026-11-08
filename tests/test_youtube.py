import json
import subprocess
import time
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import av
from PIL import Image
import requests

from matrix_app.youtube import HTTPVideoReader, YouTubePlayer, playback_error, youtube_url


class EndLoop(Exception):
    pass


class YouTubeTests(unittest.TestCase):
    def setUp(self):
        with patch("matrix_app.youtube.threading.Thread.start"):
            self.player = YouTubePlayer()

    def run_command(self, repeat=False):
        self.player.configure("https://www.youtube.com/playlist?list=test", repeat)
        with patch.object(self.player.wake, "wait", side_effect=[True, EndLoop]):
            with self.assertRaises(EndLoop):
                self.player._run()

    def test_url_validation(self):
        self.assertEqual(youtube_url(" https://youtu.be/abc "), "https://youtu.be/abc")
        for value in ("http://youtube.com/watch?v=x", "https://youtube.com.evil.test/x",
                      "file:///etc/passwd", "--help"):
            with self.assertRaises(ValueError):
                youtube_url(value)

    def test_commands_clear_frames_and_reject_old_results(self):
        self.player.configure("https://youtu.be/a")
        version = self.player.version
        self.player.configure("https://youtu.be/a")
        self.assertEqual(self.player.version, version)
        self.player._publish(version, image=Image.new("RGB", (22, 22)), frame_time=time.monotonic())
        self.assertIsNotNone(self.player.snapshot()[0])
        self.player.configure()
        self.player._publish(version, image=Image.new("RGB", (22, 22)))
        self.assertIsNone(self.player.snapshot()[0])
        self.assertEqual(self.player.snapshot()[1], "Stopped")

    def test_stale_video_blacks_out(self):
        self.player.image = Image.new("RGB", (22, 22))
        self.player.frame_time = time.monotonic() - 3
        self.assertIsNone(self.player.snapshot()[0])

    def test_shutdown_cancels_work_and_rejects_late_frames(self):
        self.player.configure("https://youtu.be/a")
        version = self.player.version
        self.player.close()
        self.assertTrue(self.player._cancelled(version))
        self.player._publish(version, image=Image.new("RGB", (22, 22)))
        self.player.configure("https://youtu.be/b")
        self.assertIsNone(self.player.snapshot()[0])
        self.assertEqual(self.player.status, "Stopped")
        self.player._run()  # Returns immediately even with no browser or wake consumer.

    @patch("matrix_app.youtube.subprocess.Popen")
    def test_extraction_does_not_download_and_resolves_each_video(self, popen):
        process = popen.return_value
        process.returncode = 0
        process.communicate.return_value = (json.dumps({"url": "https://stream.test/video"}), "")
        process.poll.return_value = 0
        info = self.player._extract("https://youtu.be/a", 0)
        self.assertIn("url", info)
        args = popen.call_args.args[0]
        self.assertIn("--skip-download", args)
        self.assertIn("--no-playlist", args)
        self.assertIn("--ignore-config", args)
        self.assertIn("--check-formats", args)
        self.assertTrue(args[args.index("-f") + 1].startswith("bestvideo[height<=360][protocol=https]"))

    @patch("matrix_app.youtube.subprocess.Popen")
    def test_retry_excludes_denied_format_from_every_fallback(self, popen):
        process = popen.return_value
        process.returncode = 0
        process.communicate.return_value = ('{"url": "https://stream.test/video"}', "")
        process.poll.return_value = 0
        self.player._extract("https://youtu.be/a", 0, exclude=("18",))
        args = popen.call_args.args[0]
        formats = args[args.index("-f") + 1].split("/")
        self.assertTrue(all(fmt.endswith("[format_id!=18]") for fmt in formats))

    def test_403_retries_using_python_http(self):
        info = {"url": "https://stream.test/video", "format_id": "134"}
        with patch.object(self.player, "_extract", return_value=info), \
                patch.object(self.player, "_decode", side_effect=[av.error.HTTPForbiddenError(403, "Forbidden"), True]) as decode:
            self.assertTrue(self.player._play("https://youtu.be/a", 0))
        self.assertEqual(decode.call_count, 2)
        self.assertEqual(decode.call_args.kwargs, {"python_http": True})

    def test_403_on_both_clients_refreshes_url_and_excludes_format(self):
        first = {"url": "https://stream.test/old", "format_id": "18"}
        second = {"url": "https://stream.test/new", "format_id": "134"}
        response = requests.Response()
        response.status_code = 403
        denied = requests.HTTPError("Forbidden", response=response)
        with patch.object(self.player, "_extract", side_effect=[first, second]) as extract, \
                patch.object(self.player, "_decode", side_effect=[av.error.HTTPForbiddenError(403, "Forbidden"), denied, True]):
            self.assertTrue(self.player._play("https://youtu.be/a", 0))
        self.assertEqual(extract.call_args_list[0].kwargs["exclude"], ())
        self.assertEqual(extract.call_args_list[1].kwargs["exclude"], ("18",))

    def test_retries_are_bounded(self):
        info = {"url": "https://stream.test/video", "format_id": "18"}
        with patch.object(self.player, "_extract", return_value=info) as extract, \
                patch.object(self.player, "_decode", side_effect=av.error.HTTPForbiddenError(403, "Forbidden")) as decode:
            with self.assertRaises(av.error.HTTPForbiddenError):
                self.player._play("https://youtu.be/a", 0)
        self.assertEqual(extract.call_count, 2)
        self.assertEqual(decode.call_count, 4)

    def test_non_http_errors_are_not_retried(self):
        with patch.object(self.player, "_extract", return_value={"url": "https://stream.test/v"}), \
                patch.object(self.player, "_decode", side_effect=RuntimeError("bad video")) as decode:
            with self.assertRaisesRegex(RuntimeError, "bad video"):
                self.player._play("https://youtu.be/a", 0)
        self.assertEqual(decode.call_count, 1)

    def test_errors_do_not_expose_signed_media_urls(self):
        error = RuntimeError("Forbidden: 'https://googlevideo.com/playback?sig=secret' (403)")
        message = playback_error(error)
        self.assertNotIn("secret", message)
        self.assertIn("403", message)

    def test_http_reader_checks_cancellation_before_network_reads(self):
        raw = MagicMock()
        reader = HTTPVideoReader(raw, lambda: True)
        self.assertFalse(reader.seekable())
        with self.assertRaises(InterruptedError):
            reader.read(32768)
        raw.read.assert_not_called()

    @patch("matrix_app.youtube.subprocess.Popen")
    def test_cancelled_extraction_kills_process(self, popen):
        process = popen.return_value
        process.poll.return_value = None
        self.player.version = 1
        self.assertIsNone(self.player._extract("https://youtu.be/a", 0))
        process.kill.assert_called_once()
        process.communicate.assert_called_once()

    @patch("matrix_app.youtube.subprocess.Popen")
    def test_lookup_timeout_kills_process(self, popen):
        process = popen.return_value
        process.communicate.side_effect = [subprocess.TimeoutExpired("yt-dlp", 0.2), ("", "")]
        process.poll.return_value = None
        with patch("matrix_app.youtube.time.monotonic", side_effect=[0, 121]):
            with self.assertRaisesRegex(RuntimeError, "timed out"):
                self.player._extract("https://youtu.be/a", 0)
        process.kill.assert_called_once()

    def test_playlist_skips_unavailable_entries_and_finishes(self):
        listing = {"entries": [{"id": "a"}, None, {"id": "b"}]}
        with patch.object(self.player, "_extract", return_value=listing), \
                patch.object(self.player, "_play", side_effect=[RuntimeError("private"), True]) as play:
            self.run_command()
        self.assertEqual(play.call_count, 2)
        self.assertEqual(self.player.status, "Finished")

    def test_repeat_resolves_again_and_can_stop(self):
        listing = {"entries": [{"id": "a"}, {"id": "b"}]}
        calls = []

        def play(url, version):
            calls.append(url)
            if len(calls) == 4:
                self.player.configure()
            return True

        with patch.object(self.player, "_extract", return_value=listing), \
                patch.object(self.player, "_play", side_effect=play):
            self.run_command(repeat=True)
        self.assertEqual(calls[:2], calls[2:])
        self.assertEqual(self.player.status, "Stopped")

    def test_all_failed_does_not_spin_forever(self):
        with patch.object(self.player, "_extract", return_value={"entries": [{"id": "a"}]}), \
                patch.object(self.player, "_play", side_effect=RuntimeError("unavailable")) as play:
            self.run_command(repeat=True)
        self.assertEqual(play.call_count, 1)
        self.assertIn("No playable videos", self.player.status)

    def test_null_lookup_reports_failure_instead_of_loading_forever(self):
        with patch.object(self.player, "_extract", return_value=None):
            self.run_command()
        self.assertEqual(self.player.status, "YouTube playback failed")
        self.assertIn("No videos found", self.player.error)

    @patch("matrix_app.youtube.av.open")
    def test_decode_publishes_small_rgb_frame_and_closes_container(self, av_open):
        container = av_open.return_value.__enter__.return_value
        container.streams.video = [SimpleNamespace(average_rate=30)]
        frame = MagicMock()
        frame.time = 0
        frame.to_image.return_value = Image.new("RGB", (640, 360), "red")
        container.decode.return_value = [frame]
        with patch.object(self.player, "_extract", return_value={"url": "https://stream.test/v", "title": "Test"}):
            self.assertTrue(self.player._play("https://youtu.be/a", 0))
        image, status, error = self.player.snapshot()
        self.assertEqual(image.size, (320, 180))
        self.assertEqual(image.getpixel((0, 0)), (255, 0, 0))
        self.assertEqual(status, "Playing: Test")
        self.assertEqual(error, "")
        av_open.return_value.__exit__.assert_called_once()

    @patch("matrix_app.youtube.requests.get")
    @patch("matrix_app.youtube.av.open")
    def test_python_http_streams_with_original_headers_and_closes_response(self, av_open, get):
        response = get.return_value.__enter__.return_value
        container = av_open.return_value.__enter__.return_value
        container.streams.video = [SimpleNamespace(average_rate=30)]
        frame = MagicMock()
        frame.time = 0
        frame.to_image.return_value = Image.new("RGB", (32, 32))
        container.decode.return_value = [frame]
        info = {"url": "https://stream.test/v", "http_headers": {"User-Agent": "Test browser"}}
        self.assertTrue(self.player._decode(info, 0, python_http=True))
        get.assert_called_once_with(info["url"], headers=info["http_headers"], stream=True, timeout=(10, 5))
        self.assertIs(av_open.call_args.args[0].raw, response.raw)
        response.raise_for_status.assert_called_once()
        get.return_value.__exit__.assert_called_once()


if __name__ == "__main__":
    unittest.main()
