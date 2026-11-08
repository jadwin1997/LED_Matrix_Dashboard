import binascii
import unittest
from datetime import datetime
from unittest.mock import patch

import numpy as np
from PIL import Image

from matrix_app.protocol import FRAME_BYTES, MAGIC, MatrixSerial, packet
from matrix_app.render import blank, calibration_image, clock_image, physical_bytes, resize_image


class RenderTests(unittest.TestCase):
    def test_frame_size_and_blackout(self):
        self.assertEqual(physical_bytes(blank()), bytes(FRAME_BYTES))
        self.assertEqual(len(physical_bytes(calibration_image())), FRAME_BYTES)

    def test_serpentine(self):
        image = blank()
        image.putpixel((0, 1), (255, 0, 0))
        pixels = np.frombuffer(physical_bytes(image, brightness=100), np.uint8).reshape(484, 3)
        self.assertEqual(pixels[43].tolist(), [255, 0, 0])
        self.assertEqual(pixels[22].tolist(), [0, 0, 0])

    def test_column_wiring(self):
        image = blank()
        image.putpixel((1, 0), (0, 255, 0))
        pixels = np.frombuffer(physical_bytes(image, brightness=100, axis="Columns"), np.uint8).reshape(484, 3)
        self.assertEqual(pixels[43].tolist(), [0, 255, 0])

    def test_rotation_and_flip(self):
        image = blank()
        image.putpixel((0, 0), (255, 0, 0))
        pixels = np.frombuffer(physical_bytes(image, brightness=100, rotation=90, serpentine=False), np.uint8).reshape(22, 22, 3)
        self.assertEqual(pixels[0, 21].tolist(), [255, 0, 0])
        pixels = np.frombuffer(physical_bytes(image, brightness=100, flip_y=True, serpentine=False), np.uint8).reshape(22, 22, 3)
        self.assertEqual(pixels[21, 0].tolist(), [255, 0, 0])

    def test_brightness_and_channel_correction(self):
        image = Image.new("RGB", (22, 22), (200, 100, 50))
        self.assertEqual(physical_bytes(image, brightness=10, color_order="GRB")[:3], bytes([10, 20, 5]))

    def test_fit_and_transparency(self):
        image = Image.new("RGBA", (50, 10), (255, 0, 0, 255))
        self.assertEqual(resize_image(image, "Crop").size, (22, 22))
        self.assertEqual(resize_image(image, "Fit").getpixel((0, 0)), (0, 0, 0))
        transparent = Image.new("RGBA", (22, 22), (255, 255, 255, 0))
        self.assertEqual(resize_image(transparent).getpixel((0, 0)), (0, 0, 0))

    def test_clock_blink_and_pm(self):
        now = datetime(2026, 11, 1, 13, 42, 1)
        image = clock_image(now, "#123456", False, True)
        self.assertEqual(image.getpixel((10, 8)), (0, 0, 0))
        self.assertEqual(image.getpixel((20, 20)), (18, 52, 86))
        image = clock_image(now, "#123456", True, False)
        self.assertEqual(image.getpixel((10, 8)), (18, 52, 86))
        self.assertEqual(image.getpixel((20, 20)), (0, 0, 0))


class ProtocolTests(unittest.TestCase):
    def test_packet(self):
        payload = bytes(range(256)) * 5 + bytes(range(172))
        self.assertEqual(len(payload), FRAME_BYTES)
        encoded = packet(payload)
        self.assertEqual(encoded[:4], MAGIC)
        self.assertEqual(encoded[4:-2], payload)
        self.assertEqual(int.from_bytes(encoded[-2:], "little"), binascii.crc_hqx(payload, 0xFFFF))
        self.assertEqual(binascii.crc_hqx(b"123456789", 0xFFFF), 0x29B1)

    def test_invalid_size(self):
        with self.assertRaises(ValueError):
            packet(b"short")

    @patch("matrix_app.protocol.serial.Serial")
    def test_handshake_and_acknowledgment(self, serial_mock):
        device = serial_mock.return_value
        device.readline.side_effect = [b"boot noise\n", b"READY MX22 1\n", b"OK\n"]
        connection = MatrixSerial("fake")
        connection.send(bytes(FRAME_BYTES))
        self.assertEqual(device.write.call_args.args[0], packet(bytes(FRAME_BYTES)))
        connection.close()
        device.close.assert_called_once()

    @patch("matrix_app.protocol.serial.Serial")
    def test_rejected_frame(self, serial_mock):
        serial_mock.return_value.readline.side_effect = [b"READY MX22 1\n", b"ERR crc\n"]
        connection = MatrixSerial("fake")
        with self.assertRaises(IOError):
            connection.send(bytes(FRAME_BYTES))
        connection.close()


if __name__ == "__main__":
    unittest.main()
