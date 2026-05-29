# TRLCDService

System tray app that pushes a static image to the **Thermalright Elite Vision AIO LCD** (USB VID `0x0416`, mass-storage class device `USBDISPLAY`) and keeps it there.

The icon lives in the Windows notification area ("Show Hidden Icons"). Right-click to change the image. No TRCC driver, no admin rights, no Windows service.

---

## Requirements

- Windows 10/11 (64-bit)
- Python 3.10 or later — [python.org](https://www.python.org/downloads/)
- AIO connected and recognised by Windows as a USB mass-storage device

---

## Installation

1. **Run `install.bat`** from the `tr_lcd_service\` folder (double-click — no admin needed):
   ```
   install.bat
   ```
   This installs Python dependencies, registers the app to start at login, and launches the tray icon immediately.

2. **Set your image** — the tray icon appears in the notification area. Right-click it and choose **Change Image...**, then pick any PNG, JPEG, BMP, etc. The image is copied to `C:\ProgramData\TRLCDService\` and the LCD updates within seconds.

The app starts automatically at every Windows login via a registry key (`HKCU\...\Run`).

---

## Changing the image

Right-click the tray icon → **Change Image...** → pick a file.

The image is automatically resized to the resolution the device reports during the HID handshake, so no pre-scaling is needed.

---

## Checking the log

```
C:\ProgramData\TRLCDService\service.log
```

Or right-click the tray icon → **Open Log**.

Rotating at 5 MB, 2 backups kept. Set `log_level = DEBUG` in `config.ini` for verbose output.

---

## Manual config

`tr_lcd_service\config.ini` holds three settings:

```ini
[lcd]
image_path      = C:\ProgramData\TRLCDService\current_image.png
resend_interval = 60
log_level       = INFO
```

`image_path` is updated automatically when you use "Change Image...". You can also edit it by hand; the tray app re-reads config between frames.

---

## Uninstalling

Run `uninstall.bat`, or manually:

1. Right-click tray icon → **Exit**
2. Delete the startup registry entry:
   ```
   reg delete "HKCU\Software\Microsoft\Windows\CurrentVersion\Run" /v TRLCDTray /f
   ```

---

## Troubleshooting

| Symptom | Check |
|---------|-------|
| LCD stays blank after picking image | Check the log for SCSI errors; ensure AIO is plugged in |
| "Device not found" in log | Windows must show the AIO as a USB storage device; retry after replugging |
| Unknown FBL byte warning | Add your FBL value and resolution to `_FBL_MAP` in `device.py` (raw response is logged at DEBUG level) |
| No icon in notification area | Run `python tray.py` in a terminal to see startup errors directly |

---

## How it works

1. **Device discovery** — finds the physical drive (VID `0x0416`, description `USBDISPLAY`) via WMI / SetupAPI, and the companion HID interface.
2. **HID handshake** — four 64-byte feature reports (`0xDA`–`0xDD`) elicit a response containing the screen resolution (FBL byte) and serial number.
3. **Image encoding** — source image is resized to detected resolution and converted to packed little-endian **RGB565** (16 bpp), then padded to 512-byte alignment.
4. **SCSI frame** — pixel data is sent via `IOCTL_SCSI_PASS_THROUGH_DIRECT` with a 16-byte vendor CDB (opcode `0xF5`).
5. **Resend loop** — frame is resent every `resend_interval` seconds (default 60 s) to survive USB power events; the loop runs on a daemon thread inside the tray process.
