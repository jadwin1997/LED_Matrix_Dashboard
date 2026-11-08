"""Pure rendering and physical wiring transforms."""

from datetime import datetime

import numpy as np
from PIL import Image, ImageColor, ImageOps

from .protocol import HEIGHT, WIDTH

DIGITS = {
    "0": ["111", "101", "101", "101", "111"],
    "1": ["010", "110", "010", "010", "111"],
    "2": ["111", "001", "111", "100", "111"],
    "3": ["111", "001", "111", "001", "111"],
    "4": ["101", "101", "111", "001", "001"],
    "5": ["111", "100", "111", "001", "111"],
    "6": ["111", "100", "111", "101", "111"],
    "7": ["111", "001", "010", "010", "010"],
    "8": ["111", "101", "111", "101", "111"],
    "9": ["111", "101", "111", "001", "111"],
}


def blank() -> Image.Image:
    return Image.new("RGB", (WIDTH, HEIGHT))


def resize_image(image: Image.Image, fit: str = "Crop") -> Image.Image:
    image = ImageOps.exif_transpose(image).convert("RGBA")
    background = Image.new("RGBA", image.size, "black")
    image = Image.alpha_composite(background, image).convert("RGB")
    if fit == "Crop":
        return ImageOps.fit(image, (WIDTH, HEIGHT), method=Image.Resampling.LANCZOS)
    return ImageOps.pad(image, (WIDTH, HEIGHT), method=Image.Resampling.LANCZOS, color="black")


def clock_image(now: datetime, color: str, use_24h: bool, blink: bool) -> Image.Image:
    image = blank()
    rgb = ImageColor.getrgb(color)
    text = now.strftime("%H%M" if use_24h else "%I%M")
    for char, x in zip(text, (2, 6, 12, 16)):
        for y, row in enumerate(DIGITS[char]):
            for dx, value in enumerate(row):
                if value == "1":
                    for dy in (0, 1):
                        image.putpixel((x + dx, 6 + y * 2 + dy), rgb)
    if not blink or now.second % 2 == 0:
        for y in (8, 9, 12, 13):
            image.putpixel((10, y), rgb)
    # A bottom-right dot denotes PM when using a 12-hour clock.
    if not use_24h and now.hour >= 12:
        image.putpixel((20, 20), rgb)
    return image


def calibration_image() -> Image.Image:
    image = blank()
    for x in range(WIDTH):
        image.putpixel((x, 0), (80, 80, 80))
    for y in range(HEIGHT):
        image.putpixel((0, y), (80, 80, 80))
    for point, color in [((0, 0), "red"), ((21, 0), "lime"),
                         ((0, 21), "blue"), ((21, 21), "white")]:
        image.putpixel(point, ImageColor.getrgb(color))
    for x in range(1, 21):
        image.putpixel((x, 10), (x * 10, 0, 0))
        image.putpixel((x, 11), (0, x * 10, 0))
    return image


def physical_bytes(image: Image.Image, *, brightness: int = 10, rotation: int = 0,
                   flip_x: bool = False, flip_y: bool = False,
                   serpentine: bool = True, axis: str = "Rows",
                   color_order: str = "RGB") -> bytes:
    pixels = np.asarray(image.convert("RGB"), dtype=np.uint8)
    pixels = np.rot90(pixels, k=-(rotation // 90))
    if flip_x:
        pixels = pixels[:, ::-1]
    if flip_y:
        pixels = pixels[::-1]
    if axis == "Columns":
        pixels = pixels.transpose(1, 0, 2)
    pixels = pixels.copy()
    if serpentine:
        pixels[1::2] = pixels[1::2, ::-1]
    pixels = pixels[..., ["RGB".index(channel) for channel in color_order]]
    pixels = (pixels.astype(np.uint16) * max(0, min(100, brightness)) // 100).astype(np.uint8)
    return pixels.tobytes()
