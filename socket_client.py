import socket
import tkinter as tk
from tkinter import scrolledtext, messagebox, filedialog, ttk
import threading
import datetime
import os
import struct
import json
import sys
import queue
import uuid
import io
import base64
import urllib.request
import urllib.error

try:
    from PIL import Image, ImageGrab, ImageTk
except ImportError:
    Image = None
    ImageGrab = None
    ImageTk = None

from azure_realtime_stt import (
    AzureRealtimeSttConfig,
    create_realtime_stt_worker,
    is_azure_speech_sdk_available,
)

CAPTION_PROTOCOL_PREFIX = "[[SC_CAPTION_V1]]"
TEXT_WIRE_ENCODING = "utf-8"


class SocketClientGUI:
    def __init__(self, master):
        self.master = master
        master.title("Socket 客户端")
        master.geometry("820x780")
        master.resizable(True, True)
        master.minsize(640, 500)

        self.client_socket = None
        self.is_connected = False
        self.receive_thread = None
        self._closing = False

        self.clipboard_monitor_enabled = False
        self.last_clipboard_content = ""
        self.clipboard_check_id = None

        self.stt_queue = queue.Queue()
        self.stt_thread = None
        self.stt_stop_event = threading.Event()
        self._subtitle_history = []
        self._subtitle_live_line = ""
        self._subtitle_max_lines = 200
        self._caption_stream_id = ""
        self._caption_segment_id = ""
        self._caption_segment_counter = 0
        self._caption_segment_seq = 0
        self._caption_last_partial_text = ""
        self._caption_last_partial_lang = ""
        self._subtitle_final_events = []
        self._manual_segment_active = False
        self._manual_segment_start_index = 0
        self._manual_segment_start_time = None

        self._ai_clipboard_image_bytes = None
        self._ai_clipboard_image_mime = ""
        self._ai_preview_photo = None
        self._ai_request_thread = None
        self._manual_segment_pending_send = False
        self._last_manual_segment_meta = None
        self._quick_panel = None
        self._quick_toggle_stt_button = None
        self._quick_segment_button = None
        self._stt_stopping_requested = False

        if getattr(sys, 'frozen', False) or hasattr(sys, '_MEIPASS'):
            application_path = os.path.dirname(sys.executable)
        else:
            application_path = os.path.dirname(os.path.abspath(__file__))
        self.config_file = os.path.join(application_path, "config.json")

        config = self.load_config()
        self._setup_style()

        self.notebook = ttk.Notebook(master)
        self.notebook.pack(fill=tk.BOTH, expand=True, padx=8, pady=8)

        self._main_frame = ttk.Frame(self.notebook)
        self._ai_frame = ttk.Frame(self.notebook)
        self._settings_frame = ttk.Frame(self.notebook)
        self.notebook.add(self._main_frame, text="   主界面   ")
        self.notebook.add(self._ai_frame, text="   AI   ")
        self.notebook.add(self._settings_frame, text="   ⚙ 设置   ")

        self._build_main_tab(config)
        self._build_ai_tab(config)
        self._build_settings_tab(config)
        self._build_quick_control_panel()

        master.protocol("WM_DELETE_WINDOW", self.on_closing)
        self.master.after(100, self.process_stt_events)

        if config.get("clipboard_monitor", True):
            self.master.after(200, self._enable_clipboard_monitor)

    # ──────────────────────────── Style ────────────────────────────

    def _setup_style(self):
        style = ttk.Style(self.master)
        try:
            style.theme_use("vista")
        except tk.TclError:
            try:
                style.theme_use("clam")
            except tk.TclError:
                pass

        F = ("Segoe UI", 9)
        FB = ("Segoe UI", 9, "bold")
        style.configure("TLabel", font=F)
        style.configure("TButton", font=F, padding=[6, 3])
        style.configure("TCheckbutton", font=F)
        style.configure("TRadiobutton", font=F)
        style.configure("TEntry", font=F)
        style.configure("TCombobox", font=F)
        style.configure("TLabelframe.Label", font=FB)
        style.configure("TNotebook.Tab", font=("Segoe UI", 10), padding=[14, 6])
        style.configure("Primary.TButton", font=FB, padding=[12, 5])

    # ──────────────────────────── Main Tab ────────────────────────────

    def _build_main_tab(self, config):
        f = self._main_frame
        f.columnconfigure(0, weight=1)
        f.rowconfigure(1, weight=1)   # PanedWindow 占满剩余空间
        f.rowconfigure(2, weight=0)   # 状态日志固定

        # ── Row 0: Connection bar ──
        conn = ttk.Frame(f)
        conn.grid(row=0, column=0, sticky="ew", padx=6, pady=(6, 4))

        self._conn_dot = ttk.Label(conn, text="●", foreground="#C62828",
                                   font=("Segoe UI", 14))
        self._conn_dot.pack(side=tk.LEFT, padx=(0, 3))
        self._conn_label = ttk.Label(conn, text="未连接", foreground="#C62828",
                                     font=("Segoe UI", 9, "bold"))
        self._conn_label.pack(side=tk.LEFT, padx=(0, 14))

        self.connect_button = ttk.Button(conn, text="连接服务器",
                                         command=self.connect_to_server,
                                         style="Primary.TButton")
        self.connect_button.pack(side=tk.LEFT, padx=2)
        self.disconnect_button = ttk.Button(conn, text="断开连接",
                                            command=self.disconnect_from_server,
                                            state=tk.DISABLED)
        self.disconnect_button.pack(side=tk.LEFT, padx=2)

        ttk.Button(conn, text="⚙ 设置",
                   command=lambda: self.notebook.select(2)).pack(side=tk.RIGHT)
        self.quick_panel_toggle_button = tk.Button(
            conn,
            text="快捷窗",
            relief=tk.SOLID,
            bd=1,
            padx=8,
            pady=2,
            command=self.toggle_quick_panel,
        )
        self.quick_panel_toggle_button.pack(side=tk.RIGHT, padx=(0, 6))

        # ── Row 1: 垂直 PanedWindow（上：发送+字幕 / 下：接收）──
        v_paned = tk.PanedWindow(f, orient=tk.VERTICAL,
                                 sashwidth=6, sashrelief=tk.FLAT,
                                 bg="#CCCCCC", borderwidth=0)
        v_paned.grid(row=1, column=0, sticky="nsew", padx=6, pady=(0, 4))

        # ── 上半：水平 PanedWindow（左：发送 / 右：字幕）──
        h_paned = tk.PanedWindow(v_paned, orient=tk.HORIZONTAL,
                                 sashwidth=6, sashrelief=tk.FLAT,
                                 bg="#CCCCCC", borderwidth=0)
        v_paned.add(h_paned, stretch="always", minsize=140)

        # ── 发送区（左）──
        send_lf = ttk.LabelFrame(h_paned, text="发送", padding=(6, 4, 6, 4))
        h_paned.add(send_lf, stretch="always", minsize=220)
        send_lf.columnconfigure(0, weight=1)
        send_lf.rowconfigure(0, weight=1)

        self.send_text = scrolledtext.ScrolledText(
            send_lf, wrap=tk.WORD,
            font=("Consolas", 9), relief=tk.FLAT, bd=0,
            highlightthickness=1, highlightbackground="#CCCCCC")
        self.send_text.grid(row=0, column=0, sticky="nsew")
        self.send_text.bind('<Control-Return>', lambda e: self.send_message())

        send_ctrl = ttk.Frame(send_lf)
        send_ctrl.grid(row=1, column=0, sticky="ew", pady=(4, 0))
        send_ctrl.columnconfigure(1, weight=1)

        mode_f = ttk.Frame(send_ctrl)
        mode_f.grid(row=0, column=0, sticky="w")
        self.send_mode_var = tk.StringVar(value="text")
        ttk.Radiobutton(mode_f, text="文本", variable=self.send_mode_var,
                        value="text", command=self.on_send_mode_change).pack(
            side=tk.LEFT, padx=(0, 4))
        ttk.Radiobutton(mode_f, text="文件", variable=self.send_mode_var,
                        value="file", command=self.on_send_mode_change).pack(
            side=tk.LEFT)

        self.file_frame = ttk.Frame(send_ctrl)
        self.file_frame.grid(row=0, column=1, sticky="ew", padx=6)
        self.file_frame.grid_remove()
        self.file_path_var = tk.StringVar()
        ttk.Entry(self.file_frame, textvariable=self.file_path_var,
                  width=28).pack(side=tk.LEFT, padx=(0, 4))
        ttk.Button(self.file_frame, text="浏览…",
                   command=self.browse_file).pack(side=tk.LEFT)

        btn_f = ttk.Frame(send_ctrl)
        btn_f.grid(row=0, column=2, sticky="e")
        ttk.Button(btn_f, text="清空",
                   command=self.clear_send_area).pack(side=tk.LEFT, padx=(0, 4))
        self.send_button = ttk.Button(btn_f, text="发送  Ctrl+Enter",
                                      command=self.send_message,
                                      style="Primary.TButton",
                                      state=tk.DISABLED)
        self.send_button.pack(side=tk.LEFT)

        # ── 字幕区（右）──
        sub_lf = ttk.LabelFrame(h_paned, text="实时字幕", padding=4)
        h_paned.add(sub_lf, stretch="always", minsize=160)
        sub_lf.columnconfigure(0, weight=1)
        sub_lf.rowconfigure(1, weight=1)

        stt_ctrl = ttk.Frame(sub_lf)
        stt_ctrl.grid(row=0, column=0, sticky="ew", pady=(0, 4))
        self.stt_start_button = ttk.Button(stt_ctrl, text="▶ 启动",
                                           command=self.start_stt_thread)
        self.stt_start_button.pack(side=tk.LEFT, padx=(0, 4))
        self.stt_stop_button = ttk.Button(stt_ctrl, text="■ 停止",
                                          command=self.stop_stt_thread,
                                          state=tk.DISABLED)
        self.stt_stop_button.pack(side=tk.LEFT)
        self.manual_segment_button = ttk.Button(
            stt_ctrl,
            text="片段开始",
            command=self.toggle_manual_segment,
        )
        self.manual_segment_button.pack(side=tk.LEFT, padx=(8, 0))
        ttk.Button(
            stt_ctrl,
            text="粘贴图片",
            command=self.paste_ai_image,
        ).pack(side=tk.LEFT, padx=(8, 0))
        self._stt_dot = ttk.Label(stt_ctrl, text="●", foreground="#CCCCCC",
                                  font=("Segoe UI", 12))
        self._stt_dot.pack(side=tk.LEFT, padx=(6, 0))

        self.subtitle_text = scrolledtext.ScrolledText(
            sub_lf, state=tk.DISABLED, wrap=tk.WORD,
            font=("Consolas", 9), relief=tk.FLAT, bd=0,
            highlightthickness=1, highlightbackground="#CCCCCC")
        self.subtitle_text.grid(row=1, column=0, sticky="nsew")
        ttk.Button(sub_lf, text="清空字幕",
                   command=self.clear_subtitle_area).grid(
            row=2, column=0, sticky="e", pady=(3, 0))

        # 默认右侧更宽（字幕区）
        f.after(120, lambda: self._set_main_paned_ratio(h_paned, left_ratio=0.42))

        # ── 下半：接收区 ──
        recv_lf = ttk.LabelFrame(v_paned, text="接收消息", padding=4)
        v_paned.add(recv_lf, stretch="always", minsize=80)
        recv_lf.columnconfigure(0, weight=1)
        recv_lf.rowconfigure(0, weight=1)

        self.receive_text = scrolledtext.ScrolledText(
            recv_lf, state=tk.DISABLED, wrap=tk.WORD,
            font=("Consolas", 9), relief=tk.FLAT, bd=0,
            highlightthickness=1, highlightbackground="#CCCCCC")
        self.receive_text.grid(row=0, column=0, sticky="nsew")
        ttk.Button(recv_lf, text="清空接收",
                   command=self.clear_receive_area).grid(
            row=1, column=0, sticky="e", pady=(3, 0))

        # ── Row 2: Status log ──
        status_lf = ttk.LabelFrame(f, text="状态日志", padding=(6, 2, 6, 4))
        status_lf.grid(row=2, column=0, sticky="nsew", padx=6, pady=(0, 6))
        status_lf.columnconfigure(0, weight=1)
        status_lf.rowconfigure(0, weight=1)

        self.status_text = scrolledtext.ScrolledText(
            status_lf, height=3, state=tk.DISABLED, wrap=tk.WORD,
            font=("Segoe UI", 8), relief=tk.FLAT, bd=0,
            background="#F7F7F7",
            highlightthickness=1, highlightbackground="#CCCCCC")
        self.status_text.grid(row=0, column=0, sticky="nsew")
        ttk.Button(status_lf, text="清空",
                   command=self.clear_status_area).grid(
            row=1, column=0, sticky="e", pady=(2, 0))

    def _set_main_paned_ratio(self, paned, left_ratio=0.42):
        try:
            paned.update_idletasks()
            width = paned.winfo_width()
            if width <= 40:
                return
            sash_x = max(160, int(width * left_ratio))
            paned.sash_place(0, sash_x, 1)
        except Exception:
            pass

    def _build_quick_control_panel(self):
        panel = tk.Toplevel(self.master)
        panel.geometry("300x78+40+40")
        panel.resizable(False, False)
        panel.attributes("-topmost", True)
        panel.overrideredirect(True)
        panel.protocol("WM_DELETE_WINDOW", panel.withdraw)
        self._quick_panel = panel

        wrap = ttk.Frame(panel, padding=(4, 4, 4, 4))
        wrap.pack(fill=tk.BOTH, expand=True)
        self._quick_wrap = wrap

        row1 = ttk.Frame(wrap)
        row1.pack(fill=tk.X, pady=(0, 3))
        row2 = ttk.Frame(wrap)
        row2.pack(fill=tk.X)

        self._quick_stt_dot = tk.Label(
            row1, text="●", fg="#CCCCCC", bg=panel.cget("bg"), font=("Segoe UI", 10)
        )
        self._quick_stt_dot.pack(side=tk.LEFT, padx=(2, 4))

        self._quick_toggle_stt_button = ttk.Button(
            row1,
            text="启动",
            width=6,
            command=self._toggle_stt_from_quick_panel,
        )
        self._quick_toggle_stt_button.pack(side=tk.LEFT, padx=(0, 4))

        self._quick_segment_button = ttk.Button(
            row1,
            text="片段开始",
            width=8,
            command=self.toggle_manual_segment,
        )
        self._quick_segment_button.pack(side=tk.LEFT, padx=(0, 4))

        ttk.Button(
            row1,
            text="粘贴图片",
            width=8,
            command=self.paste_ai_image,
        ).pack(side=tk.LEFT)

        self._quick_scene_buttons_frame = ttk.Frame(row2)
        self._quick_scene_buttons_frame.pack(side=tk.LEFT, fill=tk.X, expand=True)
        self._quick_scene_buttons = {}
        self._render_quick_scene_buttons()

        self._quick_drag_x = 0
        self._quick_drag_y = 0
        for widget in (panel, wrap, row1, row2, self._quick_stt_dot):
            widget.bind("<ButtonPress-1>", self._on_quick_drag_start)
            widget.bind("<B1-Motion>", self._on_quick_drag_move)

        self._sync_quick_panel_buttons()
        self._sync_quick_panel_toggle_button()

    def _on_quick_drag_start(self, event):
        self._quick_drag_x = event.x_root
        self._quick_drag_y = event.y_root

    def _on_quick_drag_move(self, event):
        if not self._quick_panel:
            return
        dx = event.x_root - self._quick_drag_x
        dy = event.y_root - self._quick_drag_y
        x = self._quick_panel.winfo_x() + dx
        y = self._quick_panel.winfo_y() + dy
        self._quick_panel.geometry(f"+{x}+{y}")
        self._quick_drag_x = event.x_root
        self._quick_drag_y = event.y_root

    def _render_quick_scene_buttons(self):
        if not hasattr(self, "_quick_scene_buttons_frame"):
            return
        for child in self._quick_scene_buttons_frame.winfo_children():
            child.destroy()
        self._quick_scene_buttons = {}

        templates = self._current_prompt_templates()
        count = 0
        for scene in templates.keys():
            btn = tk.Button(
                self._quick_scene_buttons_frame,
                text=scene,
                relief=tk.SOLID,
                bd=1,
                padx=4,
                pady=1,
                font=("Segoe UI", 8),
                command=lambda s=scene: self._select_ai_scene(s),
            )
            btn.pack(side=tk.LEFT, padx=(0, 3))
            self._quick_scene_buttons[scene] = btn
            count += 1
            if count >= 6:
                break
        self._refresh_quick_scene_button_styles()

    def _refresh_quick_scene_button_styles(self):
        if not hasattr(self, "_quick_scene_buttons"):
            return
        selected = (self.ai_prompt_scene_var.get() or "").strip()
        for scene, btn in self._quick_scene_buttons.items():
            active = scene == selected
            btn.config(
                bg="#1976D2" if active else "#F0F0F0",
                fg="white" if active else "#222222",
            )

    def _toggle_stt_from_quick_panel(self):
        if self.stt_thread and self.stt_thread.is_alive():
            self.stop_stt_thread()
        else:
            self.start_stt_thread()

    def show_quick_panel(self):
        if not self._quick_panel:
            return
        self._quick_panel.deiconify()
        self._quick_panel.lift()
        self._quick_panel.attributes("-topmost", True)
        self._sync_quick_panel_toggle_button()

    def _sync_quick_panel_toggle_button(self):
        if not hasattr(self, "quick_panel_toggle_button"):
            return
        visible = bool(self._quick_panel and self._quick_panel.winfo_viewable())
        if visible:
            self.quick_panel_toggle_button.config(bg="#1976D2", fg="white")
        else:
            self.quick_panel_toggle_button.config(bg="#F0F0F0", fg="#222222")

    def toggle_quick_panel(self):
        if not self._quick_panel:
            return
        visible = bool(self._quick_panel.winfo_viewable())
        if visible:
            self._quick_panel.withdraw()
        else:
            self._quick_panel.deiconify()
            self._quick_panel.lift()
            self._quick_panel.attributes("-topmost", True)
        self._sync_quick_panel_toggle_button()

    def _sync_quick_panel_buttons(self):
        if not self._quick_panel:
            return
        if self._quick_toggle_stt_button:
            stt_running = bool(
                self.stt_thread
                and self.stt_thread.is_alive()
                and not self._stt_stopping_requested
            )
            self._quick_toggle_stt_button.config(text="停止" if stt_running else "启动")
        if hasattr(self, "_quick_stt_dot") and self._quick_stt_dot:
            self._quick_stt_dot.config(fg="#2E7D32" if stt_running else "#CCCCCC")
        if self._quick_segment_button:
            self._quick_segment_button.config(
                text="片段结束" if self._manual_segment_active else "片段开始"
            )

    def _build_ai_tab(self, config):
        f = self._ai_frame
        f.columnconfigure(0, weight=1)
        f.rowconfigure(2, weight=1)

        input_lf = ttk.LabelFrame(f, text="AI 输入", padding=(8, 6, 8, 6))
        input_lf.grid(row=0, column=0, sticky="nsew", padx=8, pady=(8, 4))
        input_lf.columnconfigure(0, weight=1)
        input_lf.rowconfigure(0, weight=1)

        self.ai_prompt_text = scrolledtext.ScrolledText(
            input_lf, wrap=tk.WORD,
            font=("Consolas", 9), relief=tk.FLAT, bd=0,
            highlightthickness=1, highlightbackground="#CCCCCC",
            height=8)
        self.ai_prompt_text.grid(row=0, column=0, sticky="nsew")

        ai_ctrl = ttk.Frame(input_lf)
        ai_ctrl.grid(row=1, column=0, sticky="ew", pady=(6, 0))
        ai_ctrl.columnconfigure(3, weight=1)
        ttk.Label(ai_ctrl, text="提示词:").grid(row=0, column=0, sticky="w")
        self.ai_prompt_scene_var = tk.StringVar(
            value=config.get("ai_prompt_scene", "阅读")
        )
        self.ai_scene_buttons_frame = ttk.Frame(ai_ctrl)
        self.ai_scene_buttons_frame.grid(row=0, column=1, sticky="w", padx=(6, 10))
        self._ai_scene_buttons = {}
        self._render_ai_scene_buttons()
        ttk.Button(ai_ctrl, text="粘贴图片", command=self.paste_ai_image).grid(
            row=0, column=2, sticky="w")
        self.ai_image_info_var = tk.StringVar(value="未附加图片")
        ttk.Label(ai_ctrl, textvariable=self.ai_image_info_var).grid(
            row=0, column=3, sticky="w", padx=(8, 0))
        ttk.Button(ai_ctrl, text="清空图片", command=self.clear_ai_image).grid(
            row=0, column=4, sticky="e", padx=(8, 0))
        self.ai_send_button = ttk.Button(
            ai_ctrl, text="发送到 AI", style="Primary.TButton",
            command=self.send_ai_request)
        self.ai_send_button.grid(row=0, column=5, sticky="e", padx=(8, 0))

        preview_lf = ttk.LabelFrame(f, text="图片预览", padding=(8, 6, 8, 6))
        preview_lf.grid(row=1, column=0, sticky="ew", padx=8, pady=4)
        preview_lf.columnconfigure(0, weight=1)
        self.ai_image_preview = ttk.Label(
            preview_lf, text="(可选) 点击“粘贴图片”从剪贴板读取图片")
        self.ai_image_preview.grid(row=0, column=0, sticky="w")

        output_lf = ttk.LabelFrame(f, text="AI 返回结果", padding=(8, 6, 8, 6))
        output_lf.grid(row=2, column=0, sticky="nsew", padx=8, pady=(4, 8))
        output_lf.columnconfigure(0, weight=1)
        output_lf.rowconfigure(0, weight=1)
        self.ai_result_text = scrolledtext.ScrolledText(
            output_lf, wrap=tk.WORD, state=tk.DISABLED,
            font=("Consolas", 9), relief=tk.FLAT, bd=0,
            highlightthickness=1, highlightbackground="#CCCCCC")
        self.ai_result_text.grid(row=0, column=0, sticky="nsew")

    # ──────────────────────────── Settings Tab ────────────────────────────

    def _build_settings_tab(self, config):
        outer = self._settings_frame
        outer.columnconfigure(0, weight=1)
        outer.rowconfigure(0, weight=1)

        canvas = tk.Canvas(outer, highlightthickness=0, bd=0)
        sb = ttk.Scrollbar(outer, orient=tk.VERTICAL, command=canvas.yview)
        canvas.configure(yscrollcommand=sb.set)
        sb.grid(row=0, column=1, sticky="ns")
        canvas.grid(row=0, column=0, sticky="nsew")

        inner = ttk.Frame(canvas, padding=(4, 4, 4, 4))
        win_id = canvas.create_window((0, 0), window=inner, anchor="nw")

        def _resize_inner(event):
            canvas.configure(scrollregion=canvas.bbox("all"))

        def _resize_canvas(event):
            canvas.itemconfig(win_id, width=event.width)

        inner.bind("<Configure>", _resize_inner)
        canvas.bind("<Configure>", _resize_canvas)

        def _on_wheel(event):
            canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")

        canvas.bind_all("<MouseWheel>", _on_wheel)

        inner.columnconfigure(0, weight=1)
        r = 0

        # ── Server settings ──
        srv = ttk.LabelFrame(inner, text="服务器连接", padding=10)
        srv.grid(row=r, column=0, sticky="ew", pady=(0, 10))
        srv.columnconfigure(1, weight=1)
        srv.columnconfigure(3, weight=1)
        r += 1

        ttk.Label(srv, text="服务器地址:").grid(
            row=0, column=0, sticky="w", pady=4, padx=(0, 8))
        self.host_entry = ttk.Entry(srv, width=24)
        self.host_entry.grid(row=0, column=1, sticky="w", pady=4, padx=(0, 20))
        self.host_entry.insert(0, config.get("host", "127.0.0.1"))

        ttk.Label(srv, text="端口:").grid(
            row=0, column=2, sticky="w", pady=4, padx=(0, 8))
        self.port_entry = ttk.Entry(srv, width=10)
        self.port_entry.grid(row=0, column=3, sticky="w", pady=4)
        self.port_entry.insert(0, config.get("port", "8888"))

        # ── Encoding ──
        enc = ttk.LabelFrame(inner, text="编码格式", padding=10)
        enc.grid(row=r, column=0, sticky="ew", pady=(0, 10))
        r += 1

        self.encoding_var = tk.StringVar(value=config.get("encoding", "utf-8"))
        enc_inner = ttk.Frame(enc)
        enc_inner.pack(anchor="w")
        for val in ["utf-8", "gbk", "gb2312", "ascii", "latin-1"]:
            ttk.Radiobutton(enc_inner, text=val, variable=self.encoding_var,
                            value=val, command=self.on_encoding_change).pack(
                side=tk.LEFT, padx=4)

        # ── Misc ──
        misc = ttk.LabelFrame(inner, text="功能选项", padding=10)
        misc.grid(row=r, column=0, sticky="ew", pady=(0, 10))
        r += 1

        self.clipboard_monitor_var = tk.BooleanVar(
            value=config.get("clipboard_monitor", True))
        ttk.Checkbutton(
            misc,
            text="启用粘贴板监听（自动将复制的文本填入发送框）",
            variable=self.clipboard_monitor_var,
            command=self.toggle_clipboard_monitor).pack(anchor="w")

        # ── STT ──
        stt = ttk.LabelFrame(inner, text="实时字幕 — Azure Speech 配置",
                              padding=10)
        stt.grid(row=r, column=0, sticky="ew", pady=(0, 10))
        stt.columnconfigure(1, weight=1)
        stt.columnconfigure(3, weight=1)
        r += 1

        chk_row = ttk.Frame(stt)
        chk_row.grid(row=0, column=0, columnspan=4, sticky="w", pady=(0, 8))
        self.stt_enabled_var = tk.BooleanVar(value=config.get("stt_enabled", False))
        ttk.Checkbutton(
            chk_row,
            text="启用实时字幕",
            variable=self.stt_enabled_var,
            command=self.on_stt_config_change,
        ).pack(side=tk.LEFT, padx=(0, 20))
        self.stt_send_final_var = tk.BooleanVar(
            value=config.get("stt_send_final_to_socket", False)
        )
        ttk.Checkbutton(
            chk_row,
            text="字幕流式转发到 Socket",
            variable=self.stt_send_final_var,
            command=self.on_stt_config_change,
        ).pack(side=tk.LEFT)

        supported_langs = ["zh-CN", "en-US", "ja-JP", "ko-KR", "fr-FR",
                           "de-DE", "es-ES", "ru-RU", "it-IT", "pt-BR"]

        ttk.Label(stt, text="识别语言:").grid(
            row=1, column=0, sticky="w", pady=3, padx=(0, 8))
        self.stt_language_var = tk.StringVar(
            value=config.get("stt_language", "zh-CN"))
        lang_cb = ttk.Combobox(
            stt,
            textvariable=self.stt_language_var,
            values=supported_langs,
            state="readonly",
            width=18,
        )
        lang_cb.grid(row=1, column=1, sticky="w", pady=3)
        lang_cb.bind("<<ComboboxSelected>>", lambda _: self.on_stt_config_change())

        ttk.Label(stt, text="自动识别语言:").grid(
            row=1, column=2, sticky="w", pady=3, padx=(12, 8))
        self.stt_auto_detect_languages_entry = ttk.Entry(stt, width=36)
        self.stt_auto_detect_languages_entry.grid(
            row=1, column=3, sticky="ew", pady=3)
        self.stt_auto_detect_languages_entry.insert(
            0, config.get("stt_auto_detect_languages", ""))

        ttk.Label(stt, text="区域 (Region):").grid(
            row=2, column=0, sticky="w", pady=3, padx=(0, 8))
        self.stt_region_entry = ttk.Entry(stt, width=28)
        self.stt_region_entry.grid(row=2, column=1, sticky="ew", pady=3)
        self.stt_region_entry.insert(0, config.get("stt_region", "southeastasia"))

        ttk.Label(stt, text="终结点 (可选):").grid(
            row=2, column=2, sticky="w", pady=3, padx=(12, 8))
        self.stt_endpoint_entry = ttk.Entry(stt, width=36)
        self.stt_endpoint_entry.grid(row=2, column=3, sticky="ew", pady=3)
        self.stt_endpoint_entry.insert(0, config.get("stt_endpoint", ""))

        ttk.Label(stt, text="Endpoint ID (可选):").grid(
            row=3, column=0, sticky="w", pady=3, padx=(0, 8))
        self.stt_endpoint_id_entry = ttk.Entry(stt, width=28)
        self.stt_endpoint_id_entry.grid(row=3, column=1, sticky="ew", pady=3)
        self.stt_endpoint_id_entry.insert(0, config.get("stt_endpoint_id", ""))

        ttk.Label(stt, text="订阅 Key (可选):").grid(
            row=3, column=2, sticky="w", pady=3, padx=(12, 8))
        self.stt_key_entry = ttk.Entry(stt, width=36, show="*")
        self.stt_key_entry.grid(row=3, column=3, sticky="ew", pady=3)
        self.stt_key_entry.insert(0, config.get("stt_key", ""))

        flag_row = ttk.Frame(stt)
        flag_row.grid(row=4, column=0, columnspan=4, sticky="w", pady=(4, 6))
        self.stt_word_level_var = tk.BooleanVar(
            value=config.get("stt_word_level_timestamps", False)
        )
        ttk.Checkbutton(
            flag_row,
            text="词级时间戳",
            variable=self.stt_word_level_var,
            command=self.on_stt_config_change,
        ).pack(side=tk.LEFT, padx=(0, 16))
        self.stt_dictation_var = tk.BooleanVar(
            value=config.get("stt_dictation_enabled", False)
        )
        ttk.Checkbutton(
            flag_row,
            text="听写模式",
            variable=self.stt_dictation_var,
            command=self.on_stt_config_change,
        ).pack(side=tk.LEFT, padx=(0, 16))
        self.stt_audio_logging_var = tk.BooleanVar(
            value=config.get("stt_audio_logging_enabled", False)
        )
        ttk.Checkbutton(
            flag_row,
            text="启用音频日志",
            variable=self.stt_audio_logging_var,
            command=self.on_stt_config_change,
        ).pack(side=tk.LEFT)

        ttk.Label(stt, text="脏词处理:").grid(
            row=5, column=0, sticky="w", pady=3, padx=(0, 8))
        self.stt_profanity_var = tk.StringVar(
            value=config.get("stt_profanity", "Masked"))
        profanity_cb = ttk.Combobox(
            stt,
            textvariable=self.stt_profanity_var,
            values=["Masked", "Raw", "Removed"],
            state="readonly",
            width=18,
        )
        profanity_cb.grid(row=5, column=1, sticky="w", pady=3)
        profanity_cb.bind("<<ComboboxSelected>>",
                          lambda _: self.on_stt_config_change())

        ttk.Label(stt, text="输出格式:").grid(
            row=5, column=2, sticky="w", pady=3, padx=(12, 8))
        self.stt_output_format_var = tk.StringVar(
            value=config.get("stt_output_format", "Simple"))
        output_cb = ttk.Combobox(
            stt,
            textvariable=self.stt_output_format_var,
            values=["Simple", "Detailed"],
            state="readonly",
            width=18,
        )
        output_cb.grid(row=5, column=3, sticky="w", pady=3)
        output_cb.bind("<<ComboboxSelected>>",
                       lambda _: self.on_stt_config_change())

        ttk.Label(stt, text="语言识别模式:").grid(
            row=6, column=0, sticky="w", pady=3, padx=(0, 8))
        self.stt_language_id_mode_var = tk.StringVar(
            value=config.get("stt_language_id_mode", "AtStart"))
        lid_mode_cb = ttk.Combobox(
            stt,
            textvariable=self.stt_language_id_mode_var,
            values=["AtStart", "Continuous"],
            state="readonly",
            width=18,
        )
        lid_mode_cb.grid(row=6, column=1, sticky="w", pady=3)
        lid_mode_cb.bind("<<ComboboxSelected>>",
                         lambda _: self.on_stt_config_change())

        ttk.Label(stt, text="分段策略:").grid(
            row=6, column=2, sticky="w", pady=3, padx=(12, 8))
        self.stt_segmentation_strategy_var = tk.StringVar(
            value=config.get("stt_segmentation_strategy", "Default"))
        segmentation_cb = ttk.Combobox(
            stt,
            textvariable=self.stt_segmentation_strategy_var,
            values=["Default", "Semantic"],
            state="readonly",
            width=18,
        )
        segmentation_cb.grid(row=6, column=3, sticky="w", pady=3)
        segmentation_cb.bind("<<ComboboxSelected>>",
                             lambda _: self.on_stt_config_change())

        ttk.Label(stt, text="增量稳定阈值:").grid(
            row=7, column=0, sticky="w", pady=3, padx=(0, 8))
        self.stt_stable_partial_entry = ttk.Entry(stt, width=18)
        self.stt_stable_partial_entry.grid(row=7, column=1, sticky="w", pady=3)
        self.stt_stable_partial_entry.insert(
            0, config.get("stt_stable_partial_result_threshold", "0"))

        ttk.Label(stt, text="分段静音超时(ms):").grid(
            row=7, column=2, sticky="w", pady=3, padx=(12, 8))
        self.stt_segmentation_silence_entry = ttk.Entry(stt, width=18)
        self.stt_segmentation_silence_entry.grid(
            row=7, column=3, sticky="w", pady=3)
        self.stt_segmentation_silence_entry.insert(
            0, config.get("stt_segmentation_silence_timeout_ms", "0"))

        ttk.Label(stt, text="起始静音超时(ms):").grid(
            row=8, column=0, sticky="w", pady=3, padx=(0, 8))
        self.stt_initial_silence_entry = ttk.Entry(stt, width=18)
        self.stt_initial_silence_entry.grid(row=8, column=1, sticky="w", pady=3)
        self.stt_initial_silence_entry.insert(
            0, config.get("stt_initial_silence_timeout_ms", "0"))

        ttk.Label(stt, text="短语提示:").grid(
            row=8, column=2, sticky="w", pady=3, padx=(12, 8))
        self.stt_phrase_list_entry = ttk.Entry(stt, width=36)
        self.stt_phrase_list_entry.grid(row=8, column=3, sticky="ew", pady=3)
        self.stt_phrase_list_entry.insert(0, config.get("stt_phrase_list", ""))

        # ── AI ──
        ai = ttk.LabelFrame(inner, text="AI 配置（豆包）", padding=10)
        ai.grid(row=r, column=0, sticky="ew", pady=(0, 10))
        ai.columnconfigure(1, weight=1)
        r += 1

        ttk.Label(ai, text="Base URL:").grid(
            row=0, column=0, sticky="w", pady=3, padx=(0, 8))
        self.ai_base_url_entry = ttk.Entry(ai, width=64)
        self.ai_base_url_entry.grid(row=0, column=1, sticky="ew", pady=3)
        self.ai_base_url_entry.insert(
            0, config.get("ai_base_url", "https://ark.cn-beijing.volces.com/api/v3/chat/completions")
        )

        ttk.Label(ai, text="API Key:").grid(
            row=1, column=0, sticky="w", pady=3, padx=(0, 8))
        self.ai_api_key_entry = ttk.Entry(ai, width=64, show="*")
        self.ai_api_key_entry.grid(row=1, column=1, sticky="ew", pady=3)
        self.ai_api_key_entry.insert(0, config.get("ai_api_key", ""))

        ttk.Label(ai, text="模型:").grid(
            row=2, column=0, sticky="w", pady=3, padx=(0, 8))
        self.ai_model_var = tk.StringVar(
            value=config.get("ai_model", "doubao-seed-2-0-lite-260215")
        )
        ai_model_cb = ttk.Combobox(
            ai,
            textvariable=self.ai_model_var,
            values=[
                "doubao-seed-2-0-lite-260215",
                "doubao-seed-2-0-mini-260215",
                "doubao-seed-2-0-pro-260215",
            ],
            state="readonly",
            width=40,
        )
        ai_model_cb.grid(row=2, column=1, sticky="w", pady=3)
        ai_model_cb.bind("<<ComboboxSelected>>", lambda _: self.save_config())

        prompts = ttk.LabelFrame(inner, text="AI 提示词模板（长文本，可扩展按钮）", padding=10)
        prompts.grid(row=r, column=0, sticky="ew", pady=(0, 10))
        prompts.columnconfigure(0, weight=1)
        r += 1
        ttk.Label(
            prompts,
            text="可编辑 JSON（键=按钮名，值=模板文本）。示例：{\"阅读\":\"...\",\"听力\":\"...\"}",
            foreground="#666666",
        ).grid(row=0, column=0, sticky="w", pady=(0, 4))
        self.ai_prompt_templates_text = scrolledtext.ScrolledText(
            prompts,
            wrap=tk.WORD,
            height=14,
            font=("Consolas", 9),
            relief=tk.FLAT,
            bd=0,
            highlightthickness=1,
            highlightbackground="#CCCCCC",
        )
        self.ai_prompt_templates_text.grid(row=1, column=0, sticky="ew")
        templates_json = json.dumps(
            config.get("ai_prompt_templates", self._default_prompt_templates()),
            ensure_ascii=False,
            indent=2,
        )
        self.ai_prompt_templates_text.insert(1.0, templates_json)

        # Hint
        ttk.Label(
            inner,
            text="提示：Key/Region/Endpoint 也可通过环境变量设置："
                 " AZURE_SPEECH_KEY / AZURE_SPEECH_REGION / AZURE_SPEECH_ENDPOINT\n"
                 "自动识别语言与短语提示支持逗号分隔；未填 Endpoint 且启用 Continuous 语言识别时，会自动切到 Speech v2 endpoint。",
            foreground="#888888",
            font=("Segoe UI", 8)).grid(
            row=r, column=0, sticky="w", pady=(0, 12))
        r += 1

        # Save
        ttk.Button(inner, text="保存所有设置", command=self.save_config,
                   style="Primary.TButton").grid(
            row=r, column=0, sticky="e", pady=4)
        self._render_ai_scene_buttons()

    # ──────────────────────────── Connection state helpers ────────────────────────────

    def _set_connected_ui(self, connected: bool):
        if connected:
            self._conn_dot.config(foreground="#2E7D32")
            self._conn_label.config(text="已连接", foreground="#2E7D32")
        else:
            self._conn_dot.config(foreground="#C62828")
            self._conn_label.config(text="未连接", foreground="#C62828")

    # ──────────────────────────── Config ────────────────────────────

    def _default_prompt_templates(self):
        return {
            "阅读": "请以阅读理解老师身份，提炼关键信息，给出简要讲解与答题建议。",
            "听力": "请以听力教练身份，先总结主旨，再给关键词、易错点和跟读建议。",
            "口语": "请以口语考官身份，给出表达改进、地道替换和可直接跟读的示例回答。",
            "写作": "请以写作老师身份，先批改语法和逻辑，再给优化版本与可复用句型。",
        }

    def _parse_prompt_templates(self, raw_text):
        text = (raw_text or "").strip()
        if not text:
            return self._default_prompt_templates()
        data = json.loads(text)
        if not isinstance(data, dict):
            raise ValueError("提示词模板必须是 JSON 对象")
        cleaned = {}
        for key, value in data.items():
            k = str(key).strip()
            if not k:
                continue
            cleaned[k] = str(value or "").strip()
        if not cleaned:
            raise ValueError("提示词模板不能为空")
        return cleaned

    def _current_prompt_templates(self):
        if hasattr(self, "ai_prompt_templates_text"):
            raw = self.ai_prompt_templates_text.get(1.0, tk.END)
            try:
                return self._parse_prompt_templates(raw)
            except Exception:
                pass
        return self._default_prompt_templates()

    def _render_ai_scene_buttons(self):
        if not hasattr(self, "ai_scene_buttons_frame"):
            return
        for child in self.ai_scene_buttons_frame.winfo_children():
            child.destroy()
        self._ai_scene_buttons = {}

        templates = self._current_prompt_templates()
        col = 0
        selected = self.ai_prompt_scene_var.get().strip()
        if selected and selected not in templates:
            self.ai_prompt_scene_var.set("")
            selected = ""

        for scene in templates.keys():
            btn = tk.Button(
                self.ai_scene_buttons_frame,
                text=scene,
                relief=tk.SOLID,
                bd=1,
                padx=8,
                pady=2,
                command=lambda s=scene: self._select_ai_scene(s),
            )
            btn.grid(row=0, column=col, padx=(0, 4))
            self._ai_scene_buttons[scene] = btn
            col += 1

        none_btn = tk.Button(
            self.ai_scene_buttons_frame,
            text="无",
            relief=tk.SOLID,
            bd=1,
            padx=8,
            pady=2,
            command=lambda: self._select_ai_scene(""),
        )
        none_btn.grid(row=0, column=col)
        self._ai_scene_buttons[""] = none_btn
        self._refresh_ai_scene_button_styles()

    def _select_ai_scene(self, scene):
        self.ai_prompt_scene_var.set(scene)
        self._refresh_ai_scene_button_styles()
        self.save_config()

    def _refresh_ai_scene_button_styles(self):
        selected = self.ai_prompt_scene_var.get().strip()
        for scene, btn in self._ai_scene_buttons.items():
            key = scene.strip()
            active = (key == selected) if key else (selected == "")
            if active:
                btn.config(bg="#1976D2", fg="white")
            else:
                btn.config(bg="#F0F0F0", fg="#222222")
        self._refresh_quick_scene_button_styles()

    def load_config(self):
        default = {
            "host": "127.0.0.1",
            "port": "8888",
            "encoding": "utf-8",
            "clipboard_monitor": True,
            "stt_enabled": False,
            "stt_language": "zh-CN",
            "stt_region": "southeastasia",
            "stt_endpoint": "",
            "stt_endpoint_id": "",
            "stt_key": "",
            "stt_auto_detect_languages": "",
            "stt_language_id_mode": "AtStart",
            "stt_profanity": "Masked",
            "stt_output_format": "Simple",
            "stt_word_level_timestamps": False,
            "stt_dictation_enabled": False,
            "stt_audio_logging_enabled": False,
            "stt_stable_partial_result_threshold": "0",
            "stt_segmentation_silence_timeout_ms": "0",
            "stt_initial_silence_timeout_ms": "0",
            "stt_segmentation_strategy": "Default",
            "stt_phrase_list": "",
            "stt_send_final_to_socket": False,
            "ai_base_url": "https://ark.cn-beijing.volces.com/api/v3/chat/completions",
            "ai_api_key": "",
            "ai_model": "doubao-seed-2-0-lite-260215",
            "ai_prompt_scene": "阅读",
            "ai_prompt_templates": self._default_prompt_templates(),
        }
        if os.path.exists(self.config_file):
            try:
                with open(self.config_file, 'r', encoding='utf-8') as f:
                    saved = json.load(f)
                    default.update(saved)
                    return default
            except Exception as e:
                self.master.after(
                    0, lambda: self.update_status(f"加载配置失败: {e}，使用默认值"))
                return default
        else:
            self.save_config(default)
            return default

    def get_stt_settings(self):
        return {
            "stt_enabled": self.stt_enabled_var.get(),
            "stt_language": self.stt_language_var.get().strip() or "zh-CN",
            "stt_region": self.stt_region_entry.get().strip(),
            "stt_endpoint": self.stt_endpoint_entry.get().strip(),
            "stt_endpoint_id": self.stt_endpoint_id_entry.get().strip(),
            "stt_key": self.stt_key_entry.get().strip(),
            "stt_auto_detect_languages":
                self.stt_auto_detect_languages_entry.get().strip(),
            "stt_language_id_mode":
                self.stt_language_id_mode_var.get().strip() or "AtStart",
            "stt_profanity": self.stt_profanity_var.get().strip() or "Masked",
            "stt_output_format":
                self.stt_output_format_var.get().strip() or "Simple",
            "stt_word_level_timestamps": self.stt_word_level_var.get(),
            "stt_dictation_enabled": self.stt_dictation_var.get(),
            "stt_audio_logging_enabled": self.stt_audio_logging_var.get(),
            "stt_stable_partial_result_threshold":
                self.stt_stable_partial_entry.get().strip(),
            "stt_segmentation_silence_timeout_ms":
                self.stt_segmentation_silence_entry.get().strip(),
            "stt_initial_silence_timeout_ms":
                self.stt_initial_silence_entry.get().strip(),
            "stt_segmentation_strategy":
                self.stt_segmentation_strategy_var.get().strip() or "Default",
            "stt_phrase_list": self.stt_phrase_list_entry.get().strip(),
            "stt_send_final_to_socket": self.stt_send_final_var.get(),
        }

    def save_config(self, config=None):
        if config is None:
            prompt_templates = self._default_prompt_templates()
            try:
                prompt_templates = self._parse_prompt_templates(
                    self.ai_prompt_templates_text.get(1.0, tk.END)
                )
            except Exception as e:
                self.update_status(f"提示词模板 JSON 无效，已使用上次有效配置: {e}")
            config = {
                "host": self.host_entry.get().strip(),
                "port": self.port_entry.get().strip(),
                "encoding": self.encoding_var.get(),
                "clipboard_monitor": self.clipboard_monitor_var.get(),
                **self.get_stt_settings(),
                "ai_base_url": self.ai_base_url_entry.get().strip(),
                "ai_api_key": self.ai_api_key_entry.get().strip(),
                "ai_model": self.ai_model_var.get().strip()
                or "doubao-seed-2-0-lite-260215",
                "ai_prompt_scene": self.ai_prompt_scene_var.get().strip(),
                "ai_prompt_templates": prompt_templates,
            }
        try:
            with open(self.config_file, 'w', encoding='utf-8') as f:
                json.dump(config, f, indent=4, ensure_ascii=False)
            self._render_ai_scene_buttons()
            self._render_quick_scene_buttons()
            self.update_status("设置已保存。")
        except Exception as e:
            self.master.after(
                0, lambda: self.update_status(f"保存配置失败: {e}"))

    # ──────────────────────────── Status / UI update ────────────────────────────

    def update_status(self, message):
        timestamp = datetime.datetime.now().strftime("%H:%M:%S")
        self.status_text.config(state=tk.NORMAL)
        self.status_text.insert(tk.END, f"[{timestamp}] {message}\n")
        self.status_text.see(tk.END)
        self.status_text.config(state=tk.DISABLED)

    def update_receive_area(self, message):
        timestamp = datetime.datetime.now().strftime("%H:%M:%S")
        self.receive_text.config(state=tk.NORMAL)
        self.receive_text.insert(tk.END, f"[{timestamp}] 服务器: {message}\n")
        self.receive_text.see(tk.END)
        self.receive_text.config(state=tk.DISABLED)

    def clear_send_area(self):
        self.send_text.delete(1.0, tk.END)

    def clear_receive_area(self):
        self.receive_text.config(state=tk.NORMAL)
        self.receive_text.delete(1.0, tk.END)
        self.receive_text.config(state=tk.DISABLED)

    def clear_status_area(self):
        self.status_text.config(state=tk.NORMAL)
        self.status_text.delete(1.0, tk.END)
        self.status_text.config(state=tk.DISABLED)

    def clear_subtitle_area(self):
        self._subtitle_history = []
        self._subtitle_live_line = ""
        self._subtitle_final_events = []
        self._manual_segment_active = False
        self._manual_segment_start_index = 0
        self._manual_segment_start_time = None
        if hasattr(self, "manual_segment_button"):
            self.manual_segment_button.config(text="片段开始")
        self._sync_quick_panel_buttons()
        self._render_subtitle_area()

    def _render_subtitle_area(self):
        lines = list(self._subtitle_history)
        if self._subtitle_live_line:
            lines.append(self._subtitle_live_line)

        if len(lines) > self._subtitle_max_lines:
            lines = lines[-self._subtitle_max_lines:]
            self._subtitle_history = lines[:-1] if self._subtitle_live_line else lines

        content = ""
        if lines:
            content = "\n".join(lines) + "\n"

        self.subtitle_text.config(state=tk.NORMAL)
        self.subtitle_text.delete(1.0, tk.END)
        if content:
            self.subtitle_text.insert(tk.END, content)
            self.subtitle_text.see(tk.END)
        self.subtitle_text.config(state=tk.DISABLED)

    def _extract_stt_text(self, payload):
        if isinstance(payload, dict):
            return (payload.get("text") or "").strip()
        return str(payload or "").strip()

    def _format_subtitle_line(self, payload, final=False):
        text = self._extract_stt_text(payload)
        if not text:
            return ""

        timestamp = datetime.datetime.now().strftime("%H:%M:%S")
        prefix = "[最终]" if final else "[识别中]"
        language_tag = ""
        if isinstance(payload, dict):
            detected_language = (payload.get("detected_language") or "").strip()
            if detected_language:
                language_tag = f"[{detected_language}]"
        return f"[{timestamp}] {prefix}{language_tag} {text}"

    def update_subtitle_area(self, payload, final=False):
        line = self._format_subtitle_line(payload, final=final)
        if not line:
            return

        if final:
            self._subtitle_live_line = ""
            self._subtitle_history.append(line)
            self._subtitle_final_events.append({
                "text": self._extract_stt_text(payload),
                "timestamp": datetime.datetime.now(),
            })
            if len(self._subtitle_history) > self._subtitle_max_lines:
                self._subtitle_history = self._subtitle_history[-self._subtitle_max_lines:]
        else:
            self._subtitle_live_line = line

        self._render_subtitle_area()

    def toggle_manual_segment(self):
        if not self._manual_segment_active:
            self._manual_segment_active = True
            self._manual_segment_start_index = len(self._subtitle_final_events)
            self._manual_segment_start_time = datetime.datetime.now()
            self.manual_segment_button.config(text="片段结束")
            self._sync_quick_panel_buttons()
            self.update_status("手动片段已开始，等待结束。")
            return

        self._manual_segment_active = False
        self.manual_segment_button.config(text="片段开始")
        self._sync_quick_panel_buttons()
        end_time = datetime.datetime.now()
        segment_events = self._subtitle_final_events[self._manual_segment_start_index:]
        segment_text = " ".join(
            event["text"].strip() for event in segment_events if event["text"].strip()
        ).strip()

        if not segment_text:
            self.update_status("手动片段结束：该时间段内没有最终字幕。")
            return

        start_time = self._manual_segment_start_time or end_time
        self.ai_prompt_text.delete(1.0, tk.END)
        self.ai_prompt_text.insert(
            1.0,
            segment_text,
        )
        self._manual_segment_pending_send = True
        self.update_status(
            "手动片段结束：已拼接 %d 条字幕，已填入 AI 输入框，等待粘贴图片后自动发送。"
            % len(segment_events)
        )
        self._last_manual_segment_meta = {
            "start": start_time.strftime("%H:%M:%S"),
            "end": end_time.strftime("%H:%M:%S"),
        }

    def append_ai_result(self, text):
        self.ai_result_text.config(state=tk.NORMAL)
        self.ai_result_text.insert(tk.END, text.rstrip() + "\n\n")
        self.ai_result_text.see(tk.END)
        self.ai_result_text.config(state=tk.DISABLED)

    def _get_selected_prompt_template(self):
        scene = (self.ai_prompt_scene_var.get() or "").strip()
        if not scene:
            return ""
        templates = self._current_prompt_templates()
        return (templates.get(scene) or "").strip()

    def _combine_prompt_with_template(self, user_prompt):
        template = self._get_selected_prompt_template()
        if not template:
            return user_prompt.strip()
        if not user_prompt.strip():
            return template
        return f"{template}\n\n用户输入：\n{user_prompt.strip()}"

    def _forward_ai_result_to_socket(self, text):
        if not text:
            return False
        if not self.is_connected or not self.client_socket:
            self.update_status("AI 返回未转发：当前未连接 Socket。")
            return False
        sent = self._send_text_payload(
            text,
            source_label="AI",
            clear_input=False,
            show_dialog=False,
        )
        if sent:
            self.update_status("AI 返回已自动转发到 Socket。")
        return sent

    def clear_ai_image(self):
        self._ai_clipboard_image_bytes = None
        self._ai_clipboard_image_mime = ""
        self._ai_preview_photo = None
        self.ai_image_info_var.set("未附加图片")
        self.ai_image_preview.config(text="(可选) 点击“粘贴图片”从剪贴板读取图片", image="")

    def paste_ai_image(self):
        if ImageGrab is None:
            self.update_status("未安装 Pillow，无法读取剪贴板图片。请先安装 pillow。")
            return
        try:
            clip_obj = ImageGrab.grabclipboard()
        except Exception as e:
            self.update_status(f"读取剪贴板图片失败: {e}")
            return

        if clip_obj is None:
            self.update_status("剪贴板中没有图片。")
            return

        image = None
        if Image is not None and isinstance(clip_obj, Image.Image):
            image = clip_obj
        elif isinstance(clip_obj, list) and clip_obj:
            # 某些系统会返回文件路径列表
            try:
                image = Image.open(clip_obj[0]) if Image is not None else None
            except Exception:
                image = None

        if image is None:
            self.update_status("剪贴板内容不是可识别图片。")
            return

        try:
            buf = io.BytesIO()
            image.save(buf, format="PNG")
            self._ai_clipboard_image_bytes = buf.getvalue()
            self._ai_clipboard_image_mime = "image/png"
            self.ai_image_info_var.set(
                f"已附加图片: {image.size[0]}x{image.size[1]} (PNG)"
            )

            if ImageTk is not None:
                preview = image.copy()
                preview.thumbnail((360, 180))
                self._ai_preview_photo = ImageTk.PhotoImage(preview)
                self.ai_image_preview.config(image=self._ai_preview_photo, text="")
            else:
                self.ai_image_preview.config(
                    text=f"图片已附加 ({image.size[0]}x{image.size[1]})", image=""
                )
            self.update_status("已从剪贴板附加图片到 AI 请求。")
            if self._manual_segment_pending_send:
                self._manual_segment_pending_send = False
                meta = self._last_manual_segment_meta
                self.send_ai_request(
                    request_source="手动片段",
                    segment_meta=meta,
                )
            else:
                self.send_ai_request(request_source="粘贴图片")
        except Exception as e:
            self.update_status(f"处理图片失败: {e}")

    def send_ai_request(self, prompt=None, request_source="AI页面", segment_meta=None):
        if self._ai_request_thread and self._ai_request_thread.is_alive():
            self.update_status("AI 请求仍在处理中，请稍后。")
            return

        if prompt is None:
            prompt = self.ai_prompt_text.get(1.0, tk.END).strip()
        prompt = self._combine_prompt_with_template(prompt)
        if not prompt and not self._ai_clipboard_image_bytes:
            self.update_status("AI 输入为空，请输入文本或粘贴图片。")
            return

        api_key = self.ai_api_key_entry.get().strip()
        base_url = self.ai_base_url_entry.get().strip()
        model = self.ai_model_var.get().strip()
        if not api_key or not base_url or not model:
            self.update_status("AI 配置不完整：请在设置页填写 Base URL / API Key / 模型。")
            return

        self.save_config()
        self.ai_send_button.config(state=tk.DISABLED)
        self.update_status(f"{request_source} -> AI: 请求已提交，模型 {model}")

        payload = {
            "model": model,
            "messages": [],
            "temperature": 0.3,
        }
        if self._ai_clipboard_image_bytes:
            content_items = []
            if prompt:
                content_items.append({"type": "text", "text": prompt})
            img_b64 = base64.b64encode(self._ai_clipboard_image_bytes).decode("ascii")
            data_url = f"data:{self._ai_clipboard_image_mime};base64,{img_b64}"
            content_items.append({"type": "image_url", "image_url": {"url": data_url}})
            payload["messages"].append({"role": "user", "content": content_items})
        else:
            payload["messages"].append({"role": "user", "content": prompt})

        def _worker():
            req = urllib.request.Request(
                url=base_url,
                data=json.dumps(payload).encode("utf-8"),
                headers={
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {api_key}",
                },
                method="POST",
            )
            try:
                with urllib.request.urlopen(req, timeout=60) as resp:
                    raw = resp.read().decode("utf-8")
                parsed = json.loads(raw)
                answer = ""
                choices = parsed.get("choices") or []
                if choices:
                    msg = choices[0].get("message") or {}
                    content = msg.get("content", "")
                    if isinstance(content, str):
                        answer = content.strip()
                    elif isinstance(content, list):
                        answer = "\n".join(
                            item.get("text", "").strip()
                            for item in content
                            if isinstance(item, dict) and item.get("text")
                        ).strip()
                if not answer:
                    answer = json.dumps(parsed, ensure_ascii=False, indent=2)

                title = f"[{datetime.datetime.now().strftime('%H:%M:%S')}] {request_source}"
                if segment_meta:
                    title += f" ({segment_meta.get('start')}~{segment_meta.get('end')})"
                final_text = f"{title}\n{answer}"
                self.master.after(0, lambda: self.append_ai_result(final_text))
                self.master.after(0, lambda: self.update_status("AI 返回完成。"))
                self.master.after(0, lambda: self._forward_ai_result_to_socket(answer))
            except urllib.error.HTTPError as e:
                try:
                    err = e.read().decode("utf-8", errors="replace")
                except Exception:
                    err = str(e)
                self.master.after(0, lambda: self.update_status(f"AI 请求失败: HTTP {e.code} {err}"))
            except Exception as e:
                self.master.after(0, lambda: self.update_status(f"AI 请求异常: {e}"))
            finally:
                self.master.after(0, lambda: self.ai_send_button.config(state=tk.NORMAL))

        self._ai_request_thread = threading.Thread(target=_worker, daemon=True)
        self._ai_request_thread.start()

    # ──────────────────────────── Connection ────────────────────────────

    def connect_to_server(self):
        if self.is_connected:
            self.update_status("已连接，请勿重复操作。")
            return

        host = self.host_entry.get().strip()
        port_str = self.port_entry.get().strip()

        if not host or not port_str:
            messagebox.showerror("错误", "服务器地址和端口号不能为空！")
            return

        try:
            port = int(port_str)
            if not (0 < port < 65536):
                raise ValueError
        except ValueError:
            messagebox.showerror("错误", "端口号无效，请输入 1-65535 之间的整数。")
            return

        self.update_status(f"尝试连接到 {host}:{port}...")
        self.connect_button.config(state=tk.DISABLED)

        def _connect():
            try:
                self.client_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                self.client_socket.settimeout(10)
                self.client_socket.connect((host, port))
                self.master.after(0, self._on_connect_success)
            except socket.timeout:
                self.master.after(0, lambda: self._on_connect_error("连接超时"))
            except ConnectionRefusedError:
                self.master.after(0, lambda: self._on_connect_error("服务器拒绝连接"))
            except socket.gaierror:
                self.master.after(0, lambda: self._on_connect_error("无效的服务器地址"))
            except OSError as e:
                self.master.after(0, lambda: self._on_connect_error(f"网络错误：{e}"))
            except Exception as e:
                self.master.after(0, lambda: self._on_connect_error(f"未知错误：{e}"))

        threading.Thread(target=_connect, daemon=True).start()

    def _on_connect_success(self):
        self.is_connected = True
        self.client_socket.settimeout(None)
        if self.stt_thread and self.stt_thread.is_alive():
            self._begin_caption_stream()
        self.send_button.config(state=tk.NORMAL)
        self.connect_button.config(state=tk.DISABLED)
        self.disconnect_button.config(state=tk.NORMAL)
        self._set_connected_ui(True)
        self.update_status("已成功连接到服务器！")
        self.receive_thread = threading.Thread(
            target=self._receive_messages, daemon=True)
        self.receive_thread.start()

    def _on_connect_error(self, msg):
        self.update_status(f"连接失败：{msg}")
        self.is_connected = False
        if self.client_socket:
            try:
                self.client_socket.close()
            except Exception:
                pass
        self.client_socket = None
        self.connect_button.config(state=tk.NORMAL)

    def _receive_messages(self):
        while self.is_connected and self.client_socket:
            try:
                self.client_socket.settimeout(1.0)
                data = self.client_socket.recv(4096)
                if not data:
                    self.master.after(
                        0, lambda: self._on_connection_lost("服务器关闭了连接"))
                    break
                encoding = self.encoding_var.get()
                try:
                    message = data.decode(encoding)
                except UnicodeDecodeError:
                    message = data.decode('utf-8', errors='replace')
                    self.master.after(
                        0, lambda: self.update_status(
                            f"解码失败，已降级为 UTF-8（原编码: {encoding}）"))
                self.master.after(
                    0, lambda msg=message: self.update_receive_area(msg))
            except socket.timeout:
                continue
            except ConnectionResetError:
                self.master.after(
                    0, lambda: self._on_connection_lost("连接被重置"))
                break
            except OSError:
                break
            except Exception as e:
                self.master.after(
                    0, lambda: self._on_connection_lost(f"接收出错：{e}"))
                break

    def _on_connection_lost(self, reason):
        self.update_status(f"连接已断开：{reason}")
        self.disconnect_from_server()

    def disconnect_from_server(self):
        if self.client_socket:
            try:
                self.client_socket.close()
            except Exception:
                pass
            self.client_socket = None
        self.is_connected = False
        self._reset_caption_protocol_state()
        self.send_button.config(state=tk.DISABLED)
        self.connect_button.config(state=tk.NORMAL)
        self.disconnect_button.config(state=tk.DISABLED)
        self._set_connected_ui(False)
        self.update_status("已与服务器断开连接。")

    # ──────────────────────────── Send ────────────────────────────

    def _reset_caption_segment_state(self):
        self._caption_segment_id = ""
        self._caption_segment_seq = 0
        self._caption_last_partial_text = ""
        self._caption_last_partial_lang = ""

    def _reset_caption_protocol_state(self):
        self._caption_stream_id = ""
        self._caption_segment_counter = 0
        self._reset_caption_segment_state()

    def _begin_caption_stream(self):
        timestamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S-%f")
        self._caption_stream_id = f"stt-{timestamp}-{uuid.uuid4().hex[:6]}"
        self._caption_segment_counter = 0
        self._reset_caption_segment_state()

    def _ensure_caption_stream(self):
        if not self._caption_stream_id:
            self._begin_caption_stream()
        return self._caption_stream_id

    def _ensure_caption_segment(self):
        if not self._caption_segment_id:
            self._caption_segment_counter += 1
            self._caption_segment_id = f"seg-{self._caption_segment_counter:04d}"
            self._caption_segment_seq = 0
            self._caption_last_partial_text = ""
            self._caption_last_partial_lang = ""
        return self._caption_segment_id

    def _next_caption_seq(self):
        self._caption_segment_seq += 1
        return self._caption_segment_seq

    def _caption_forward_enabled(self):
        return bool(
            self.stt_send_final_var.get()
            and self.is_connected
            and self.client_socket
        )

    def _normalize_plain_text_message(self, message):
        # Keep real newlines so code blocks remain readable end-to-end.
        return (message or "").replace("\r\n", "\n").replace("\r", "\n")

    def _send_socket_bytes(self, data, show_dialog=True):
        if not self.is_connected or not self.client_socket:
            if show_dialog:
                messagebox.showerror("错误", "请先连接到服务器！")
            self.update_status("尚未连接到服务器。")
            return False

        try:
            self.client_socket.sendall(data)
            return True
        except (BrokenPipeError, ConnectionResetError):
            self.update_status("发送失败：连接已断开。")
            if show_dialog:
                messagebox.showerror("错误", "连接已断开，请重新连接。")
            self.disconnect_from_server()
            return False
        except OSError as e:
            self.update_status(f"发送失败：{e}")
            self.disconnect_from_server()
            return False
        except Exception as e:
            self.update_status(f"发送异常：{e}")
            return False

    def _send_text_payload(self, message, source_label="我",
                           clear_input=False, show_dialog=True):
        wire_message = self._normalize_plain_text_message(message)
        if not wire_message:
            self.update_status("发送内容不能为空。")
            return False
        if wire_message.startswith(CAPTION_PROTOCOL_PREFIX):
            if show_dialog:
                messagebox.showerror(
                    "错误",
                    f"普通消息不能以保留前缀 {CAPTION_PROTOCOL_PREFIX} 开头。",
                )
            self.update_status("普通消息命中了字幕协议保留前缀，已阻止发送。")
            return False

        try:
            encoded = f"{wire_message}\n".encode(TEXT_WIRE_ENCODING)
        except UnicodeEncodeError as e:
            self.update_status(f"编码失败：{TEXT_WIRE_ENCODING}")
            if show_dialog:
                messagebox.showerror(
                    "编码错误",
                    f"无法用 {TEXT_WIRE_ENCODING} 编码。\n{e}",
                )
            return False

        if not self._send_socket_bytes(encoded, show_dialog=show_dialog):
            return False

        timestamp = datetime.datetime.now().strftime("%H:%M:%S")
        self.update_status(
            f"{source_label}消息已发送（行协议: {TEXT_WIRE_ENCODING}）"
        )
        self.receive_text.config(state=tk.NORMAL)
        self.receive_text.insert(
            tk.END, f"[{timestamp}] {source_label}: {wire_message}\n")
        self.receive_text.see(tk.END)
        self.receive_text.config(state=tk.DISABLED)

        if clear_input:
            self.send_text.delete(1.0, tk.END)
        return True

    def _build_caption_message(self, message_type, payload=None):
        message = {
            "type": message_type,
            "stream_id": self._ensure_caption_stream(),
            "timestamp_ms": int(datetime.datetime.now().timestamp() * 1000),
        }
        if payload:
            message.update(payload)
        return message

    def _send_caption_protocol_message(self, message):
        wire_message = (
            f"{CAPTION_PROTOCOL_PREFIX} "
            f"{json.dumps(message, ensure_ascii=False, separators=(',', ':'))}\n"
        )
        encoded = wire_message.encode(TEXT_WIRE_ENCODING)
        return self._send_socket_bytes(encoded, show_dialog=False)

    def _forward_caption_partial(self, payload):
        if not self._caption_forward_enabled():
            return False

        text = self._extract_stt_text(payload)
        if not text:
            return False

        lang = ""
        if isinstance(payload, dict):
            lang = (payload.get("detected_language") or "").strip()

        if (text == self._caption_last_partial_text
                and lang == self._caption_last_partial_lang):
            return False

        message = self._build_caption_message(
            "caption_partial",
            {
                "segment_id": self._ensure_caption_segment(),
                "seq": self._next_caption_seq(),
                "text": text,
            },
        )
        if lang:
            message["lang"] = lang

        if self._send_caption_protocol_message(message):
            self._caption_last_partial_text = text
            self._caption_last_partial_lang = lang
            return True
        return False

    def _forward_caption_final(self, payload):
        text = self._extract_stt_text(payload)
        if not text:
            self._reset_caption_segment_state()
            return False

        sent = False
        if self._caption_forward_enabled():
            lang = ""
            if isinstance(payload, dict):
                lang = (payload.get("detected_language") or "").strip()

            message = self._build_caption_message(
                "caption_final",
                {
                    "segment_id": self._ensure_caption_segment(),
                    "seq": self._next_caption_seq(),
                    "text": text,
                },
            )
            if lang:
                message["lang"] = lang
            sent = self._send_caption_protocol_message(message)

        self._reset_caption_segment_state()
        return sent

    def _forward_caption_clear(self):
        if not self._caption_segment_id:
            return False

        sent = False
        if self._caption_forward_enabled():
            message = self._build_caption_message(
                "caption_clear",
                {
                    "segment_id": self._caption_segment_id,
                    "seq": self._next_caption_seq(),
                },
            )
            sent = self._send_caption_protocol_message(message)

        self._reset_caption_segment_state()
        return sent

    def send_message(self):
        if not self.is_connected or not self.client_socket:
            self.update_status("尚未连接到服务器。")
            messagebox.showerror("错误", "请先连接到服务器！")
            return

        if self.send_mode_var.get() == "file":
            file_path = self.file_path_var.get().strip()
            if not file_path:
                messagebox.showerror("错误", "请选择要发送的文件！")
                return
            if self.send_file(file_path):
                self.file_path_var.set("")
            return

        message = self.send_text.get(1.0, tk.END).strip()
        if not message:
            self.update_status("发送内容不能为空。")
            return

        self._send_text_payload(
            message,
            source_label="我",
            clear_input=True,
            show_dialog=True,
        )

    def on_send_mode_change(self):
        if self.send_mode_var.get() == "file":
            self.file_frame.grid()
            self.send_text.config(state=tk.DISABLED)
            self.send_button.config(text="发送文件")
        else:
            self.file_frame.grid_remove()
            self.send_text.config(state=tk.NORMAL)
            self.send_button.config(text="发送  Ctrl+Enter")

    def browse_file(self):
        path = filedialog.askopenfilename(
            title="选择要发送的文件",
            filetypes=[("所有文件", "*.*"), ("文本文件", "*.txt"),
                       ("图片文件", "*.jpg;*.png;*.gif;*.bmp"),
                       ("文档文件", "*.doc;*.docx;*.pdf")])
        if path:
            self.file_path_var.set(path)

    def send_file(self, file_path):
        if not os.path.exists(file_path):
            messagebox.showerror("错误", "文件不存在！")
            return False

        file_size = os.path.getsize(file_path)
        filename = os.path.basename(file_path)

        if file_size > 10 * 1024 * 1024:
            if not messagebox.askyesno(
                    "警告",
                    f"文件 {file_size/1024/1024:.2f}MB，可能较慢。继续？"):
                return False

        try:
            encoding = self.encoding_var.get()
            fn_enc = filename.encode(encoding)
            header = (b"FILE_TRANSFER:"
                      + struct.pack('I', len(fn_enc))
                      + fn_enc
                      + struct.pack('Q', file_size))
            self.client_socket.sendall(header)

            sent = 0
            with open(file_path, 'rb') as fobj:
                while sent < file_size:
                    chunk = fobj.read(4096)
                    if not chunk:
                        break
                    self.client_socket.sendall(chunk)
                    sent += len(chunk)
                    self.update_status(
                        f"发送进度: {sent/file_size*100:.1f}%"
                        f" ({sent}/{file_size} 字节)")

            timestamp = datetime.datetime.now().strftime("%H:%M:%S")
            self.update_status(f"文件发送完成: {filename} ({file_size} 字节)")
            self.receive_text.config(state=tk.NORMAL)
            self.receive_text.insert(
                tk.END, f"[{timestamp}] 我: [文件] {filename} ({file_size} 字节)\n")
            self.receive_text.see(tk.END)
            self.receive_text.config(state=tk.DISABLED)
            return True

        except Exception as e:
            messagebox.showerror("发送失败", f"{e}")
            return False

    # ──────────────────────────── Clipboard ────────────────────────────

    def _enable_clipboard_monitor(self):
        self.clipboard_monitor_enabled = True
        self.last_clipboard_content = ""
        self.check_clipboard()

    def toggle_clipboard_monitor(self):
        self.clipboard_monitor_enabled = self.clipboard_monitor_var.get()
        if self.clipboard_monitor_enabled:
            self.last_clipboard_content = ""
            self.check_clipboard()
            self.update_status("已启用粘贴板监听")
        else:
            if self.clipboard_check_id:
                self.master.after_cancel(self.clipboard_check_id)
                self.clipboard_check_id = None
            self.update_status("已禁用粘贴板监听")
        self.save_config()

    def check_clipboard(self):
        if not self.clipboard_monitor_enabled:
            return
        try:
            content = self.master.clipboard_get()
            if content and content != self.last_clipboard_content:
                if self.send_mode_var.get() == "text":
                    self.send_text.delete(1.0, tk.END)
                    self.send_text.insert(1.0, content)
                    self.update_status(
                        f"已从粘贴板填入文本（{len(content)} 字符）")
                self.last_clipboard_content = content
        except tk.TclError:
            pass
        except Exception:
            pass

        if self.clipboard_monitor_enabled:
            self.clipboard_check_id = self.master.after(200, self.check_clipboard)

    def on_encoding_change(self):
        self.save_config()

    # ──────────────────────────── STT ────────────────────────────

    def on_stt_config_change(self):
        self.save_config()

    def build_stt_options(self):
        return AzureRealtimeSttConfig.from_mapping(self.get_stt_settings())

    def _emit_stt_event(self, event_type, payload):
        self.stt_queue.put((event_type, payload))

    def create_realtime_subtitle_worker(self, stt_options):
        return create_realtime_stt_worker(
            stt_options,
            self.stt_stop_event,
            self._emit_stt_event,
        )

    def start_stt_thread(self):
        self.on_stt_config_change()
        self._stt_stopping_requested = False
        if self.stt_thread and self.stt_thread.is_alive():
            self.update_status("实时字幕已在运行中。")
            self._sync_quick_panel_buttons()
            return
        if not self.stt_enabled_var.get():
            self.update_status('请先在「设置」中勾选"启用实时字幕"。')
            return
        if not is_azure_speech_sdk_available():
            self.update_status(
                "未安装 azure-cognitiveservices-speech，无法启动实时字幕。")
            return

        try:
            stt_options = self.build_stt_options()
        except ValueError as e:
            self.update_status(f"实时字幕配置错误：{e}")
            return

        self._begin_caption_stream()
        self.stt_stop_event.clear()
        worker = self.create_realtime_subtitle_worker(stt_options)
        self.stt_thread = threading.Thread(target=worker, daemon=True)
        self.stt_thread.start()
        self.stt_start_button.config(state=tk.DISABLED)
        self.stt_stop_button.config(state=tk.NORMAL)
        self._stt_dot.config(foreground="#2E7D32")
        self._sync_quick_panel_buttons()
        self.update_status("正在启动实时字幕线程...")

    def stop_stt_thread(self):
        if self.stt_thread and self.stt_thread.is_alive():
            self.stt_stop_event.set()
            self._stt_stopping_requested = True
            self._sync_quick_panel_buttons()
            self.update_status("正在停止实时字幕线程...")
        else:
            self._stt_stopping_requested = False
            self.stt_start_button.config(state=tk.NORMAL)
            self.stt_stop_button.config(state=tk.DISABLED)
            self._stt_dot.config(foreground="#CCCCCC")
            self._sync_quick_panel_buttons()

    def process_stt_events(self):
        try:
            while True:
                event_type, payload = self.stt_queue.get_nowait()
                if event_type == "partial":
                    self.update_subtitle_area(payload, final=False)
                    self._forward_caption_partial(payload)
                elif event_type == "final":
                    self.update_subtitle_area(payload, final=True)
                    self._forward_caption_final(payload)
                elif event_type == "status":
                    self.update_status(payload)
                elif event_type == "stopped":
                    self._forward_caption_clear()
                    self._stt_stopping_requested = False
                    self.stt_thread = None
                    self.stt_start_button.config(state=tk.NORMAL)
                    self.stt_stop_button.config(state=tk.DISABLED)
                    self._stt_dot.config(foreground="#CCCCCC")
                    self._sync_quick_panel_buttons()
                    self.update_status("实时字幕已停止")
        except queue.Empty:
            pass
        finally:
            if not self._closing:
                self.master.after(100, self.process_stt_events)

    # ──────────────────────────── Close ────────────────────────────

    def on_closing(self):
        self._closing = True
        if self.clipboard_check_id:
            self.master.after_cancel(self.clipboard_check_id)
        self.stop_stt_thread()
        self.save_config()
        if messagebox.askokcancel("退出", "确定要退出吗？"):
            if self._quick_panel:
                try:
                    self._quick_panel.destroy()
                except Exception:
                    pass
            self.disconnect_from_server()
            self.master.destroy()
        else:
            self._closing = False
            self.master.after(100, self.process_stt_events)


if __name__ == "__main__":
    root = tk.Tk()
    app = SocketClientGUI(root)
    root.mainloop()
