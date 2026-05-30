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
import sys
import threading
import time
import winreg

import pystray
from PIL import Image, ImageDraw, ImageFont

from config import load_config, save_image_path
from device import close_device, find_device, get_resolution, open_device, send_image
from gui import LCDEditorWindow
from image_utils import encode_rgb565, resize_image

# ---------------------------------------------------------------------------
# Paths / constants
# ---------------------------------------------------------------------------
APP_DATA_DIR     = r'C:\ProgramData\TRLCDService'
LOG_PATH         = os.path.join(APP_DATA_DIR, 'service.log')
LOG_MAX_BYTES    = 5 * 1024 * 1024
LOG_BACKUP_COUNT = 2

STARTUP_REG_KEY  = r'Software\Microsoft\Windows\CurrentVersion\Run'
STARTUP_REG_NAME = 'TRLCDTray'

SERVICE_DIR    = os.path.dirname(os.path.abspath(__file__))
DEVICE_RETRY_S = 30


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
# Tray icon image
# ---------------------------------------------------------------------------

def _make_tray_icon() -> Image.Image:
    size = 64
    img = Image.new('RGBA', (size, size), (26, 26, 46, 255))
    draw = ImageDraw.Draw(img)
    draw.rounded_rectangle([2, 2, size - 3, size - 3], radius=10,
                            outline=(80, 180, 255, 255), width=3)
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
# LCD device thread
# ---------------------------------------------------------------------------

class LCDThread(threading.Thread):
    def __init__(self) -> None:
        super().__init__(daemon=True, name='lcd-driver')
        self._stop   = threading.Event()
        self._reload = threading.Event()

    def signal_reload(self) -> None:
        self._reload.set()

    def stop(self) -> None:
        self._stop.set()
        self._reload.set()

    def _wait(self, seconds: float) -> bool:
        deadline = time.monotonic() + seconds
        while not self._stop.is_set():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return False
            fired = self._reload.wait(timeout=min(remaining, 1.0))
            if fired and self._stop.is_set():
                return True
            if fired:
                return False
        return True

    def run(self) -> None:
        logger = logging.getLogger('lcd')
        dev = None
        frames: list[tuple[bytes, float]] | None = None
        frame_idx = 0
        width, height = 240, 240

        while not self._stop.is_set():
            if dev is None:
                try:
                    dev = find_device()
                    if dev is None:
                        logger.warning('Device not found, retrying in %ds', DEVICE_RETRY_S)
                        if self._wait(DEVICE_RETRY_S):
                            break
                        continue
                    open_device(dev)
                    width, height = get_resolution(dev)
                    logger.info('Resolution: %dx%d', width, height)
                    frames = self._load_frames(width, height)
                    frame_idx = 0
                    if frames is None:
                        close_device(dev)
                        dev = None
                        if self._wait(DEVICE_RETRY_S):
                            break
                        continue
                except Exception:
                    logger.exception('Device init failed')
                    if dev is not None:
                        try:
                            close_device(dev)
                        except Exception:
                            pass
                        dev = None
                    if self._wait(DEVICE_RETRY_S):
                        break
                    continue

            if self._reload.is_set():
                self._reload.clear()
                try:
                    new_frames = self._load_frames(width, height)
                    if new_frames is not None:
                        frames = new_frames
                        frame_idx = 0
                        cfg = load_config()
                        logger.info('Image reloaded: %s', cfg.image_path)
                except Exception:
                    logger.exception('Image reload failed')

            data, delay = frames[frame_idx]
            frame_idx = (frame_idx + 1) % len(frames)
            try:
                ok = send_image(dev, data, width, height)
                if not ok:
                    logger.error('Frame send failed — rediscovering')
                    try:
                        close_device(dev)
                    except Exception:
                        pass
                    dev = None
                    continue
            except Exception:
                logger.exception('Frame send exception — rediscovering')
                try:
                    close_device(dev)
                except Exception:
                    pass
                dev = None
                continue

            if self._wait(delay):
                break

        if dev is not None:
            try:
                close_device(dev)
                logging.getLogger('lcd').info('Device closed')
            except Exception as exc:
                logging.getLogger('lcd').warning('Error closing device: %s', exc)

    @staticmethod
    def _load_frames(width: int, height: int) -> list[tuple[bytes, float]] | None:
        """Return list of (rgb565_bytes, delay_s). Single-element for static images."""
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
            n = getattr(img, 'n_frames', 1)
            if n <= 1:
                frame = resize_image(img.convert('RGB'), width, height)
                return [(encode_rgb565(frame), float(cfg.resend_interval))]
            result = []
            for i in range(n):
                img.seek(i)
                delay = max(0.033, img.info.get('duration', 100) / 1000.0)
                frame = resize_image(img.convert('RGB'), width, height)
                result.append((encode_rgb565(frame), delay))
            logger.info('GIF loaded: %d frames', n)
            return result
        except Exception:
            logger.exception('Image encode failed')
            return None


# ---------------------------------------------------------------------------
# Tray application
# ---------------------------------------------------------------------------

class TrayApp:
    def __init__(self) -> None:
        self._lcd     = LCDThread()
        self._window: LCDEditorWindow | None = None
        self._icon:   pystray.Icon | None = None
        self._exiting = False

    def _show_window(self) -> None:
        if self._window is not None:
            self._window.after(0, self._window.show)

    def _on_open_editor(self, icon: pystray.Icon, item) -> None:
        self._show_window()

    def _on_open_log(self, icon: pystray.Icon, item) -> None:
        try:
            os.makedirs(APP_DATA_DIR, exist_ok=True)
            if not os.path.exists(LOG_PATH):
                open(LOG_PATH, 'a').close()
            os.startfile(LOG_PATH)
        except Exception:
            logging.getLogger('tray').exception('Open log failed')

    def _on_exit(self, icon: pystray.Icon, item) -> None:
        if self._exiting:
            return
        self._exiting = True
        logging.getLogger('tray').info('Exit requested')
        if self._window is not None:
            self._window.after(0, self._window.destroy)

    def _build_menu(self) -> pystray.Menu:
        return pystray.Menu(
            pystray.MenuItem('Open Editor', self._on_open_editor, default=True),
            pystray.MenuItem('Open Log',    self._on_open_log),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem('Exit',        self._on_exit),
        )

    def run(self) -> None:
        cfg = load_config()
        _setup_logging(cfg.log_level)
        log = logging.getLogger('tray')
        log.info('TRLCDTray starting')

        self._lcd.start()

        self._window = LCDEditorWindow(self._lcd)

        self._icon = pystray.Icon(
            name='TRLCDTray',
            icon=_make_tray_icon(),
            title='Thermalright LCD',
            menu=self._build_menu(),
        )
        self._icon.run_detached()

        self._window.mainloop()   # blocks; exits when window.destroy() is called

        self._lcd.stop()
        self._icon.stop()
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
