import unittest
from pathlib import Path
import tempfile
from unittest.mock import patch

from PIL import Image
from streamlit.testing.v1 import AppTest

from matrix_app.worker import MatrixWorker


class AppTests(unittest.TestCase):
    def setUp(self):
        with patch.dict("os.environ", MATRIX_SERIAL_PORT="", MATRIX_BAUD="250000"), \
                patch("threading.Thread.start"):
            self.worker = MatrixWorker()
        self.addCleanup(self.worker.close)
        self.factory = patch("matrix_app.worker.get_worker", return_value=self.worker)
        self.factory.start()
        self.addCleanup(self.factory.stop)

    def app(self):
        app = AppTest.from_file(str(Path(__file__).parents[1] / "app.py"), default_timeout=30).run()
        self.assertEqual(list(app.exception), [])
        return app

    def test_modes_without_hardware(self):
        app = self.app()
        self.assertNotIn("Camera", app.selectbox(key="mode").options)
        for mode in ("Clock", "YouTube", "Gallery", "Drawing", "Wiring test", "Blackout"):
            app.selectbox(key="mode").set_value(mode).run()
            self.assertEqual(list(app.exception), [], f"Mode failed: {mode}")
            self.assertEqual(self.worker.control_state()["mode"], mode)

    def test_blackout_button(self):
        app = self.app()
        app.selectbox(key="mode").set_value("Clock").run()
        next(button for button in app.button if button.label == "⬛ Blackout").click().run()
        self.assertEqual(app.selectbox(key="mode").value, "Blackout")
        self.assertEqual(self.worker.control_state()["mode"], "Blackout")
        self.assertEqual(list(app.exception), [])

    def test_youtube_play_validation_and_stop(self):
        app = self.app()
        app.selectbox(key="mode").set_value("YouTube").run()
        app.text_input(key="youtube_input").set_value("https://example.com/video")
        next(item for item in app.button if item.label == "Play / restart").click().run()
        self.assertEqual(len(app.error), 1)
        self.assertEqual(self.worker.control_state()["youtube_url"], "")
        app.text_input(key="youtube_input").set_value("https://youtu.be/test")
        next(item for item in app.button if item.label == "Play / restart").click().run()
        self.assertEqual(self.worker.control_state()["youtube_url"], "https://youtu.be/test")
        self.assertEqual(self.worker.control_state()["youtube_token"], 1)
        next(item for item in app.button if item.label == "Stop video").click().run()
        self.assertEqual(self.worker.control_state()["youtube_url"], "")
        self.assertEqual(list(app.exception), [])

    def test_opening_second_browser_does_not_reset_playback(self):
        self.worker.play_youtube("https://youtu.be/test")
        self.worker.configure(brightness=7, rotation=90)
        before = self.worker.control_state()
        first, second = self.app(), self.app()
        first.run()
        second.run()
        self.assertEqual(self.worker.control_state(), before)
        self.assertEqual(second.selectbox(key="mode").value, "YouTube")
        self.assertEqual(second.slider(key="brightness").value, 7)
        self.assertEqual(second.text_input(key="youtube_input").value, "https://youtu.be/test")

    def test_stale_browser_changes_only_the_field_user_touched(self):
        first, second = self.app(), self.app()
        first.selectbox(key="mode").set_value("Clock").run()
        first.slider(key="brightness").set_value(5).run()
        # Second browser still displays Blackout and brightness 10 until rerun.
        second.slider(key="fps").set_value(8).run()
        state = self.worker.control_state()
        self.assertEqual((state["mode"], state["brightness"], state["fps"]), ("Clock", 5, 8))
        self.assertEqual(second.selectbox(key="mode").value, "Clock")
        self.assertEqual(second.slider(key="brightness").value, 5)
        first.run()
        self.assertEqual(first.slider(key="fps").value, 8)

    def test_link_draft_survives_unrelated_remote_change(self):
        app = self.app()
        app.selectbox(key="mode").set_value("YouTube").run()
        app.text_input(key="youtube_input").set_value("https://youtu.be/draft").run()
        self.worker.configure(brightness=6)
        app.run()
        self.assertEqual(app.text_input(key="youtube_input").value, "https://youtu.be/draft")
        self.assertEqual(self.worker.control_state()["youtube_url"], "")

    def test_gallery_load_is_explicit_and_shared(self):
        app = self.app()
        app.selectbox(key="mode").set_value("Gallery").run()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "image.png"
            Image.new("RGB", (22, 22), "red").save(path)
            app.text_input(key="gallery_input").set_value(directory)
            next(item for item in app.button if item.label == "Load / refresh directory").click().run()
            self.assertEqual(self.worker.control_state()["gallery"], (str(path),))
            before = self.worker.control_state()
            second = self.app()
            self.assertEqual(second.text_input(key="gallery_input").value, directory)
            self.assertEqual(self.worker.control_state(), before)


if __name__ == "__main__":
    unittest.main()
