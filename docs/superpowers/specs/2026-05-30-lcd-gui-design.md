# LCD Editor GUI — Design Spec
Date: 2026-05-30

## Overview

Add a CustomTkinter GUI window to the ThermalLight tray app. The window provides image upload, a 1:1 square crop editor, and a history gallery of previously applied images. The system tray icon remains; clicking it opens the window.

## Architecture

### New file: `tr_lcd_service/gui.py`
Owns the `LCDEditorWindow` class. Imported and managed by `tray.py`.

### Changes to `tr_lcd_service/tray.py`
- Import and instantiate `LCDEditorWindow` at startup (window starts hidden)
- Left-click or double-click on tray icon → `window.show()`
- "Change Image..." menu item → `window.show()` (replaces file dialog)
- `LCDThread` reference passed to window so Apply can signal reload
- Exit from tray menu → stops `LCDThread`, destroys window, stops tray

### No changes to: `device.py`, `image_utils.py`, `config.py`

## GUI Layout

Single CustomTkinter `CTk` window (~900×580px), dark theme.

```
┌──────────────────────────────────────────────────────┐
│  Thermalright LCD Editor          [Upload Image]      │
├──────────────┬───────────────────────────────────────┤
│  History     │  Crop Editor                          │
│              │                                       │
│  [thumb]     │  ┌────────────────────────────────┐  │
│  [thumb]     │  │                                │  │
│  [thumb]     │  │   image with draggable square  │  │
│  [thumb]     │  │   selection overlay            │  │
│  [thumb]     │  │                                │  │
│  [thumb]     │  └────────────────────────────────┘  │
│              │                                       │
│              │                    [Apply to LCD]     │
└──────────────┴───────────────────────────────────────┘
```

### History Panel (left, ~200px wide)
- Scrollable vertical list of image thumbnails (80×80px) from `C:\ProgramData\TRLCDService`
- Thumbnails generated with PIL on panel load; cached in memory
- Clicking a thumbnail loads that image into the crop editor
- Currently-applied image highlighted with a border
- Panel refreshes when a new image is applied

### Crop Editor (right, fills remaining width)
- `tkinter.Canvas` inside a CTkFrame
- Image scaled to fit canvas while preserving aspect ratio
- Draggable square selection box (1:1 aspect ratio locked):
  - Mouse down → set top-left corner
  - Mouse drag → expand square (size = min(dx, dy))
  - Square constrained to image bounds
  - Visual: semi-transparent overlay, bright border on selection
- Default selection on load: largest centered square (auto-fills the LCD well)
- No apply until user clicks Apply

### Upload Button (top right)
- Opens native file dialog (subprocess picker, same as current `_pick_file_native()`)
- Selected file copied to `TRLCDService` dir with original filename
- Loaded into crop editor immediately
- Does NOT auto-apply — user must click Apply

### Apply Button (bottom right of crop editor)
1. Crop the image using current selection box coordinates
2. Resize cropped region to 320×320
3. Save result back to `TRLCDService` dir (overwrite same file)
4. Call `save_image_path(path)` to update `config.ini`
5. Call `lcd_thread.signal_reload()` to push to device immediately
6. Refresh history panel, highlight newly applied image

## Window Behaviour

- Window starts **hidden** on app launch
- Tray left-click → `window.deiconify()` / `window.lift()`
- Closing window (X) → `window.withdraw()` (hide, don't destroy)
- Tray Exit → `window.destroy()` then app quits
- Window is not shown automatically on boot — stays hidden until user clicks tray

## Boot / Startup

- `install.bat` already registers `pythonw tray.py` in `HKCU\Run`
- On boot: `LCDThread` starts, reads `config.ini` for last `image_path`, pushes frame immediately
- No window shown on boot; LCD is updated silently in background
- User opens window via tray click when they want to change image

## Dependencies

Add to `requirements.txt`:
```
customtkinter>=5.2.0
```

No other new dependencies. PIL (already present) handles thumbnail generation and cropping.

## File Summary

| File | Change |
|------|--------|
| `tr_lcd_service/gui.py` | New — `LCDEditorWindow` class |
| `tr_lcd_service/tray.py` | Open window on tray click; pass `LCDThread` ref to window |
| `tr_lcd_service/requirements.txt` | Add `customtkinter>=5.2.0` |
| `tr_lcd_service/install.bat` | No change needed |
