import socket
import tkinter as tk
from tkinter import scrolledtext, messagebox, filedialog
import threading
import datetime
import os
import struct
import json
import sys

class SocketClientGUI:
    def __init__(self, master):
        self.master = master
        master.title("Socket 客户端")
        master.geometry("600x800")  # 设置窗口大小
        master.resizable(True, True)  # 允许调整窗口大小

        self.client_socket = None
        self.is_connected = False
        self.receive_thread = None
        
        # 粘贴板监听相关
        self.clipboard_monitor_enabled = False
        self.last_clipboard_content = ""
        self.clipboard_check_id = None

        # 配置文件路径（兼容打包后的情况）
        if getattr(sys, 'frozen', False) or hasattr(sys, '_MEIPASS'):
            # 打包后的情况：使用可执行文件所在目录
            application_path = os.path.dirname(sys.executable)
        else:
            # 开发环境：使用脚本所在目录
            application_path = os.path.dirname(os.path.abspath(__file__))
        self.config_file = os.path.join(application_path, "config.json")
        
        # 加载配置
        config = self.load_config()

        # 连接设置
        self.host_label = tk.Label(master, text="服务器地址:")
        self.host_label.grid(row=0, column=0, padx=5, pady=5, sticky="w")
        self.host_entry = tk.Entry(master, width=30)
        self.host_entry.grid(row=0, column=1, padx=5, pady=5)
        self.host_entry.insert(0, config.get("host", "127.0.0.1"))

        self.port_label = tk.Label(master, text="端口号:")
        self.port_label.grid(row=1, column=0, padx=5, pady=5, sticky="w")
        self.port_entry = tk.Entry(master, width=30)
        self.port_entry.grid(row=1, column=1, padx=5, pady=5)
        self.port_entry.insert(0, config.get("port", "8888"))

        # 编码格式选择
        self.encoding_label = tk.Label(master, text="编码格式:")
        self.encoding_label.grid(row=2, column=0, padx=5, pady=5, sticky="w")
        self.encoding_var = tk.StringVar(value=config.get("encoding", "utf-8"))
        encoding_frame = tk.Frame(master)
        encoding_frame.grid(row=2, column=1, padx=5, pady=5, sticky="w")
        
        encodings = ["utf-8", "gbk", "gb2312", "ascii", "latin-1"]
        for i, encoding in enumerate(encodings):
            rb = tk.Radiobutton(encoding_frame, text=encoding, variable=self.encoding_var, value=encoding,
                              command=self.on_encoding_change)
            rb.pack(side=tk.LEFT, padx=5)

        # 连接和断开按钮框架
        button_frame = tk.Frame(master)
        button_frame.grid(row=3, column=0, columnspan=2, pady=10)
        
        self.connect_button = tk.Button(button_frame, text="连接服务器", command=self.connect_to_server)
        self.connect_button.pack(side=tk.LEFT, padx=5)
        
        self.disconnect_button = tk.Button(button_frame, text="断开连接", command=self.disconnect_from_server, state=tk.DISABLED)
        self.disconnect_button.pack(side=tk.LEFT, padx=5)

        # 粘贴板监听开关（默认开启）
        clipboard_enabled = config.get("clipboard_monitor", True)
        self.clipboard_monitor_var = tk.BooleanVar(value=clipboard_enabled)
        self.clipboard_checkbox = tk.Checkbutton(
            master, 
            text="监听粘贴板", 
            variable=self.clipboard_monitor_var,
            command=self.toggle_clipboard_monitor
        )
        self.clipboard_checkbox.grid(row=4, column=0, padx=5, pady=5, sticky="w")
        
        # 如果默认开启，立即启动监听
        if clipboard_enabled:
            self.master.after(100, self._enable_clipboard_monitor)
        
        # 发送区域
        self.send_label = tk.Label(master, text="要发送的内容:")
        self.send_label.grid(row=4, column=1, padx=5, pady=5, sticky="nw")
        
        # 发送模式选择
        send_mode_frame = tk.Frame(master)
        send_mode_frame.grid(row=5, column=1, padx=5, pady=2, sticky="w")
        
        self.send_mode_var = tk.StringVar(value="text")
        text_rb = tk.Radiobutton(send_mode_frame, text="文本消息", variable=self.send_mode_var, 
                                value="text", command=self.on_send_mode_change)
        text_rb.pack(side=tk.LEFT, padx=5)
        
        file_rb = tk.Radiobutton(send_mode_frame, text="文件传输", variable=self.send_mode_var, 
                                value="file", command=self.on_send_mode_change)
        file_rb.pack(side=tk.LEFT, padx=5)
        
        # 文件选择框架（初始隐藏）
        self.file_frame = tk.Frame(master)
        self.file_frame.grid(row=6, column=1, padx=5, pady=2, sticky="ew")
        self.file_frame.grid_remove()  # 初始隐藏
        
        self.file_path_var = tk.StringVar()
        self.file_entry = tk.Entry(self.file_frame, textvariable=self.file_path_var, width=50)
        self.file_entry.pack(side=tk.LEFT, padx=2)
        
        self.file_browse_button = tk.Button(self.file_frame, text="浏览...", command=self.browse_file)
        self.file_browse_button.pack(side=tk.LEFT, padx=2)
        
        self.send_text = scrolledtext.ScrolledText(master, width=60, height=10, wrap=tk.WORD)
        self.send_text.grid(row=7, column=1, padx=5, pady=5, sticky="nsew")

        # 绑定回车键发送
        self.send_text.bind('<Control-Return>', lambda event: self.send_message())
        
        # 发送按钮框架
        send_button_frame = tk.Frame(master)
        send_button_frame.grid(row=8, column=1, pady=5, sticky="e")
        
        self.send_button = tk.Button(send_button_frame, text="发送 (Ctrl+Enter)", command=self.send_message, state=tk.DISABLED, width=15)
        self.send_button.pack(side=tk.LEFT, padx=5)
        
        self.clear_send_button = tk.Button(send_button_frame, text="清除发送", command=self.clear_send_area, width=10)
        self.clear_send_button.pack(side=tk.LEFT, padx=5)
        
        # 接收消息区域
        self.receive_label = tk.Label(master, text="接收的消息:")
        self.receive_label.grid(row=9, column=0, padx=5, pady=5, sticky="nw")
        self.receive_text = scrolledtext.ScrolledText(master, width=60, height=6, state=tk.DISABLED, wrap=tk.WORD)
        self.receive_text.grid(row=9, column=1, padx=5, pady=5, sticky="nsew")
        
        # 状态区域
        self.status_label = tk.Label(master, text="状态信息:")
        self.status_label.grid(row=10, column=0, padx=5, pady=5, sticky="nw")
        self.status_text = scrolledtext.ScrolledText(master, width=60, height=5, state=tk.DISABLED, wrap=tk.WORD)
        self.status_text.grid(row=10, column=1, padx=5, pady=5, sticky="nsew")

        # 清空按钮框架
        clear_frame = tk.Frame(master)
        clear_frame.grid(row=11, column=1, pady=5, sticky="e")
        
        self.clear_receive_button = tk.Button(clear_frame, text="清空接收", command=self.clear_receive_area)
        self.clear_receive_button.pack(side=tk.LEFT, padx=2)
        
        self.clear_status_button = tk.Button(clear_frame, text="清空状态", command=self.clear_status_area)
        self.clear_status_button.pack(side=tk.LEFT, padx=2)

        # 退出按钮
        self.exit_button = tk.Button(master, text="退出", command=self.on_closing)
        self.exit_button.grid(row=12, column=0, columnspan=2, pady=10)

        # 配置网格权重，使界面可以调整大小
        master.columnconfigure(1, weight=1)
        master.rowconfigure(7, weight=4)  # 发送区域
        master.rowconfigure(9, weight=1)  # 接收区域
        master.rowconfigure(10, weight=1)  # 状态区域

        master.protocol("WM_DELETE_WINDOW", self.on_closing) # 窗口关闭协议

    def load_config(self):
        """加载配置文件"""
        default_config = {
            "host": "127.0.0.1",
            "port": "8888",
            "encoding": "utf-8",
            "clipboard_monitor": True
        }
        
        if os.path.exists(self.config_file):
            try:
                with open(self.config_file, 'r', encoding='utf-8') as f:
                    config = json.load(f)
                    # 合并默认配置，确保所有键都存在
                    default_config.update(config)
                    return default_config
            except Exception as e:
                self.master.after(0, lambda: self.update_status(f"加载配置文件失败: {e}，使用默认配置"))
                return default_config
        else:
            # 如果配置文件不存在，创建默认配置文件
            self.save_config(default_config)
            return default_config
    
    def save_config(self, config=None):
        """保存配置文件"""
        if config is None:
            config = {
                "host": self.host_entry.get().strip(),
                "port": self.port_entry.get().strip(),
                "encoding": self.encoding_var.get(),
                "clipboard_monitor": self.clipboard_monitor_var.get()
            }
        
        try:
            with open(self.config_file, 'w', encoding='utf-8') as f:
                json.dump(config, f, indent=4, ensure_ascii=False)
        except Exception as e:
            self.master.after(0, lambda: self.update_status(f"保存配置文件失败: {e}"))

    def _enable_clipboard_monitor(self):
        """初始化时启用粘贴板监听"""
        self.clipboard_monitor_enabled = True
        self.last_clipboard_content = ""
        self.check_clipboard()

    def update_status(self, message):
        """更新状态提示区域的内容"""
        timestamp = datetime.datetime.now().strftime("%H:%M:%S")
        formatted_message = f"[{timestamp}] {message}"
        
        self.status_text.config(state=tk.NORMAL)
        self.status_text.insert(tk.END, formatted_message + "\n")
        self.status_text.see(tk.END) # 滚动到最新消息
        self.status_text.config(state=tk.DISABLED)

    def update_receive_area(self, message):
        """更新接收消息区域的内容"""
        timestamp = datetime.datetime.now().strftime("%H:%M:%S")
        formatted_message = f"[{timestamp}] 服务器: {message}"
        
        self.receive_text.config(state=tk.NORMAL)
        self.receive_text.insert(tk.END, formatted_message + "\n")
        self.receive_text.see(tk.END)
        self.receive_text.config(state=tk.DISABLED)

    def clear_send_area(self):
        """清空发送消息区域"""
        self.send_text.config(state=tk.NORMAL)
        self.send_text.delete(1.0, tk.END)
        self.send_text.config(state=tk.NORMAL)  # 发送文本框应该保持可编辑状态
        self.update_status("已清空发送区域")

    def clear_receive_area(self):
        """清空接收消息区域"""
        self.receive_text.config(state=tk.NORMAL)
        self.receive_text.delete(1.0, tk.END)
        self.receive_text.config(state=tk.DISABLED)

    def clear_status_area(self):
        """清空状态信息区域"""
        self.status_text.config(state=tk.NORMAL)
        self.status_text.delete(1.0, tk.END)
        self.status_text.config(state=tk.DISABLED)

    def connect_to_server(self):
        """连接到服务器"""
        if self.is_connected:
            self.update_status("已连接到服务器，请勿重复连接。")
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
            messagebox.showerror("错误", "端口号无效，请输入一个介于1到65535之间的整数。")
            return

        self.update_status(f"尝试连接到 {host}:{port}...")
        
        # 禁用连接按钮防止重复点击
        self.connect_button.config(state=tk.DISABLED)
        
        def connect_thread():
            try:
                self.client_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                self.client_socket.settimeout(10)  # 设置10秒连接超时
                self.client_socket.connect((host, port))
                
                # 连接成功后的界面更新必须在主线程中执行
                self.master.after(0, self._on_connect_success)
                
            except socket.timeout:
                self.master.after(0, lambda: self._on_connect_error("连接超时，请检查网络连接或服务器状态。"))
            except ConnectionRefusedError:
                self.master.after(0, lambda: self._on_connect_error("服务器拒绝连接。请检查服务器是否正在运行。"))
            except socket.gaierror:
                self.master.after(0, lambda: self._on_connect_error("无效的服务器地址。"))
            except OSError as e:
                self.master.after(0, lambda: self._on_connect_error(f"网络错误：{e}"))
            except Exception as e:
                self.master.after(0, lambda: self._on_connect_error(f"连接时发生未知错误：{e}"))

        # 在子线程中执行连接操作
        connect_thread = threading.Thread(target=connect_thread, daemon=True)
        connect_thread.start()

    def _on_connect_success(self):
        """连接成功的回调"""
        self.is_connected = True
        self.client_socket.settimeout(None)  # 移除超时限制
        self.send_button.config(state=tk.NORMAL)
        self.connect_button.config(state=tk.DISABLED)
        self.disconnect_button.config(state=tk.NORMAL)
        self.update_status("成功连接到服务器！")
        
        # 启动接收消息的线程
        self.receive_thread = threading.Thread(target=self._receive_messages, daemon=True)
        self.receive_thread.start()

    def _on_connect_error(self, error_message):
        """连接失败的回调"""
        self.update_status(f"连接失败：{error_message}")
        self.is_connected = False
        if self.client_socket:
            try:
                self.client_socket.close()
            except:
                pass
        self.client_socket = None
        self.connect_button.config(state=tk.NORMAL)

    def _receive_messages(self):
        """在后台线程中接收服务器消息"""
        while self.is_connected and self.client_socket:
            try:
                # 设置接收超时，避免线程阻塞
                self.client_socket.settimeout(1.0)
                data = self.client_socket.recv(4096)
                
                if not data:
                    # 服务器关闭了连接
                    self.master.after(0, lambda: self._on_connection_lost("服务器关闭了连接"))
                    break
                    
                encoding = self.encoding_var.get()
                try:
                    message = data.decode(encoding)
                except UnicodeDecodeError:
                    # 如果解码失败，尝试用utf-8，并显示错误信息
                    message = data.decode('utf-8', errors='replace')
                    self.master.after(0, lambda: self.update_status(f"解码失败，使用UTF-8解码 (原编码: {encoding})"))
                # 在主线程中更新UI
                self.master.after(0, lambda msg=message: self.update_receive_area(msg))
                
            except socket.timeout:
                # 超时是正常的，继续循环
                continue
            except ConnectionResetError:
                self.master.after(0, lambda: self._on_connection_lost("连接被重置"))
                break
            except OSError:
                # 连接已关闭
                break
            except Exception as e:
                self.master.after(0, lambda: self._on_connection_lost(f"接收消息时出错：{e}"))
                break

    def _on_connection_lost(self, reason):
        """连接丢失的处理"""
        self.update_status(f"连接已断开：{reason}")
        self.disconnect_from_server()

    def send_message(self):
        """发送消息到服务器"""
        if not self.is_connected or not self.client_socket:
            self.update_status("尚未连接到服务器，无法发送消息。")
            messagebox.showerror("错误", "请先连接到服务器！")
            return

        # 检查发送模式
        if self.send_mode_var.get() == "file":
            # 文件发送模式
            file_path = self.file_path_var.get().strip()
            if not file_path:
                self.update_status("请选择要发送的文件。")
                messagebox.showerror("错误", "请选择要发送的文件！")
                return
            
            # 发送文件
            if self.send_file(file_path):
                self.file_path_var.set("")  # 清空文件路径
            return
        
        # 文本发送模式
        message = self.send_text.get(1.0, tk.END).strip() # 获取所有内容并去除首尾空白

        if not message:
            self.update_status("发送内容不能为空。")
            return

        # # 检查消息长度
        # if len(message.encode('utf-8')) > 4096:
        #     if not messagebox.askyesno("警告", "消息较长，可能发送失败。是否继续发送？"):
        #         return

        try:
            encoding = self.encoding_var.get()
            try:
                encoded_message = message.encode(encoding)
            except UnicodeEncodeError as e:
                self.update_status(f"编码失败：无法使用 {encoding} 编码此消息")
                messagebox.showerror("编码错误", f"无法使用 {encoding} 编码此消息。\n错误：{e}")
                return
                
            self.client_socket.sendall(encoded_message)
            timestamp = datetime.datetime.now().strftime("%H:%M:%S")
            self.update_status(f"消息已发送 (编码: {encoding})")
            
            # 在接收区域显示发送的消息
            self.receive_text.config(state=tk.NORMAL)
            self.receive_text.insert(tk.END, f"[{timestamp}] 我: {message}\n")
            self.receive_text.see(tk.END)
            self.receive_text.config(state=tk.DISABLED)
            
            self.send_text.delete(1.0, tk.END) # 清空发送栏
            
        except BrokenPipeError:
            self.update_status("发送失败：连接已断开。")
            messagebox.showerror("错误", "连接已断开，请重新连接。")
            self.disconnect_from_server()
        except ConnectionResetError:
            self.update_status("发送失败：连接被重置。")
            messagebox.showerror("错误", "连接被重置，请重新连接。")
            self.disconnect_from_server()
        except OSError as e:
            self.update_status(f"发送失败：网络错误 {e}")
            self.disconnect_from_server()
        except Exception as e:
            self.update_status(f"发送时发生错误：{e}")

    def on_send_mode_change(self):
        """处理发送模式改变"""
        if self.send_mode_var.get() == "file":
            self.file_frame.grid()  # 显示文件选择框架
            self.send_text.config(state=tk.DISABLED)  # 禁用文本输入框
            self.send_button.config(text="发送文件")
        else:
            self.file_frame.grid_remove()  # 隐藏文件选择框架
            self.send_text.config(state=tk.NORMAL)  # 启用文本输入框
            self.send_button.config(text="发送 (Ctrl+Enter)")
    
    def browse_file(self):
        """浏览选择文件"""
        file_path = filedialog.askopenfilename(
            title="选择要发送的文件",
            filetypes=[
                ("所有文件", "*.*"),
                ("文本文件", "*.txt"),
                ("图片文件", "*.jpg;*.png;*.gif;*.bmp"),
                ("文档文件", "*.doc;*.docx;*.pdf")
            ]
        )
        if file_path:
            self.file_path_var.set(file_path)

    def send_file(self, file_path):
        """发送文件到服务器"""
        if not os.path.exists(file_path):
            self.update_status("文件不存在！")
            messagebox.showerror("错误", "选择的文件不存在！")
            return False
            
        file_size = os.path.getsize(file_path)
        filename = os.path.basename(file_path)
        
        # 检查文件大小（限制为10MB）
        if file_size > 10 * 1024 * 1024:
            if not messagebox.askyesno("警告", f"文件大小为 {file_size/1024/1024:.2f}MB，可能发送失败。是否继续？"):
                return False
        
        try:
            encoding = self.encoding_var.get()
            
            # 发送文件头信息：标识符 + 文件名长度 + 文件名 + 文件大小
            header = "FILE_TRANSFER:"
            filename_encoded = filename.encode(encoding)
            header_data = header.encode('ascii') + struct.pack('I', len(filename_encoded)) + filename_encoded + struct.pack('Q', file_size)
            self.client_socket.sendall(header_data)
            
            # 发送文件内容
            sent_bytes = 0
            with open(file_path, 'rb') as f:
                while sent_bytes < file_size:
                    chunk = f.read(4096)  # 每次读取4KB
                    if not chunk:
                        break
                    self.client_socket.sendall(chunk)
                    sent_bytes += len(chunk)
                    
                    # 更新进度
                    progress = (sent_bytes / file_size) * 100
                    self.update_status(f"发送进度: {progress:.1f}% ({sent_bytes}/{file_size} 字节)")
            
            timestamp = datetime.datetime.now().strftime("%H:%M:%S")
            self.update_status(f"文件发送完成: {filename} ({file_size} 字节)")
            
            # 在接收区域显示发送的文件信息
            self.receive_text.config(state=tk.NORMAL)
            self.receive_text.insert(tk.END, f"[{timestamp}] 我: [文件] {filename} ({file_size} 字节)\n")
            self.receive_text.see(tk.END)
            self.receive_text.config(state=tk.DISABLED)
            
            return True
            
        except Exception as e:
            self.update_status(f"文件发送失败: {e}")
            messagebox.showerror("发送失败", f"文件发送失败：{e}")
            return False

    def disconnect_from_server(self):
        """断开与服务器的连接"""
        if self.client_socket:
            try:
                self.client_socket.close()
            except:
                pass
            self.client_socket = None
            
        self.is_connected = False
        self.send_button.config(state=tk.DISABLED)
        self.connect_button.config(state=tk.NORMAL)
        self.disconnect_button.config(state=tk.DISABLED)
        self.update_status("已与服务器断开连接。")

    def toggle_clipboard_monitor(self):
        """切换粘贴板监听状态"""
        self.clipboard_monitor_enabled = self.clipboard_monitor_var.get()
        
        if self.clipboard_monitor_enabled:
            # 启动监听
            self.last_clipboard_content = ""
            self.check_clipboard()
            self.update_status("已启用粘贴板监听")
        else:
            # 停止监听
            if self.clipboard_check_id:
                self.master.after_cancel(self.clipboard_check_id)
                self.clipboard_check_id = None
            self.update_status("已禁用粘贴板监听")
        
        # 保存配置
        self.save_config()
    
    def on_encoding_change(self):
        """编码格式改变时的回调"""
        self.save_config()
    
    def check_clipboard(self):
        """检查粘贴板内容"""
        if not self.clipboard_monitor_enabled:
            return
        
        try:
            # 尝试获取粘贴板内容（只获取文本）
            clipboard_content = self.master.clipboard_get()
            
            # 检查内容是否变化且不为空
            if clipboard_content and clipboard_content != self.last_clipboard_content:
                # 只处理文本模式
                if self.send_mode_var.get() == "text":
                    # 检查文本框是否为空，如果为空则填入，否则追加
                    current_text = self.send_text.get(1.0, tk.END).strip()
                    if not current_text:
                        # 文本框为空，直接填入
                        self.send_text.delete(1.0, tk.END)
                        self.send_text.insert(1.0, clipboard_content)
                        self.update_status(f"已从粘贴板自动填入文本 ({len(clipboard_content)} 字符)")
                    else:
                        # 文本框有内容，追加（可选：也可以选择替换）
                        # 这里选择追加，如果需要替换可以改为：
                        # 替换
                        self.send_text.delete(1.0, tk.END)
                        self.send_text.insert(1.0, clipboard_content)
                        # 追加
                        # self.send_text.insert(tk.END, "\n" + clipboard_content)
                        # self.update_status(f"已从粘贴板追加文本 ({len(clipboard_content)} 字符)")
                
                self.last_clipboard_content = clipboard_content
                
        except tk.TclError:
            # 粘贴板中没有文本内容（可能是图片、文件等），忽略
            pass
        except Exception as e:
            # 其他错误，记录但不中断监听
            pass
        
        # 继续监听（每500毫秒检查一次）
        if self.clipboard_monitor_enabled:
            self.clipboard_check_id = self.master.after(200, self.check_clipboard)
    
    def on_closing(self):
        """处理窗口关闭事件"""
        # 停止粘贴板监听
        if self.clipboard_check_id:
            self.master.after_cancel(self.clipboard_check_id)
        
        # 保存配置
        self.save_config()
        
        if messagebox.askokcancel("退出", "确定要退出吗？"):
            self.disconnect_from_server()
            self.master.destroy()

if __name__ == "__main__":
    root = tk.Tk()
    app = SocketClientGUI(root)
    root.mainloop()