"""
TRLCDTray — Thermalright Elite Vision AIO LCD system-tray app.

Run directly:
    pythonw tray.py              # launch (no console window)
    python   tray.py             # launch with console (debug)

CLI flags (used by install/uninstall scripts):
    --register-startup           # add to HKCU Run, then exit
    --unregister-startup         # remove from HKCU Run, then exit
"""

import logging
import logging.handlers
import os
import shutil
import sys
import threading
import time
import winreg

import pystray
from PIL import Image, ImageDraw, ImageFont

from config import load_config, save_image_path
from device import (
    close_drive,
    do_hid_handshake,
    find_disk_device,
    find_hid_device,
    open_drive,
    send_scsi_frame,
)
from image_utils import encode_rgb565, pad_to_512, resize_image

# ---------------------------------------------------------------------------
# Paths / constants
# ---------------------------------------------------------------------------
APP_DATA_DIR    = r'C:\ProgramData\TRLCDService'
LOG_PATH        = os.path.join(APP_DATA_DIR, 'service.log')
LOG_MAX_BYTES   = 5 * 1024 * 1024
LOG_BACKUP_COUNT = 2

STARTUP_REG_KEY  = r'Software\Microsoft\Windows\CurrentVersion\Run'
STARTUP_REG_NAME = 'TRLCDTray'

SERVICE_DIR = os.path.dirname(os.path.abspath(__file__))

DEVICE_RETRY_S = 30

IMAGE_EXTS = [
    ('Image files', '*.png *.jpg *.jpeg *.bmp *.gif *.webp *.tiff'),
    ('All files',   '*.*'),
]

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

def _setup_logging(level_str: str = 'INFO') -> None:
    os.makedirs(APP_DATA_DIR, exist_ok=True)
    level = getattr(logging, level_str.upper(), logging.INFO)
    root = logging.getLogger()
    if root.handlers:
        return
    root.setLevel(level)
    handler = logging.handlers.RotatingFileHandler(
        LOG_PATH, maxBytes=LOG_MAX_BYTES, backupCount=LOG_BACKUP_COUNT,
        encoding='utf-8',
    )
    handler.setFormatter(
        logging.Formatter('%(asctime)s [%(levelname)s] %(name)s: %(message)s')
    )
    root.addHandler(handler)
    if sys.stdout and hasattr(sys.stdout, 'isatty') and sys.stdout.isatty():
        root.addHandler(logging.StreamHandler(sys.stdout))


# ---------------------------------------------------------------------------
# Startup registration (HKCU — no admin needed)
# ---------------------------------------------------------------------------

def _pythonw_path() -> str:
    """Return absolute path to pythonw.exe next to the running python.exe."""
    py = sys.executable
    pythonw = os.path.join(os.path.dirname(py), 'pythonw.exe')
    return pythonw if os.path.exists(pythonw) else py


def _startup_value() -> str:
    script = os.path.abspath(__file__)
    return f'"{_pythonw_path()}" "{script}"'


def register_startup() -> None:
    with winreg.OpenKey(
        winreg.HKEY_CURRENT_USER, STARTUP_REG_KEY,
        access=winreg.KEY_SET_VALUE,
    ) as key:
        winreg.SetValueEx(key, STARTUP_REG_NAME, 0, winreg.REG_SZ, _startup_value())
    print(f'Startup entry set: {_startup_value()}')


def unregister_startup() -> None:
    try:
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER, STARTUP_REG_KEY,
            access=winreg.KEY_SET_VALUE,
        ) as key:
            winreg.DeleteValue(key, STARTUP_REG_NAME)
        print('Startup entry removed.')
    except FileNotFoundError:
        print('Startup entry not found (already removed).')


# ---------------------------------------------------------------------------
# Tray icon image (generated via PIL, no external asset needed)
# ---------------------------------------------------------------------------

def _make_tray_icon() -> Image.Image:
    size = 64
    img = Image.new('RGBA', (size, size), (26, 26, 46, 255))
    draw = ImageDraw.Draw(img)
    # Outer rounded-rect border
    draw.rounded_rectangle([2, 2, size - 3, size - 3], radius=10,
                            outline=(80, 180, 255, 255), width=3)
    # "LCD" label in the centre
    try:
        font = ImageFont.truetype('arialbd.ttf', 18)
    except OSError:
        font = ImageFont.load_default()
    text = 'LCD'
    bbox = draw.textbbox((0, 0), text, font=font)
    tw = bbox[2] - bbox[0]
    th = bbox[3] - bbox[1]
    draw.text(((size - tw) / 2, (size - th) / 2 - 2), text,
              fill=(80, 180, 255, 255), font=font)
    return img


# ---------------------------------------------------------------------------
# Image copy helper
# ---------------------------------------------------------------------------

def _store_image(src_path: str) -> str:
    """Copy src_path into APP_DATA_DIR with a stable filename.

    Returns the destination path.
    """
    os.makedirs(APP_DATA_DIR, exist_ok=True)
    ext = os.path.splitext(src_path)[1].lower() or '.png'
    dest = os.path.join(APP_DATA_DIR, f'current_image{ext}')
    shutil.copy2(src_path, dest)
    return dest


# ---------------------------------------------------------------------------
# LCD device thread
# ---------------------------------------------------------------------------

class LCDThread(threading.Thread):
    """Background thread that discovers the device and pushes frames."""

    def __init__(self) -> None:
        super().__init__(daemon=True, name='lcd-driver')
        self._stop  = threading.Event()
        self._reload = threading.Event()

    def signal_reload(self) -> None:
        """Tell the loop to reload the image on the next iteration."""
        self._reload.set()

    def stop(self) -> None:
        self._stop.set()
        self._reload.set()  # unblock any Event.wait

    def _wait(self, seconds: float) -> bool:
        """Wait up to `seconds`. Returns True if stop was requested."""
        deadline = time.monotonic() + seconds
        while not self._stop.is_set():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return False
            # Wake early if reload or stop fires
            fired = self._reload.wait(timeout=min(remaining, 1.0))
            if fired and self._stop.is_set():
                return True
            if fired:
                # reload signal — not a stop
                return False
        return True

    def run(self) -> None:
        logger = logging.getLogger('lcd')
        drive_handle = None
        frame_data: bytes | None = None

        while not self._stop.is_set():
            # ── Device discovery ───────────────────────────────────────
            if drive_handle is None:
                try:
                    disk_path = find_disk_device()
                    if disk_path is None:
                        logger.warning('Disk device not found, retrying in %ds', DEVICE_RETRY_S)
                        if self._wait(DEVICE_RETRY_S):
                            break
                        continue

                    hid_path = find_hid_device()
                    if hid_path is None:
                        logger.warning('HID interface not found, retrying in %ds', DEVICE_RETRY_S)
                        if self._wait(DEVICE_RETRY_S):
                            break
                        continue

                    logger.info('Disk: %s', disk_path)
                    logger.info('HID : %s', hid_path)
                    width, height = do_hid_handshake(hid_path)

                    frame_data = self._encode_current_image(width, height)
                    if frame_data is None:
                        if self._wait(DEVICE_RETRY_S):
                            break
                        continue

                    drive_handle = open_drive(disk_path)
                    logger.info('Drive handle opened')

                except Exception:
                    logger.exception('Device init failed')
                    if drive_handle is not None:
                        try:
                            close_drive(drive_handle)
                        except Exception:
                            pass
                        drive_handle = None
                    if self._wait(DEVICE_RETRY_S):
                        break
                    continue

            # ── Reload image if signalled ──────────────────────────────
            if self._reload.is_set():
                self._reload.clear()
                try:
                    cfg = load_config()
                    # Re-discover resolution via quick handshake if needed
                    hid_path = find_hid_device()
                    if hid_path:
                        w, h = do_hid_handshake(hid_path)
                    else:
                        # Fall back to previously detected size (re-open won't give us it)
                        # Re-encode at default 240x240; device thread will correct next cycle
                        w, h = 240, 240
                    new_frame = self._encode_current_image(w, h)
                    if new_frame is not None:
                        frame_data = new_frame
                        logger.info('Image reloaded: %s', cfg.image_path)
                except Exception:
                    logger.exception('Image reload failed')

            # ── Send frame ─────────────────────────────────────────────
            try:
                ok = send_scsi_frame(drive_handle, frame_data)
                if ok:
                    logger.info('Frame sent')
                else:
                    logger.error('Frame send failed — rediscovering')
                    try:
                        close_drive(drive_handle)
                    except Exception:
                        pass
                    drive_handle = None
                    continue
            except Exception:
                logger.exception('Frame send exception — rediscovering')
                try:
                    close_drive(drive_handle)
                except Exception:
                    pass
                drive_handle = None
                continue

            # ── Wait for next send or reload/stop ──────────────────────
            cfg = load_config()
            self._wait(cfg.resend_interval)

        # ── Cleanup ────────────────────────────────────────────────────
        if drive_handle is not None:
            try:
                close_drive(drive_handle)
                logging.getLogger('lcd').info('Drive handle closed')
            except Exception as exc:
                logging.getLogger('lcd').warning('Error closing handle: %s', exc)

    @staticmethod
    def _encode_current_image(width: int, height: int) -> bytes | None:
        logger = logging.getLogger('lcd')
        cfg = load_config()
        if not cfg.image_path:
            logger.warning('No image_path configured — skipping encode')
            return None
        if not os.path.isfile(cfg.image_path):
            logger.error('Image not found: %s', cfg.image_path)
            return None
        try:
            img = Image.open(cfg.image_path)
            img = resize_image(img, width, height)
            raw = encode_rgb565(img)
            return pad_to_512(raw)
        except Exception:
            logger.exception('Image encode failed')
            return None


# ---------------------------------------------------------------------------
# Tray application
# ---------------------------------------------------------------------------

class TrayApp:
    def __init__(self) -> None:
        self._lcd = LCDThread()
        self._icon: pystray.Icon | None = None

    # ── Menu actions ───────────────────────────────────────────────────

    def _on_change_image(self, icon: pystray.Icon, item) -> None:
        """Open a file dialog and update the image."""
        try:
            import tkinter as tk
            from tkinter import filedialog, messagebox

            root = tk.Tk()
            root.withdraw()
            root.attributes('-topmost', True)

            path = filedialog.askopenfilename(
                title='Select LCD image',
                filetypes=IMAGE_EXTS,
                parent=root,
            )
            root.destroy()

            if not path:
                return  # user cancelled

            stored = _store_image(path)
            save_image_path(stored)
            logging.getLogger('tray').info('Image changed to %s', stored)
            self._lcd.signal_reload()
            self._update_tooltip(stored)

        except Exception:
            logging.getLogger('tray').exception('Change image failed')

    def _on_open_log(self, icon: pystray.Icon, item) -> None:
        try:
            os.makedirs(APP_DATA_DIR, exist_ok=True)
            # Ensure file exists so startfile doesn't error
            if not os.path.exists(LOG_PATH):
                open(LOG_PATH, 'a').close()
            os.startfile(LOG_PATH)
        except Exception:
            logging.getLogger('tray').exception('Open log failed')

    def _on_exit(self, icon: pystray.Icon, item) -> None:
        logging.getLogger('tray').info('Exit requested')
        self._lcd.stop()
        icon.stop()

    # ── Helpers ────────────────────────────────────────────────────────

    def _update_tooltip(self, image_path: str) -> None:
        if self._icon is not None:
            name = os.path.basename(image_path) if image_path else 'no image'
            self._icon.title = f'Thermalright LCD — {name}'

    def _build_menu(self) -> pystray.Menu:
        return pystray.Menu(
            pystray.MenuItem('Change Image...', self._on_change_image, default=True),
            pystray.MenuItem('Open Log',        self._on_open_log),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem('Exit',            self._on_exit),
        )

    # ── Entry point ────────────────────────────────────────────────────

    def run(self) -> None:
        cfg = load_config()
        _setup_logging(cfg.log_level)
        log = logging.getLogger('tray')
        log.info('TRLCDTray starting')

        self._lcd.start()

        icon_img  = _make_tray_icon()
        image_name = os.path.basename(cfg.image_path) if cfg.image_path else 'no image'

        self._icon = pystray.Icon(
            name='TRLCDTray',
            icon=icon_img,
            title=f'Thermalright LCD — {image_name}',
            menu=self._build_menu(),
        )
        self._icon.run()     # blocks until icon.stop() is called

        self._lcd.stop()
        self._lcd.join(timeout=5)
        log.info('TRLCDTray stopped')


# ---------------------------------------------------------------------------
# CLI entry
# ---------------------------------------------------------------------------

def main() -> None:
    if '--register-startup' in sys.argv:
        register_startup()
        return
    if '--unregister-startup' in sys.argv:
        unregister_startup()
        return
    TrayApp().run()


if __name__ == '__main__':
    main()
