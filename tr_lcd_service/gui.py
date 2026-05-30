"""
LCDEditorWindow — CustomTkinter GUI for ThermalLight LCD control.

Lifecycle:
  - Instantiated hidden at app start
  - show() called from tray (via after()) to reveal
  - WM_DELETE_WINDOW withdraws rather than destroys
  - destroy() called by tray Exit to end mainloop
"""

import os
import shutil
import tkinter as tk
from tkinter import filedialog
from PIL import Image, ImageDraw, ImageFont, ImageTk
import customtkinter as ctk

from config import load_config, save_image_path

APP_DATA_DIR = r'C:\ProgramData\TRLCDService'
IMAGE_EXTS = ('.png', '.jpg', '.jpeg', '.bmp', '.gif', '.webp', '.tiff')
LCD_SIZE = 320

ctk.set_appearance_mode('dark')
ctk.set_default_color_theme('blue')

try:
    _GIF_BADGE_FONT = ImageFont.truetype('arialbd.ttf', 10)
except OSError:
    _GIF_BADGE_FONT = ImageFont.load_default()


class LCDEditorWindow(ctk.CTk):
    def __init__(self, lcd_thread) -> None:
        super().__init__()
        self._lcd_thread = lcd_thread
        self._source_path: str | None = None   # path of image in editor
        self._img_orig: Image.Image | None = None
        self._canvas_photo: ImageTk.PhotoImage | None = None
        self._img_offset: tuple[int, int] = (0, 0)
        self._img_scale: float = 1.0
        self._sel: tuple[int, int, int, int] | None = None  # x1,y1,x2,y2 image coords
        self._drag_mode: str | None = None   # 'move' | 'tl'|'tr'|'bl'|'br'
        self._drag_origin: tuple[int, int] | None = None  # canvas coords at mousedown
        self._sel_at_drag: tuple[int, int, int, int] | None = None
        self._icon_refs: list = []
        self._last_applied_source: str | None = None  # original path, for gallery highlight
        self._thumb_cache: dict[tuple[str, float], ImageTk.PhotoImage] = {}
        self._gallery_state: list[tuple[str, float]] = []   # last rendered (normpath, mtime) list
        self._gallery_buttons: list[tuple[str, ctk.CTkButton]] = []  # parallel to _gallery_state

        self.title('Thermalright LCD Editor')
        self.geometry('920x580')
        self.resizable(False, False)
        self.protocol('WM_DELETE_WINDOW', self.withdraw)
        self.withdraw()   # start hidden

        self._build_ui()
        self._set_window_icon()

    # ── Icon ───────────────────────────────────────────────────────────

    def _set_window_icon(self) -> None:
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
        tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
        draw.text(((size - tw) / 2, (size - th) / 2 - 2), text,
                  fill=(80, 180, 255, 255), font=font)
        icon32 = ImageTk.PhotoImage(img.resize((32, 32), Image.LANCZOS))
        icon16 = ImageTk.PhotoImage(img.resize((16, 16), Image.LANCZOS))
        self._icon_refs = [icon32, icon16]
        self.iconphoto(True, icon32, icon16)

    # ── Public ─────────────────────────────────────────────────────────

    def show(self) -> None:
        self.deiconify()
        self.lift()
        self.focus_force()
        self._refresh_gallery()

    # ── UI construction ────────────────────────────────────────────────

    def _build_ui(self) -> None:
        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(1, weight=1)

        # ── Header bar ────────────────────────────────────────────────
        header = ctk.CTkFrame(self, height=48, corner_radius=0)
        header.grid(row=0, column=0, columnspan=2, sticky='ew')
        header.grid_propagate(False)
        header.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(
            header, text='Thermalright LCD Editor',
            font=ctk.CTkFont(size=15, weight='bold'),
        ).grid(row=0, column=0, padx=16, pady=12, sticky='w')

        ctk.CTkButton(
            header, text='Upload Image', width=130,
            command=self._on_upload,
        ).grid(row=0, column=1, padx=12, pady=8, sticky='e')

        # ── History panel (left) ───────────────────────────────────────
        self._gallery_frame = ctk.CTkScrollableFrame(
            self, width=180, label_text='History',
        )
        self._gallery_frame.grid(row=1, column=0, padx=(8, 4), pady=8, sticky='ns')

        # ── Crop editor (right) ────────────────────────────────────────
        editor_frame = ctk.CTkFrame(self)
        editor_frame.grid(row=1, column=1, padx=(4, 8), pady=8, sticky='nsew')
        editor_frame.grid_rowconfigure(0, weight=1)
        editor_frame.grid_columnconfigure(0, weight=1)

        self._canvas = tk.Canvas(
            editor_frame, bg='#1a1a2e', highlightthickness=0,
            width=680, height=480,
        )
        self._canvas.grid(row=0, column=0, padx=8, pady=(8, 4), sticky='nsew')

        self._canvas.bind('<ButtonPress-1>',   self._on_mouse_down)
        self._canvas.bind('<B1-Motion>',       self._on_mouse_drag)
        self._canvas.bind('<ButtonRelease-1>', self._on_mouse_up)

        self._apply_btn = ctk.CTkButton(
            editor_frame, text='Apply to LCD', width=160,
            command=self._on_apply, state='disabled',
        )
        self._apply_btn.grid(row=1, column=0, padx=8, pady=(4, 8), sticky='e')

        self._status_label = ctk.CTkLabel(
            editor_frame, text='Upload an image to get started',
            text_color='gray',
        )
        self._status_label.grid(row=1, column=0, padx=8, pady=(4, 8), sticky='w')

    # ── Gallery ────────────────────────────────────────────────────────

    def _refresh_gallery(self) -> None:
        os.makedirs(APP_DATA_DIR, exist_ok=True)
        active = (os.path.normcase(self._last_applied_source)
                  if self._last_applied_source else '')

        # Build ordered list of (normpath, mtime) for files that exist and can be stat'd
        new_state: list[tuple[str, float]] = []
        for fname in sorted(
            (f for f in os.listdir(APP_DATA_DIR)
             if f.lower().endswith(IMAGE_EXTS) and not f.startswith('_')),
            key=lambda f: os.path.getmtime(os.path.join(APP_DATA_DIR, f)),
            reverse=True,
        ):
            fpath = os.path.normcase(os.path.join(APP_DATA_DIR, fname))
            try:
                mtime = os.path.getmtime(fpath)
            except OSError:
                continue
            new_state.append((fpath, mtime))

        # Fast path: file list and mtimes unchanged — only border updates needed
        if new_state == self._gallery_state:
            for fpath, btn in self._gallery_buttons:
                border = 2 if fpath == active else 0
                btn.configure(border_width=border)
            return

        # Slow path: structural change — evict stale cache, rebuild widgets
        live_keys = {(p, m) for p, m in new_state}
        for key in list(self._thumb_cache):
            if key not in live_keys:
                del self._thumb_cache[key]

        new_buttons: list[tuple[str, ctk.CTkButton]] = []
        for w in self._gallery_frame.winfo_children():
            w.destroy()

        for fpath, mtime in new_state:
            key = (fpath, mtime)
            photo = self._thumb_cache.get(key)
            if photo is None:
                try:
                    thumb = Image.open(fpath).convert('RGB')
                    thumb.thumbnail((80, 80), Image.LANCZOS)
                    if fpath.lower().endswith('.gif'):
                        thumb = thumb.convert('RGBA')
                        badge = Image.new('RGBA', thumb.size, (0, 0, 0, 0))
                        d = ImageDraw.Draw(badge)
                        fnt = _GIF_BADGE_FONT
                        text = 'GIF'
                        bb = d.textbbox((0, 0), text, font=fnt)
                        tw, th = bb[2] - bb[0], bb[3] - bb[1]
                        pad = 2
                        bx, by = thumb.size[0] - tw - pad * 2 - 3, 3
                        d.rectangle([bx - pad, by - pad, bx + tw + pad, by + th + pad],
                                    fill=(0, 100, 200, 210))
                        d.text((bx, by), text, font=fnt, fill=(255, 255, 255, 255))
                        thumb.paste(badge, (0, 0), badge)
                        thumb = thumb.convert('RGB')
                    photo = ImageTk.PhotoImage(thumb)
                    self._thumb_cache[key] = photo
                except Exception:
                    continue

            is_active = fpath == active
            border = 2 if is_active else 0
            btn = ctk.CTkButton(
                self._gallery_frame,
                image=photo, text='',
                width=88, height=88,
                border_width=border,
                border_color='#50B4FF',
                command=lambda p=fpath: self._load_image(p),
            )
            btn.pack(pady=4)
            new_buttons.append((fpath, btn))

        self._gallery_state = new_state
        self._gallery_buttons = new_buttons

    # ── Crop editor ────────────────────────────────────────────────────

    def _load_image(self, path: str) -> None:
        try:
            img = Image.open(path).convert('RGB')
        except Exception:
            return
        self._source_path = path
        self._img_orig = img
        self._render_image()
        self._default_selection()
        self._apply_btn.configure(state='normal')
        self._status_label.configure(
            text=os.path.basename(path), text_color='white',
        )

    def _render_image(self) -> None:
        if self._img_orig is None:
            return
        cw = self._canvas.winfo_width() or 680
        ch = self._canvas.winfo_height() or 480
        iw, ih = self._img_orig.size
        scale = min(cw / iw, ch / ih)
        nw, nh = int(iw * scale), int(ih * scale)
        ox = (cw - nw) // 2
        oy = (ch - nh) // 2

        self._img_scale = scale
        self._img_offset = (ox, oy)

        disp = self._img_orig.resize((nw, nh), Image.LANCZOS)
        self._canvas_photo = ImageTk.PhotoImage(disp)
        self._canvas.delete('all')
        self._canvas.create_image(ox, oy, anchor='nw', image=self._canvas_photo)

    def _default_selection(self) -> None:
        if self._img_orig is None:
            return
        iw, ih = self._img_orig.size
        side = min(iw, ih)
        x1 = (iw - side) // 2
        y1 = (ih - side) // 2
        self._sel = (x1, y1, x1 + side, y1 + side)
        self._draw_selection()

    _HANDLE_R = 6  # handle hit radius in canvas pixels

    def _draw_selection(self) -> None:
        self._canvas.delete('sel')
        if self._sel is None or self._img_orig is None:
            return
        ox, oy = self._img_offset
        s = self._img_scale
        x1, y1, x2, y2 = self._sel
        cx1 = ox + x1 * s
        cy1 = oy + y1 * s
        cx2 = ox + x2 * s
        cy2 = oy + y2 * s
        iw, ih = self._img_orig.size
        ix2 = ox + iw * s
        iy2 = oy + ih * s
        for coords in [
            (ox, oy, cx1, iy2),
            (cx2, oy, ix2, iy2),
            (cx1, oy, cx2, cy1),
            (cx1, cy2, cx2, iy2),
        ]:
            self._canvas.create_rectangle(
                *coords, fill='black', stipple='gray50',
                outline='', tags='sel',
            )
        self._canvas.create_rectangle(
            cx1, cy1, cx2, cy2,
            outline='#50B4FF', width=2, tags='sel',
        )
        h = self._HANDLE_R
        for hx, hy in [(cx1, cy1), (cx2, cy1), (cx1, cy2), (cx2, cy2)]:
            self._canvas.create_rectangle(
                hx - h, hy - h, hx + h, hy + h,
                fill='#50B4FF', outline='white', width=1, tags='sel',
            )

    # ── Mouse events ───────────────────────────────────────────────────

    def _canvas_to_image(self, cx: int, cy: int) -> tuple[int, int]:
        ox, oy = self._img_offset
        s = self._img_scale
        ix = int((cx - ox) / s)
        iy = int((cy - oy) / s)
        iw, ih = self._img_orig.size
        return max(0, min(ix, iw)), max(0, min(iy, ih))

    def _hit_test(self, cx: int, cy: int) -> str | None:
        """Return 'tl','tr','bl','br','move', or None."""
        if self._sel is None or self._img_orig is None:
            return None
        ox, oy = self._img_offset
        s = self._img_scale
        x1, y1, x2, y2 = self._sel
        cx1, cy1 = ox + x1 * s, oy + y1 * s
        cx2, cy2 = ox + x2 * s, oy + y2 * s
        h = self._HANDLE_R
        if abs(cx - cx1) <= h and abs(cy - cy1) <= h:
            return 'tl'
        if abs(cx - cx2) <= h and abs(cy - cy1) <= h:
            return 'tr'
        if abs(cx - cx1) <= h and abs(cy - cy2) <= h:
            return 'bl'
        if abs(cx - cx2) <= h and abs(cy - cy2) <= h:
            return 'br'
        if cx1 <= cx <= cx2 and cy1 <= cy <= cy2:
            return 'move'
        return None

    def _on_mouse_down(self, event) -> None:
        if self._img_orig is None:
            return
        mode = self._hit_test(event.x, event.y)
        if mode is None:
            return
        self._drag_mode = mode
        self._drag_origin = (event.x, event.y)
        self._sel_at_drag = self._sel

    def _on_mouse_drag(self, event) -> None:
        if self._img_orig is None or self._drag_mode is None or self._sel_at_drag is None:
            return
        iw, ih = self._img_orig.size
        s = self._img_scale

        if self._drag_mode == 'move':
            ox0, oy0 = self._drag_origin
            dcx = event.x - ox0
            dcy = event.y - oy0
            dix = dcx / s
            diy = dcy / s
            x1, y1, x2, y2 = self._sel_at_drag
            side = x2 - x1
            nx1 = max(0, min(int(x1 + dix), iw - side))
            ny1 = max(0, min(int(y1 + diy), ih - side))
            self._sel = (nx1, ny1, nx1 + side, ny1 + side)
        else:
            # Corner resize — opposite corner is anchor
            ax1, ay1, ax2, ay2 = self._sel_at_drag
            mx, my = self._canvas_to_image(event.x, event.y)
            if self._drag_mode == 'br':
                anchor_x, anchor_y = ax1, ay1
                side = max(1, min(mx - anchor_x, my - anchor_y, iw - anchor_x, ih - anchor_y))
                self._sel = (anchor_x, anchor_y, anchor_x + side, anchor_y + side)
            elif self._drag_mode == 'bl':
                anchor_x, anchor_y = ax2, ay1
                side = max(1, min(anchor_x - mx, my - anchor_y, anchor_x, ih - anchor_y))
                self._sel = (anchor_x - side, anchor_y, anchor_x, anchor_y + side)
            elif self._drag_mode == 'tr':
                anchor_x, anchor_y = ax1, ay2
                side = max(1, min(mx - anchor_x, anchor_y - my, iw - anchor_x, anchor_y))
                self._sel = (anchor_x, anchor_y - side, anchor_x + side, anchor_y)
            elif self._drag_mode == 'tl':
                anchor_x, anchor_y = ax2, ay2
                side = max(1, min(anchor_x - mx, anchor_y - my, anchor_x, anchor_y))
                self._sel = (anchor_x - side, anchor_y - side, anchor_x, anchor_y)
        self._draw_selection()

    def _on_mouse_up(self, event) -> None:
        self._drag_mode = None
        self._drag_origin = None
        self._sel_at_drag = None

    # ── Upload ─────────────────────────────────────────────────────────

    def _on_upload(self) -> None:
        path = filedialog.askopenfilename(
            title='Select LCD image',
            filetypes=[
                ('Image files', '*.png *.jpg *.jpeg *.bmp *.gif *.webp *.tiff'),
                ('All files', '*.*'),
            ],
        )
        if not path:
            return
        os.makedirs(APP_DATA_DIR, exist_ok=True)
        dest = os.path.join(APP_DATA_DIR, os.path.basename(path))
        shutil.copy2(path, dest)
        self._refresh_gallery()
        self._load_image(dest)

    # ── Apply ──────────────────────────────────────────────────────────

    def _on_apply(self) -> None:
        if self._img_orig is None or self._sel is None or self._source_path is None:
            return
        x1, y1, x2, y2 = self._sel
        is_gif = self._source_path.lower().endswith('.gif')
        ext = '.gif' if is_gif else '.png'
        out_path = os.path.join(APP_DATA_DIR, f'_lcd_output{ext}')

        if is_gif:
            try:
                src = Image.open(self._source_path)
                n = getattr(src, 'n_frames', 1)
                out_frames, durations = [], []
                for i in range(n):
                    src.seek(i)
                    frame = src.convert('RGB').crop((x1, y1, x2, y2))
                    out_frames.append(frame.resize((LCD_SIZE, LCD_SIZE), Image.LANCZOS))
                    durations.append(src.info.get('duration', 100))
                out_frames[0].save(
                    out_path, save_all=True, format='GIF',
                    append_images=out_frames[1:], loop=0, duration=durations,
                )
            except Exception:
                return
        else:
            cropped = self._img_orig.crop((x1, y1, x2, y2))
            result = cropped.resize((LCD_SIZE, LCD_SIZE), Image.LANCZOS).convert('RGB')
            result.save(out_path)

        self._last_applied_source = self._source_path
        save_image_path(out_path)
        self._lcd_thread.signal_reload()
        self._status_label.configure(
            text=f'Applied: {os.path.basename(self._source_path)}',
            text_color='#50B4FF',
        )
        self._refresh_gallery()
