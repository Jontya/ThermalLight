import struct
from PIL import Image


def resize_image(img: Image.Image, width: int, height: int) -> Image.Image:
    """Resize to target resolution and convert to RGB."""
    return img.resize((width, height), Image.LANCZOS).convert('RGB')


def encode_rgb565(img: Image.Image) -> bytes:
    """Encode an RGB PIL image to packed little-endian BGR565 bytes.

    Device uses BGR channel order: ((B & 0xF8) << 8) | ((G & 0xFC) << 3) | (R >> 3)
    """
    w, h = img.size
    pixels = img.load()
    buf = bytearray(w * h * 2)
    idx = 0
    for y in range(h):
        for x in range(w):
            r, g, b = pixels[x, y]
            val = ((b & 0xF8) << 8) | ((g & 0xFC) << 3) | (r >> 3)
            struct.pack_into('<H', buf, idx, val)
            idx += 2
    return bytes(buf)


def pad_to_512(data: bytes) -> bytes:
    """Pad data with zero bytes to the next 512-byte boundary."""
    remainder = len(data) % 512
    if remainder:
        data += b'\x00' * (512 - remainder)
    return data
