"""
Device discovery, HID handshake, and SCSI frame delivery for the
Thermalright Elite Vision AIO LCD (USB VID 0x0416 / USBDISPLAY).

Mass-storage I/O uses IOCTL_SCSI_PASS_THROUGH_DIRECT with a 16-byte
vendor CDB (opcode 0xF5).  HID is used only for the one-time handshake
that detects the screen resolution.
"""

import ctypes
import ctypes.wintypes
import logging
import struct

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Win32 DLLs
# ---------------------------------------------------------------------------
kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)
setupapi  = ctypes.WinDLL('setupapi',  use_last_error=True)
hid_dll   = ctypes.WinDLL('hid',       use_last_error=True)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
GENERIC_READ       = 0x80000000
GENERIC_WRITE      = 0x40000000
FILE_SHARE_NONE    = 0
OPEN_EXISTING      = 3
INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value

# IOCTL_SCSI_PASS_THROUGH_DIRECT
# CTL_CODE(FILE_DEVICE_CONTROLLER=4, 0x0405, METHOD_OUT_DIRECT=2,
#          FILE_READ_ACCESS|FILE_WRITE_ACCESS=3)
IOCTL_SCSI_PASS_THROUGH_DIRECT = 0x4D014

SCSI_IOCTL_DATA_OUT = 0   # host → device
SENSE_BUFFER_SIZE   = 32

DIGCF_PRESENT         = 0x00000002
DIGCF_DEVICEINTERFACE = 0x00000010

GUID_DEVINTERFACE_DISK = '{53F56307-B6BF-11D0-94F2-00A0C91EFB8B}'
GUID_DEVINTERFACE_HID  = '{4D1E55B2-F16F-11CF-88CB-001111000030}'

TARGET_VID  = 0x0416
TARGET_DESC = 'USBDISPLAY'

# FBL byte value → (width, height).  Extend if your firmware returns a
# different byte; the raw response is logged so you can identify the value.
_FBL_MAP: dict[int, tuple[int, int]] = {
    0x01: (240, 240),
    0x02: (320, 240),
    0x03: (480, 480),
    0x04: (800, 480),
    0x05: (480, 320),
    0x06: (320, 320),
}
DEFAULT_RESOLUTION = (240, 240)

# ---------------------------------------------------------------------------
# ctypes structures
# ---------------------------------------------------------------------------

class _GUID(ctypes.Structure):
    _fields_ = [
        ('Data1', ctypes.c_ulong),
        ('Data2', ctypes.c_ushort),
        ('Data3', ctypes.c_ushort),
        ('Data4', ctypes.c_ubyte * 8),
    ]

    @classmethod
    def from_str(cls, s: str) -> '_GUID':
        s = s.strip('{}')
        p = s.split('-')
        g = cls()
        g.Data1 = int(p[0], 16)
        g.Data2 = int(p[1], 16)
        g.Data3 = int(p[2], 16)
        b = bytes.fromhex(p[3] + p[4])
        for i in range(8):
            g.Data4[i] = b[i]
        return g


class _SP_DEVICE_INTERFACE_DATA(ctypes.Structure):
    _fields_ = [
        ('cbSize',              ctypes.c_ulong),
        ('InterfaceClassGuid', _GUID),
        ('Flags',               ctypes.c_ulong),
        ('Reserved',            ctypes.c_size_t),
    ]


class _HIDD_ATTRIBUTES(ctypes.Structure):
    _fields_ = [
        ('Size',          ctypes.c_ulong),
        ('VendorID',      ctypes.c_ushort),
        ('ProductID',     ctypes.c_ushort),
        ('VersionNumber', ctypes.c_ushort),
    ]


class _SCSI_PASS_THROUGH_DIRECT(ctypes.Structure):
    """Maps to SCSI_PASS_THROUGH_DIRECT from ntddscsi.h.

    ctypes inserts natural-alignment padding automatically:
      - 3 bytes after DataIn  (to align DataTransferLength to offset 12)
      - 4 bytes after TimeOut (to align DataBuffer pointer to offset 24 on x64)
    Resulting sizeof = 56 on 64-bit, 44 on 32-bit.
    """
    _fields_ = [
        ('Length',              ctypes.c_uint16),
        ('ScsiStatus',          ctypes.c_uint8),
        ('PathId',              ctypes.c_uint8),
        ('TargetId',            ctypes.c_uint8),
        ('Lun',                 ctypes.c_uint8),
        ('CdbLength',           ctypes.c_uint8),
        ('SenseInfoLength',     ctypes.c_uint8),
        ('DataIn',              ctypes.c_uint8),
        ('DataTransferLength',  ctypes.c_uint32),
        ('TimeOutValue',        ctypes.c_uint32),
        ('DataBuffer',          ctypes.c_void_p),
        ('SenseInfoOffset',     ctypes.c_uint32),
        ('Cdb',                 ctypes.c_uint8 * 16),
    ]


class _SPTD_WITH_SENSE(ctypes.Structure):
    _fields_ = [
        ('sptd',         _SCSI_PASS_THROUGH_DIRECT),
        ('sense_buffer', ctypes.c_uint8 * SENSE_BUFFER_SIZE),
    ]

# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _open_handle(path: str, read: bool = True, write: bool = True) -> int:
    access = (GENERIC_READ if read else 0) | (GENERIC_WRITE if write else 0)
    h = kernel32.CreateFileW(
        path, access, FILE_SHARE_NONE, None,
        OPEN_EXISTING, 0, None,
    )
    if h == INVALID_HANDLE_VALUE:
        err = ctypes.get_last_error()
        raise OSError(f"CreateFile({path!r}) failed: Win32 error {err} (0x{err:08X})")
    return h


def _close_handle(h: int) -> None:
    if h and h != INVALID_HANDLE_VALUE:
        kernel32.CloseHandle(h)


def _enum_interface_paths(guid_str: str) -> list[str]:
    """Return all device interface paths for the given GUID."""
    guid = _GUID.from_str(guid_str)
    hdi = setupapi.SetupDiGetClassDevsW(
        ctypes.byref(guid), None, None,
        DIGCF_PRESENT | DIGCF_DEVICEINTERFACE,
    )
    if hdi == INVALID_HANDLE_VALUE:
        return []

    # cbSize for SP_DEVICE_INTERFACE_DETAIL_DATA_W:
    #   DWORD(4) + WCHAR(2) = 6 bytes; but 64-bit builds expect 8 due to
    #   compiler padding in the Windows SDK header.
    detail_cb = 8 if ctypes.sizeof(ctypes.c_void_p) == 8 else 6

    paths: list[str] = []
    iface = _SP_DEVICE_INTERFACE_DATA()
    iface.cbSize = ctypes.sizeof(_SP_DEVICE_INTERFACE_DATA)
    idx = 0

    while True:
        if not setupapi.SetupDiEnumDeviceInterfaces(
            hdi, None, ctypes.byref(guid), idx, ctypes.byref(iface)
        ):
            break

        # Query required buffer size
        req = ctypes.c_ulong(0)
        setupapi.SetupDiGetDeviceInterfaceDetailW(
            hdi, ctypes.byref(iface), None, 0, ctypes.byref(req), None
        )

        buf = (ctypes.c_byte * req.value)()
        ctypes.cast(buf, ctypes.POINTER(ctypes.c_ulong))[0] = detail_cb

        if setupapi.SetupDiGetDeviceInterfaceDetailW(
            hdi, ctypes.byref(iface),
            ctypes.cast(buf, ctypes.c_void_p),
            req.value, None, None,
        ):
            # DevicePath starts at byte offset 4 (past the cbSize DWORD)
            path = ctypes.wstring_at(ctypes.addressof(buf) + 4)
            paths.append(path)

        idx += 1

    setupapi.SetupDiDestroyDeviceInfoList(hdi)
    return paths

# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def find_disk_device() -> str | None:
    r"""Return the physical drive path (\\.\PhysicalDriveN) for the LCD device.

    Tries WMI first (reliable VID extraction), falls back to SetupAPI path
    string matching if WMI is unavailable.
    """
    # --- WMI path ---
    try:
        import win32com.client
        wmi = win32com.client.GetObject('winmgmts://./root/cimv2')
        rows = wmi.ExecQuery("SELECT * FROM Win32_DiskDrive WHERE InterfaceType='USB'")
        for drv in rows:
            pnp     = (drv.PNPDeviceID or '').upper()
            caption = (drv.Caption     or '').upper()
            model   = (drv.Model       or '').upper()
            if 'VID_0416' in pnp and TARGET_DESC in (caption + model + pnp):
                path = drv.DeviceID
                logger.info('Disk found via WMI: %s', path)
                return path
    except Exception as exc:
        logger.warning('WMI disk query failed (%s), falling back to SetupAPI', exc)

    # --- SetupAPI fallback ---
    try:
        for path in _enum_interface_paths(GUID_DEVINTERFACE_DISK):
            upper = path.upper()
            if 'VID_0416' in upper and TARGET_DESC in upper:
                logger.info('Disk found via SetupAPI: %s', path)
                return path
    except Exception as exc:
        logger.error('SetupAPI disk enumeration failed: %s', exc)

    return None


def find_hid_device() -> str | None:
    """Return the HID interface path for the LCD device (VID 0x0416)."""
    try:
        for path in _enum_interface_paths(GUID_DEVINTERFACE_HID):
            try:
                h = _open_handle(path)
            except OSError:
                continue
            try:
                attrs = _HIDD_ATTRIBUTES()
                attrs.Size = ctypes.sizeof(_HIDD_ATTRIBUTES)
                if hid_dll.HidD_GetAttributes(h, ctypes.byref(attrs)):
                    if attrs.VendorID == TARGET_VID:
                        logger.info(
                            'HID device found: %s  VID=0x%04X PID=0x%04X',
                            path, attrs.VendorID, attrs.ProductID,
                        )
                        return path
            finally:
                _close_handle(h)
    except Exception as exc:
        logger.error('HID enumeration failed: %s', exc)

    return None


def do_hid_handshake(hid_path: str) -> tuple[int, int]:
    """Send four 64-byte HID feature reports (0xDA–0xDD) and read back the
    device response to determine screen resolution.

    Returns (width, height).  Logs serial number and raw response bytes so
    the FBL mapping can be extended if needed.
    """
    h = _open_handle(hid_path)
    try:
        for report_id in (0xDA, 0xDB, 0xDC, 0xDD):
            buf = (ctypes.c_uint8 * 64)()
            buf[0] = report_id
            # Remaining bytes are already zero (handshake payload)
            ok = hid_dll.HidD_SetFeature(h, buf, 64)
            if not ok:
                err = ctypes.get_last_error()
                logger.warning(
                    'HidD_SetFeature(0x%02X) failed: Win32 error %d', report_id, err
                )

        # Read response after the final report
        resp = (ctypes.c_uint8 * 64)()
        resp[0] = 0xDD
        ok = hid_dll.HidD_GetFeature(h, resp, 64)
        if not ok:
            err = ctypes.get_last_error()
            logger.warning(
                'HidD_GetFeature failed: Win32 error %d — using default resolution', err
            )
            return DEFAULT_RESOLUTION

        raw = bytes(resp)
        logger.debug('HID handshake raw response: %s', raw.hex())

        # Serial number: spec says bytes 20-35 contain it as hex
        serial_hex = raw[20:36].hex().upper()
        logger.info('Device serial (bytes 20-35 hex): %s', serial_hex)

        # Scan early bytes for a known FBL code (common positions: 1-6)
        fbl_val: int | None = None
        fbl_pos: int | None = None
        for pos in range(1, 7):
            v = raw[pos]
            if v in _FBL_MAP:
                fbl_val = v
                fbl_pos = pos
                break

        if fbl_val is None:
            logger.warning(
                'No recognised FBL byte in response positions 1-6 '
                '(values: %s) — using default %dx%d',
                ' '.join(f'0x{raw[i]:02X}' for i in range(1, 7)),
                *DEFAULT_RESOLUTION,
            )
            return DEFAULT_RESOLUTION

        resolution = _FBL_MAP[fbl_val]
        logger.info(
            'FBL=0x%02X at response offset %d → resolution %dx%d',
            fbl_val, fbl_pos, *resolution,
        )
        return resolution

    finally:
        _close_handle(h)


def open_drive(path: str) -> int:
    """Open the physical drive for SCSI pass-through. Returns a Win32 HANDLE."""
    return _open_handle(path)


def close_drive(handle: int) -> None:
    _close_handle(handle)


def send_scsi_frame(handle: int, rgb565_padded: bytes) -> bool:
    """Push an RGB565 image buffer to the LCD via IOCTL_SCSI_PASS_THROUGH_DIRECT.

    The 16-byte vendor CDB (opcode 0xF5) carries the transfer length; the
    pixel data goes in the DataBuffer.  Returns True on success.
    """
    data_len = len(rgb565_padded)

    # Pin the payload buffer so Python's GC cannot move it during the IOCTL.
    data_buf = ctypes.create_string_buffer(rgb565_padded)

    sw = _SPTD_WITH_SENSE()

    sw.sptd.Length          = ctypes.sizeof(_SCSI_PASS_THROUGH_DIRECT)
    sw.sptd.ScsiStatus      = 0
    sw.sptd.PathId          = 0
    sw.sptd.TargetId        = 0
    sw.sptd.Lun             = 0
    sw.sptd.CdbLength       = 16
    sw.sptd.SenseInfoLength = SENSE_BUFFER_SIZE
    sw.sptd.DataIn          = SCSI_IOCTL_DATA_OUT
    sw.sptd.DataTransferLength = data_len
    sw.sptd.TimeOutValue    = 30   # seconds
    sw.sptd.DataBuffer      = ctypes.addressof(data_buf)
    sw.sptd.SenseInfoOffset = ctypes.sizeof(_SCSI_PASS_THROUGH_DIRECT)

    # Build the 16-byte vendor CDB
    cdb = bytearray(16)
    cdb[0]  = 0xF5          # vendor opcode
    cdb[1]  = 0x00          # sub-command: send image
    cdb[2]  = 0x01          # mode flag
    cdb[3]  = 0x00
    cdb[4]  = 0xBC          # magic bytes
    cdb[5]  = 0xFF
    cdb[6]  = 0xB6
    cdb[7]  = 0xC8
    # bytes 8-11: 0x00 (already zeroed)
    struct.pack_into('<I', cdb, 12, data_len)   # LE transfer length
    for i, b in enumerate(cdb):
        sw.sptd.Cdb[i] = b

    bytes_returned = ctypes.c_ulong(0)
    ok = kernel32.DeviceIoControl(
        handle,
        IOCTL_SCSI_PASS_THROUGH_DIRECT,
        ctypes.byref(sw),
        ctypes.sizeof(sw),
        ctypes.byref(sw),
        ctypes.sizeof(sw),
        ctypes.byref(bytes_returned),
        None,
    )

    if not ok:
        err = ctypes.get_last_error()
        logger.error(
            'DeviceIoControl failed: Win32 error %d (0x%08X)', err, err
        )
        return False

    if sw.sptd.ScsiStatus != 0:
        sense = bytes(sw.sense_buffer)
        logger.error(
            'SCSI error: status=0x%02X  sense=%s',
            sw.sptd.ScsiStatus, sense.hex(),
        )
        return False

    logger.debug('SCSI frame sent: %d bytes transferred', data_len)
    return True
