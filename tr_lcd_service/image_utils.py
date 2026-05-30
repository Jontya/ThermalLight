import numpy as np
from PIL import Image


def resize_image(img: Image.Image, width: int, height: int) -> Image.Image:
    """Resize to target resolution and convert to RGB."""
    return img.resize((width, height), Image.LANCZOS).convert('RGB')


def encode_rgb565(img: Image.Image) -> bytes:
    """Encode an RGB PIL image to packed big-endian RGB565 bytes."""
    arr = np.array(img, dtype=np.uint16)
    r, g, b = arr[:, :, 0], arr[:, :, 1], arr[:, :, 2]
    rgb565 = ((r & 0xF8) << 8) | ((g & 0xFC) << 3) | (b >> 3)
    return rgb565.astype('>u2').tobytes()

