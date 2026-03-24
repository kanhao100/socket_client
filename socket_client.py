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

from azure_realtime_stt import (
    AzureRealtimeSttConfig,
    create_realtime_stt_worker,
    is_azure_speech_sdk_available,
)


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
        self._settings_frame = ttk.Frame(self.notebook)
        self.notebook.add(self._main_frame, text="   主界面   ")
        self.notebook.add(self._settings_frame, text="   ⚙ 设置   ")

        self._build_main_tab(config)
        self._build_settings_tab(config)

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
                   command=lambda: self.notebook.select(1)).pack(side=tk.RIGHT)

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
            text="最终识别结果自动发送到 Socket",
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

    # ──────────────────────────── Connection state helpers ────────────────────────────

    def _set_connected_ui(self, connected: bool):
        if connected:
            self._conn_dot.config(foreground="#2E7D32")
            self._conn_label.config(text="已连接", foreground="#2E7D32")
        else:
            self._conn_dot.config(foreground="#C62828")
            self._conn_label.config(text="未连接", foreground="#C62828")

    # ──────────────────────────── Config ────────────────────────────

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
            config = {
                "host": self.host_entry.get().strip(),
                "port": self.port_entry.get().strip(),
                "encoding": self.encoding_var.get(),
                "clipboard_monitor": self.clipboard_monitor_var.get(),
                **self.get_stt_settings(),
            }
        try:
            with open(self.config_file, 'w', encoding='utf-8') as f:
                json.dump(config, f, indent=4, ensure_ascii=False)
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
            if len(self._subtitle_history) > self._subtitle_max_lines:
                self._subtitle_history = self._subtitle_history[-self._subtitle_max_lines:]
        else:
            self._subtitle_live_line = line

        self._render_subtitle_area()

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
        self.send_button.config(state=tk.DISABLED)
        self.connect_button.config(state=tk.NORMAL)
        self.disconnect_button.config(state=tk.DISABLED)
        self._set_connected_ui(False)
        self.update_status("已与服务器断开连接。")

    # ──────────────────────────── Send ────────────────────────────

    def _send_text_payload(self, message, source_label="我",
                           clear_input=False, show_dialog=True):
        try:
            encoding = self.encoding_var.get()
            try:
                encoded = message.encode(encoding)
            except UnicodeEncodeError as e:
                self.update_status(f"编码失败：{encoding}")
                if show_dialog:
                    messagebox.showerror(
                        "编码错误",
                        f"无法用 {encoding} 编码。\n{e}",
                    )
                return False

            self.client_socket.sendall(encoded)
            timestamp = datetime.datetime.now().strftime("%H:%M:%S")
            self.update_status(f"{source_label}消息已发送（编码: {encoding}）")
            self.receive_text.config(state=tk.NORMAL)
            self.receive_text.insert(
                tk.END, f"[{timestamp}] {source_label}: {message}\n")
            self.receive_text.see(tk.END)
            self.receive_text.config(state=tk.DISABLED)

            if clear_input:
                self.send_text.delete(1.0, tk.END)
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
        if self.stt_thread and self.stt_thread.is_alive():
            self.update_status("实时字幕已在运行中。")
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

        self.stt_stop_event.clear()
        worker = self.create_realtime_subtitle_worker(stt_options)
        self.stt_thread = threading.Thread(target=worker, daemon=True)
        self.stt_thread.start()
        self.stt_start_button.config(state=tk.DISABLED)
        self.stt_stop_button.config(state=tk.NORMAL)
        self._stt_dot.config(foreground="#2E7D32")
        self.update_status("正在启动实时字幕线程...")

    def stop_stt_thread(self):
        if self.stt_thread and self.stt_thread.is_alive():
            self.stt_stop_event.set()
            self.update_status("正在停止实时字幕线程...")
        else:
            self.stt_start_button.config(state=tk.NORMAL)
            self.stt_stop_button.config(state=tk.DISABLED)
            self._stt_dot.config(foreground="#CCCCCC")

    def process_stt_events(self):
        try:
            while True:
                event_type, payload = self.stt_queue.get_nowait()
                if event_type == "partial":
                    self.update_subtitle_area(payload, final=False)
                elif event_type == "final":
                    self.update_subtitle_area(payload, final=True)
                    final_text = self._extract_stt_text(payload)
                    if (self.stt_send_final_var.get()
                            and self.is_connected
                            and self.client_socket
                            and final_text):
                        self._send_text_payload(
                            final_text,
                            source_label="字幕",
                            clear_input=False,
                            show_dialog=False,
                        )
                elif event_type == "status":
                    self.update_status(payload)
                elif event_type == "stopped":
                    self.stt_start_button.config(state=tk.NORMAL)
                    self.stt_stop_button.config(state=tk.DISABLED)
                    self._stt_dot.config(foreground="#CCCCCC")
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
            self.disconnect_from_server()
            self.master.destroy()
        else:
            self._closing = False
            self.master.after(100, self.process_stt_events)


if __name__ == "__main__":
    root = tk.Tk()
    app = SocketClientGUI(root)
    root.mainloop()
