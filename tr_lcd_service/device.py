"""
Device communication for the Thermalright Elite Vision AIO LCD
(USB VID 0x87AD / PID 0x70DB).

The device is a vendor-class (0xFF) USB bulk device with two endpoints:
  EP 0x01  BULK OUT  512-byte max packet  -- image/command data host->device
  EP 0x81  BULK IN   512-byte max packet  -- not used for image display

Image transfer protocol (reverse-engineered from USB capture):
  - Single bulk OUT write: 64-byte header + raw RGB565 pixel data
  - No handshake or ACK required; device immediately renders received frame
  - Resolution is declared in the header, not queried from the device

Header format (all fields little-endian):
  0x00  4B  magic        = 0x12 0x34 0x56 0x78 (literal byte order)
  0x04  4B  command      = 3
  0x08  4B  width        (pixels)
  0x0C  4B  height       (pixels)
  0x10  40B reserved     = zeros
  0x38  4B  mode         = 2 (RGB565)
  0x3C  4B  data_size    = width * height * 2
"""

import logging
import struct

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

VID = 0x87AD
PID = 0x70DB
EP_OUT = 0x01
INTERFACE = 0
BULK_TIMEOUT_MS = 5000

DEFAULT_RESOLUTION = (320, 320)

_HEADER_MAGIC   = b'\x12\x34\x56\x78'
_HEADER_COMMAND = 3
_HEADER_MODE    = 2

# ---------------------------------------------------------------------------
# Backend
# ---------------------------------------------------------------------------

def _get_backend():
    """Return a libusb1 backend, preferring the bundled libusb PyPI package."""
    try:
        import usb.backend.libusb1
        try:
            import libusb
            backend = usb.backend.libusb1.get_backend(
                find_library=lambda _: libusb.dll._name
            )
            if backend is not None:
                return backend
        except Exception:
            pass
        return usb.backend.libusb1.get_backend()
    except ImportError:
        raise RuntimeError('pyusb not installed. Run: pip install pyusb libusb')


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def find_device():
    """Return the usb.core.Device for the LCD, or None if not connected."""
    import usb.core
    dev = usb.core.find(idVendor=VID, idProduct=PID, backend=_get_backend())
    if dev is None:
        logger.warning('Device VID=0x%04X PID=0x%04X not found', VID, PID)
    else:
        logger.info('Device found: VID=0x%04X PID=0x%04X', VID, PID)
    return dev


def open_device(dev) -> None:
    """Claim the device interface so we can write to it."""
    import usb.core
    try:
        if dev.is_kernel_driver_active(INTERFACE):
            dev.detach_kernel_driver(INTERFACE)
    except (NotImplementedError, usb.core.USBError):
        pass
    dev.set_configuration()
    logger.info('Device interface claimed')


def close_device(dev) -> None:
    """Release the device and free pyusb resources."""
    try:
        import usb.util
        usb.util.dispose_resources(dev)
        logger.info('Device released')
    except Exception as exc:
        logger.warning('Error releasing device: %s', exc)


def get_resolution(dev) -> tuple[int, int]:
    """Return the LCD resolution.

    This device does not have a resolution query protocol — the resolution
    is declared in the image header each frame.  Return the default observed
    during USB capture (320x320).  Override via config if needed.
    """
    return DEFAULT_RESOLUTION


def send_image(dev, rgb565_data: bytes, width: int = 320, height: int = 320) -> bool:
    """Push an RGB565 frame to the LCD via a single bulk OUT write.

    `rgb565_data` must be exactly width*height*2 bytes of raw RGB565 pixels
    (no padding required).  Returns True on success.
    """
    expected = width * height * 2
    if len(rgb565_data) != expected:
        logger.error('send_image: expected %d bytes, got %d', expected, len(rgb565_data))
        return False

    header = (
        _HEADER_MAGIC
        + struct.pack('<I', _HEADER_COMMAND)   # 0x04: command = 3
        + struct.pack('<I', width)             # 0x08: width
        + struct.pack('<I', height)            # 0x0C: height
        + bytes(40)                            # 0x10: reserved (40 bytes)
        + struct.pack('<I', _HEADER_MODE)      # 0x38: mode = 2
        + struct.pack('<I', expected)          # 0x3C: data size
    )   # total: 4+4+4+4+40+4+4 = 64 bytes

    try:
        dev.write(EP_OUT, header + rgb565_data, timeout=BULK_TIMEOUT_MS)
        logger.debug('Frame sent: %d bytes (header=64 data=%d)', 64 + expected, expected)
        return True
    except Exception as exc:
        logger.error('send_image failed: %s', exc)
        return False
