"""A stop-and-wait protocol: never transmit while FastLED disables interrupts."""

import binascii
import time

import serial

WIDTH = HEIGHT = 22
FRAME_BYTES = WIDTH * HEIGHT * 3
MAGIC = b"MX22"


def packet(pixels: bytes) -> bytes:
    if len(pixels) != FRAME_BYTES:
        raise ValueError(f"Expected {FRAME_BYTES} RGB bytes")
    crc = binascii.crc_hqx(pixels, 0xFFFF)
    return MAGIC + pixels + crc.to_bytes(2, "little")


class MatrixSerial:
    def __init__(self, port: str, baud: int = 250000):
        self.device = serial.Serial(port, baud, timeout=0.2, write_timeout=2)
        try:
            # Opening a classic Nano usually resets it; allow time for its bootloader.
            deadline = time.monotonic() + 8
            while time.monotonic() < deadline:
                if self.device.readline().strip() == b"READY MX22 1":
                    break
                # Also handles adapters which do not auto-reset the Nano.
                self.device.write(b"?")
            else:
                raise TimeoutError("No Nano handshake. Check firmware, port, and baud rate.")
            self.device.reset_input_buffer()
        except Exception:
            self.device.close()
            raise

    def send(self, pixels: bytes) -> None:
        self.device.write(packet(pixels))
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            response = self.device.readline().strip()
            if response == b"OK":
                return
            if response.startswith(b"ERR"):
                raise IOError(f"Nano rejected frame: {response.decode(errors='replace')}")
        raise TimeoutError("Nano did not acknowledge the frame; reconnect before retrying.")

    def close(self) -> None:
        self.device.close()
