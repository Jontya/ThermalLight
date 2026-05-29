"""
TRLCDService — Thermalright Elite Vision AIO LCD image pusher.

Install  : python service.py install  (then start)
Start    : python service.py start
Stop     : python service.py stop
Remove   : python service.py remove
Debug    : python service.py debug    (runs in foreground, Ctrl-C to stop)
"""

import logging
import logging.handlers
import os
import sys

import servicemanager
import win32event
import win32service
import win32serviceutil

from config import load_config
from device import (
    close_drive,
    do_hid_handshake,
    find_disk_device,
    find_hid_device,
    open_drive,
    send_scsi_frame,
)
from image_utils import encode_rgb565, pad_to_512, resize_image
from PIL import Image

LOG_PATH        = r'C:\ProgramData\TRLCDService\service.log'
LOG_MAX_BYTES   = 5 * 1024 * 1024   # 5 MB
LOG_BACKUP_COUNT = 2
DEVICE_RETRY_S  = 30                 # seconds between device-not-found retries


def _setup_logging(log_level: str = 'INFO') -> None:
    os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)
    level = getattr(logging, log_level.upper(), logging.INFO)
    root = logging.getLogger()
    if root.handlers:
        return  # already configured
    root.setLevel(level)
    handler = logging.handlers.RotatingFileHandler(
        LOG_PATH, maxBytes=LOG_MAX_BYTES, backupCount=LOG_BACKUP_COUNT,
        encoding='utf-8',
    )
    handler.setFormatter(
        logging.Formatter('%(asctime)s [%(levelname)s] %(name)s: %(message)s')
    )
    root.addHandler(handler)
    if sys.stdout and sys.stdout.isatty():
        root.addHandler(logging.StreamHandler(sys.stdout))


def _load_and_encode(image_path: str, width: int, height: int) -> bytes:
    """Open image, resize, encode to RGB565, pad to 512-byte boundary."""
    img = Image.open(image_path)
    img = resize_image(img, width, height)
    raw = encode_rgb565(img)
    return pad_to_512(raw)


class TRLCDService(win32serviceutil.ServiceFramework):
    _svc_name_        = 'TRLCDService'
    _svc_display_name_ = 'Thermalright LCD Image Service'
    _svc_description_ = (
        'Pushes a static image to the Thermalright Elite Vision AIO '
        'LCD screen.  Resends periodically to survive power events.'
    )

    def __init__(self, args: list) -> None:
        win32serviceutil.ServiceFramework.__init__(self, args)
        self._stop_event = win32event.CreateEvent(None, 0, 0, None)
        self._running    = True

    # ------------------------------------------------------------------
    # Service control handlers
    # ------------------------------------------------------------------

    def SvcStop(self) -> None:
        self.ReportServiceStatus(win32service.SERVICE_STOP_PENDING)
        self._running = False
        win32event.SetEvent(self._stop_event)
        logging.info('Service stop requested')

    def SvcDoRun(self) -> None:
        servicemanager.LogMsg(
            servicemanager.EVENTLOG_INFORMATION_TYPE,
            servicemanager.PYS_SERVICE_STARTED,
            (self._svc_name_, ''),
        )
        try:
            cfg = load_config()
            _setup_logging(cfg.log_level)
        except Exception:
            _setup_logging('INFO')

        logging.info('TRLCDService starting')
        try:
            self._main_loop()
        except Exception:
            logging.exception('Unhandled exception in main loop')
        logging.info('TRLCDService stopped')

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _wait(self, seconds: float) -> bool:
        """Block for `seconds` or until stop event.  Returns True if stop fired."""
        ms = max(1, int(seconds * 1000))
        rc = win32event.WaitForSingleObject(self._stop_event, ms)
        return rc == win32event.WAIT_OBJECT_0

    # ------------------------------------------------------------------
    # Main logic
    # ------------------------------------------------------------------

    def _main_loop(self) -> None:
        cfg           = load_config()
        drive_handle  = None
        frame_data: bytes | None = None

        while self._running:
            # ── Device discovery ───────────────────────────────────────
            if drive_handle is None:
                try:
                    disk_path = find_disk_device()
                    if disk_path is None:
                        logging.warning(
                            'Disk device not found (VID 0x0416 / USBDISPLAY), '
                            'retrying in %ds', DEVICE_RETRY_S
                        )
                        if self._wait(DEVICE_RETRY_S):
                            break
                        continue

                    hid_path = find_hid_device()
                    if hid_path is None:
                        logging.warning(
                            'HID interface not found (VID 0x0416), '
                            'retrying in %ds', DEVICE_RETRY_S
                        )
                        if self._wait(DEVICE_RETRY_S):
                            break
                        continue

                    logging.info('Disk: %s', disk_path)
                    logging.info('HID : %s', hid_path)

                    width, height = do_hid_handshake(hid_path)

                    cfg = load_config()   # re-read in case it changed on disk
                    if not cfg.image_path:
                        logging.error('image_path not set in config.ini')
                        if self._wait(DEVICE_RETRY_S):
                            break
                        continue

                    frame_data = _load_and_encode(cfg.image_path, width, height)
                    logging.info(
                        'Image loaded: %s → %dx%d, %d bytes (padded)',
                        cfg.image_path, width, height, len(frame_data),
                    )

                    drive_handle = open_drive(disk_path)
                    logging.info('Drive handle opened')

                except Exception:
                    logging.exception('Device initialisation failed')
                    if drive_handle is not None:
                        try:
                            close_drive(drive_handle)
                        except Exception:
                            pass
                        drive_handle = None
                    if self._wait(DEVICE_RETRY_S):
                        break
                    continue

            # ── Send frame ─────────────────────────────────────────────
            try:
                ok = send_scsi_frame(drive_handle, frame_data)
                if ok:
                    logging.info('Frame sent successfully')
                else:
                    logging.error('Frame send failed — rediscovering device')
                    try:
                        close_drive(drive_handle)
                    except Exception:
                        pass
                    drive_handle = None
                    continue

            except Exception:
                logging.exception('Exception during frame send — rediscovering device')
                try:
                    close_drive(drive_handle)
                except Exception:
                    pass
                drive_handle = None
                continue

            # ── Wait for resend interval or stop signal ─────────────────
            cfg = load_config()
            if self._wait(cfg.resend_interval):
                break  # stop requested

        # ── Cleanup ────────────────────────────────────────────────────
        if drive_handle is not None:
            try:
                close_drive(drive_handle)
                logging.info('Drive handle closed')
            except Exception as exc:
                logging.warning('Error closing drive handle: %s', exc)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == '__main__':
    if len(sys.argv) == 1:
        # Launched by the SCM — hand off to the service dispatcher
        servicemanager.Initialize()
        servicemanager.PrepareToHostSingle(TRLCDService)
        servicemanager.StartServiceCtrlDispatcher()
    else:
        # CLI: install / start / stop / remove / debug
        win32serviceutil.HandleCommandLine(TRLCDService)
