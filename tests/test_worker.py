import unittest
from unittest.mock import MagicMock, patch

from PIL import Image

from matrix_app.protocol import FRAME_BYTES
from matrix_app.worker import MatrixWorker


class WorkerTests(unittest.TestCase):
    def setUp(self):
        with patch.dict("os.environ", MATRIX_SERIAL_PORT="", MATRIX_BAUD="250000"), \
                patch("threading.Thread.start"):
            self.worker = MatrixWorker()
        self.addCleanup(self.worker.close)

    def test_safe_defaults_and_reads_have_no_side_effects(self):
        before = self.worker.control_state()
        self.assertEqual(before["mode"], "Blackout")
        self.assertEqual(before["brightness"], 10)
        self.worker.snapshot()
        self.assertEqual(self.worker.control_state(), before)
        self.worker.configure(brightness=10)
        self.assertEqual(self.worker.control_state()["revision"], before["revision"])

    def test_youtube_continues_without_browser_heartbeat(self):
        self.worker.play_youtube("https://youtu.be/test")
        self.worker.youtube.snapshot = MagicMock(return_value=(Image.new("RGB", (32, 32), "red"), "Playing", ""))
        self.worker.youtube.configure = MagicMock()
        cfg = self.worker.control_state()
        for now in (1, 3600, 86400):
            image, error = self.worker._render(cfg, now)
            self.assertEqual(image.getpixel((0, 0)), (255, 0, 0))
            self.assertEqual(error, "")
        self.worker.youtube.configure.assert_called_with("https://youtu.be/test", True, 1)

    def test_blackout_cancels_youtube_and_renders_black(self):
        self.worker.play_youtube("https://youtu.be/test")
        self.worker.configure(mode="Blackout")
        image, error = self.worker._render(self.worker.control_state(), 1)
        self.assertEqual(image.tobytes(), bytes(FRAME_BYTES))
        self.assertIsNone(self.worker.youtube.command)
        self.assertEqual(error, "")

    def test_stop_clears_video_without_changing_mode(self):
        self.worker.play_youtube("https://youtu.be/test")
        self.worker.configure(youtube_url="")
        self.worker._render(self.worker.control_state(), 1)
        self.assertIsNone(self.worker.youtube.command)
        self.assertEqual(self.worker.control_state()["mode"], "YouTube")

    def test_drawing_is_shared_and_stale_results_are_rejected(self):
        self.worker.configure(mode="Drawing")
        red = Image.new("RGB", (22, 22), "red")
        self.assertTrue(self.worker.set_drawing(red, 0))
        self.assertEqual(self.worker.control_state()["draw_pixels"], ["#ff0000"] * 484)
        self.assertFalse(self.worker.set_drawing(Image.new("RGB", (22, 22), "blue"), 0))
        self.worker.clear_drawing()
        self.assertFalse(self.worker.set_drawing(red, 1))
        self.assertEqual(self.worker.control_state()["draw_pixels"], ["#000000"] * 484)

    @patch("matrix_app.worker.MatrixSerial")
    def test_env_autoconnect_starts_black_without_a_browser(self, serial):
        with patch.dict("os.environ", MATRIX_SERIAL_PORT="/dev/serial/by-id/nano", MATRIX_BAUD="250000"), \
                patch("threading.Thread.start"):
            worker = MatrixWorker()
        self.addCleanup(worker.close)
        with patch.object(worker.stop, "wait"):
            device, version = worker._output_frame(None, -1)
        serial.assert_called_once_with("/dev/serial/by-id/nano", 250000)
        device.send.assert_called_with(bytes(FRAME_BYTES))
        self.assertEqual(version, 0)

    @patch("matrix_app.worker.MatrixSerial")
    def test_transport_error_disconnects_without_blind_retry(self, serial):
        self.worker.connect("/dev/test", 250000)
        serial.return_value.send.side_effect = OSError("USB lost")
        with patch.object(self.worker.stop, "wait"):
            device, version = self.worker._output_frame(None, -1)
            device, version = self.worker._output_frame(device, version)
        self.assertIsNone(device)
        self.assertEqual(serial.call_count, 1)
        self.assertIn("Click Connect", self.worker.snapshot()[1])

    def test_shutdown_blacks_out_and_closes_serial(self):
        device = MagicMock()

        def frame(*args):
            self.worker.stop.set()
            return device, 0

        with patch.object(self.worker, "_output_frame", side_effect=frame):
            self.worker._run()
        device.send.assert_called_once_with(bytes(FRAME_BYTES))
        device.close.assert_called_once()
        self.assertTrue(self.worker.youtube.closing.is_set())


class LauncherTests(unittest.TestCase):
    @patch("matrix_app.__main__.cli.main")
    @patch("matrix_app.__main__.get_worker")
    def test_worker_starts_before_server_and_stops_on_exit(self, get_worker, cli):
        from matrix_app.__main__ import main

        def server(**kwargs):
            get_worker.assert_called_once()
            self.assertIn("--server.port=8502", kwargs["args"])

        cli.side_effect = server
        with patch("sys.argv", ["matrix_app", "--server.port=8502"]):
            main()
        get_worker.return_value.close.assert_called_once()


if __name__ == "__main__":
    unittest.main()
