# TRLCDService

Minimal Windows service that pushes a static image to the **Thermalright Elite Vision AIO LCD** (USB VID `0x0416`, mass-storage class device `USBDISPLAY`) and keeps it there.

No sensor polling, no GUI, no TRCC driver required.

---

## Requirements

- Windows 10/11 (64-bit)
- Python 3.10 or later — [python.org](https://www.python.org/downloads/)
- The device must be connected and recognised by Windows as a USB mass-storage device before starting the service

---

## Installation

1. **Open an Administrator command prompt** (right-click → *Run as administrator*).

2. **Install Python dependencies** (done automatically by the batch file, but you can also run manually):
   ```
   pip install -r requirements.txt
   ```

3. **Edit `config.ini`** to point `image_path` at your desired image file:
   ```ini
   [lcd]
   image_path = C:\Users\You\Pictures\lcd_image.png
   resend_interval = 60
   log_level = INFO
   ```

4. **Run `install.bat` as Administrator** from the `tr_lcd_service\` folder:
   ```
   install.bat
   ```
   This installs and immediately starts the service.  The image should appear on the LCD within a few seconds.

---

## Changing the image

1. Edit `config.ini` and update `image_path`.
2. Restart the service so it re-reads the config:
   ```
   python service.py restart
   ```
   Or use Windows Services (`services.msc`) → *TRLCDService* → *Restart*.

The image is **automatically resized** to the resolution reported by the device; no pre-scaling is required.

---

## Checking the log

```
C:\ProgramData\TRLCDService\service.log
```

The log rotates at 5 MB and keeps two backups.  Set `log_level = DEBUG` in `config.ini` for verbose output (raw HID response bytes, SCSI transfer sizes, etc.).

---

## Uninstalling

Run `uninstall.bat` as Administrator, or manually:

```
python service.py stop
python service.py remove
```

---

## Troubleshooting

| Symptom | Check |
|---------|-------|
| Service starts but LCD stays blank | Verify `image_path` in `config.ini` is correct; check the log for SCSI errors |
| "Device not found" repeated in log | Make sure the AIO is plugged in and Windows shows it as a storage device; try `log_level = DEBUG` to see enumerated paths |
| Unknown FBL byte warning | The device returned an unrecognised resolution code — add your FBL byte and resolution to `_FBL_MAP` in `device.py` (the raw HID response is logged at DEBUG level) |
| Service fails to install | Run the command prompt as Administrator; confirm `python` is in `PATH` |

---

## How it works

1. **Device discovery** — WMI queries `Win32_DiskDrive` for a USB drive whose PNP ID contains `VID_0416` and whose caption/model contains `USBDISPLAY`.  SetupAPI enumeration is used as a fallback and for the HID interface.
2. **HID handshake** — Four 64-byte feature reports (`0xDA`–`0xDD`) are sent to the HID interface.  The response encodes the screen resolution via a firmware byte-layout (FBL) code.
3. **Image encoding** — The source image is resized to the detected resolution and converted to packed little-endian **RGB565** (16 bpp).
4. **SCSI frame** — The pixel data is padded to 512-byte alignment and delivered to the mass-storage interface via `IOCTL_SCSI_PASS_THROUGH_DIRECT` with a 16-byte vendor CDB (opcode `0xF5`).
5. **Resend loop** — The frame is resent every `resend_interval` seconds to recover from USB power events that reset the display.
