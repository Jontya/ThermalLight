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
from PIL import Image, ImageTk
import customtkinter as ctk

from config import load_config, save_image_path

APP_DATA_DIR = r'C:\ProgramData\TRLCDService'
IMAGE_EXTS = ('.png', '.jpg', '.jpeg', '.bmp', '.gif', '.webp', '.tiff')
LCD_SIZE = 320

ctk.set_appearance_mode('dark')
ctk.set_default_color_theme('blue')


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
        self._drag_start: tuple[int, int] | None = None
        self._thumb_refs: list = []  # keep PhotoImage refs alive

        self.title('Thermalright LCD Editor')
        self.geometry('920x580')
        self.resizable(False, False)
        self.protocol('WM_DELETE_WINDOW', self.withdraw)
        self.withdraw()   # start hidden

        self._build_ui()

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
        for w in self._gallery_frame.winfo_children():
            w.destroy()
        self._thumb_refs.clear()

        os.makedirs(APP_DATA_DIR, exist_ok=True)
        cfg = load_config()
        active = os.path.normcase(cfg.image_path) if cfg.image_path else ''

        files = sorted(
            (f for f in os.listdir(APP_DATA_DIR)
             if f.lower().endswith(IMAGE_EXTS)),
            key=lambda f: os.path.getmtime(os.path.join(APP_DATA_DIR, f)),
            reverse=True,
        )

        for fname in files:
            fpath = os.path.join(APP_DATA_DIR, fname)
            try:
                thumb = Image.open(fpath).convert('RGB')
                thumb.thumbnail((80, 80), Image.LANCZOS)
                photo = ImageTk.PhotoImage(thumb)
                self._thumb_refs.append(photo)

                is_active = os.path.normcase(fpath) == active
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
            except Exception:
                pass

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
        # Dim outside selection with stipple overlay
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

    # ── Mouse events ───────────────────────────────────────────────────

    def _canvas_to_image(self, cx: int, cy: int) -> tuple[int, int]:
        ox, oy = self._img_offset
        s = self._img_scale
        ix = int((cx - ox) / s)
        iy = int((cy - oy) / s)
        iw, ih = self._img_orig.size
        return max(0, min(ix, iw)), max(0, min(iy, ih))

    def _on_mouse_down(self, event) -> None:
        if self._img_orig is None:
            return
        self._drag_start = self._canvas_to_image(event.x, event.y)

    def _on_mouse_drag(self, event) -> None:
        if self._img_orig is None or self._drag_start is None:
            return
        sx, sy = self._drag_start
        ex, ey = self._canvas_to_image(event.x, event.y)
        iw, ih = self._img_orig.size
        dx = ex - sx
        dy = ey - sy
        side = max(1, min(abs(dx), abs(dy)))
        x1 = sx if dx >= 0 else sx - side
        y1 = sy if dy >= 0 else sy - side
        x1 = max(0, min(x1, iw - side))
        y1 = max(0, min(y1, ih - side))
        self._sel = (x1, y1, x1 + side, y1 + side)
        self._draw_selection()

    def _on_mouse_up(self, event) -> None:
        self._drag_start = None

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
        cropped = self._img_orig.crop((x1, y1, x2, y2))
        result = cropped.resize((LCD_SIZE, LCD_SIZE), Image.LANCZOS).convert('RGB')
        result.save(self._source_path)
        save_image_path(self._source_path)
        self._lcd_thread.signal_reload()
        self._status_label.configure(
            text=f'Applied: {os.path.basename(self._source_path)}',
            text_color='#50B4FF',
        )
        self._refresh_gallery()
