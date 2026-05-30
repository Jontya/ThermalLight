# ThermalLight

A Windows system-tray app that displays custom images and animated GIFs on the LCD screen built into the **Thermalright Elite Vision AIO** cooler. Runs silently at startup, no admin rights required, no TRCC driver needed.

---

## Features

- **Image editor** — crop any image to a square, preview it, then push it to the LCD with one click
- **Animated GIF support** — full per-frame playback at the GIF's native frame rate
- **Gallery** — browse all uploaded images in a scrollable thumbnail panel; active image highlighted
- **Upload** — import PNG, JPEG, BMP, GIF, WebP, or TIFF files from anywhere
- **Startup registration** — registers itself to launch at Windows login automatically
- **System tray** — lives in the notification area; double-click to open the editor
- **Custom taskbar icon** — LCD-branded icon, not the default Python icon

---

## Requirements

- Windows 10/11 (64-bit)
- Python 3.10 or later — [python.org](https://www.python.org/downloads/)
- Thermalright Elite Vision AIO connected via USB (VID `0x87AD` / PID `0x70DB`)
- libusb-1.0 (installed automatically by `install.bat` via the `libusb` pip package)

---

## Installation

Double-click **`tr_lcd_service\install.bat`** — no admin needed.

It will:
1. Install all Python dependencies (`pip install -r requirements.txt`)
2. Register ThermalLight to start at Windows login
3. Launch the tray icon immediately

The LCD icon appears in the notification area. Double-click it to open the editor.

---

## Usage

### Opening the editor

Double-click the tray icon, or right-click → **Open Editor**.

### Setting an image

1. Click **Upload** to import a file (PNG, JPEG, BMP, GIF, WebP, TIFF)
2. Drag the selection box to choose the crop area; drag a corner handle to resize it
3. Click **Apply** — the image is cropped, resized to 240×240, and sent to the LCD instantly

Previously applied images stay in the gallery for one-click reuse.

### Animated GIFs

Upload a GIF the same way as a static image. Each frame is encoded and played back on the LCD at the GIF's original frame rate.

---

## Uninstalling

Double-click **`tr_lcd_service\uninstall.bat`**, then right-click the tray icon → **Exit**.

Or manually:
```
reg delete "HKCU\Software\Microsoft\Windows\CurrentVersion\Run" /v TRLCDTray /f
```

---

## Log file

```
C:\ProgramData\TRLCDService\service.log
```

Right-click tray icon → **Open Log** to view it directly. Rotates at 5 MB, 2 backups kept.

Set `log_level = DEBUG` in `config.ini` for verbose USB output.

---

## Manual config

`C:\ProgramData\TRLCDService\config.ini`:

```ini
[lcd]
image_path      = C:\ProgramData\TRLCDService\_lcd_output.png
resend_interval = 1
log_level       = INFO
```

`image_path` is updated automatically on Apply. `resend_interval` controls how often the static frame is re-pushed to keep the display alive (default 1 s).

---

## Troubleshooting

| Symptom | Check |
|---------|-------|
| LCD stays blank | Check log for USB errors; replug the AIO and wait for Windows to re-enumerate it |
| "Device not found" in log | Confirm VID `0x87AD` / PID `0x70DB` appears in Device Manager under USB devices |
| libusb error on first run | Re-run `install.bat`; ensure `pip install libusb` completed without errors |
| Tray icon missing | Run `python tr_lcd_service\tray.py` in a terminal to see startup errors |
| Image looks stretched | Use the crop handles to select a square region before applying |

---

## How it works

1. **Device** — pyusb opens the vendor-class bulk USB device directly; no OS driver or mass-storage interface required
2. **Protocol** — a single bulk OUT write sends a 64-byte header (magic, dimensions, RGB565 mode) followed by raw pixel data; no ACK needed
3. **Image pipeline** — source → PIL crop → resize to 240×240 (LANCZOS) → numpy RGB565 encode → bulk write
4. **GIF playback** — all frames pre-encoded at load time; LCD thread cycles through `(bytes, delay_s)` pairs
5. **Resend loop** — static images are resent every `resend_interval` seconds to prevent the display blanking on USB idle
6. **Thread model** — LCD runs on a daemon thread; GUI and thread communicate only via `threading.Event` and config on disk — no shared mutable state

---

## Project structure

```
tr_lcd_service/
├── tray.py          # Entry point — tray icon, app lifecycle, LCD driver thread
├── gui.py           # CustomTkinter editor window (gallery, crop editor, upload)
├── device.py        # USB device discovery and bulk image transfer
├── image_utils.py   # Resize and numpy RGB565 encoding
├── config.py        # INI config read/write
├── install.bat      # One-click install and startup registration
└── uninstall.bat    # Removes startup entry
```
