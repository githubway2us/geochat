import tkinter as tk
from tkinter import messagebox, scrolledtext, filedialog, ttk
import paramiko
import threading
import os
import stat

class TermiusMiniApp:
    def __init__(self, root):
        self.root = root
        self.root.title("Termius Mini - Server & Docker Commander Pro")
        self.root.geometry("1250x750")
        
        # ตั้งค่าธีมสีเข้มสไตล์ Modern IDE / Termius
        self.bg_dark = "#1e1e1e"
        self.fg_light = "#d4d4d4"
        self.accent_color = "#0e639c"
        self.root.configure(bg=self.bg_dark)

        # --- ตัวแปรค่าคอนฟิกโปรเจกต์ ---
        self.project_name = tk.StringVar(value="chatroom")
        self.project_port = tk.StringVar(value="7890")
        self.current_remote_path = tk.StringVar(value="/home/geomanss")

        # --- Menu Bar ด้านบน ---
        menubar = tk.Menu(root)
        root.config(menu=menubar)

        deploy_menu = tk.Menu(menubar, tearoff=0)
        menubar.add_cascade(label="⚙️ Deploy Settings", menu=deploy_menu)
        deploy_menu.add_command(label="เปลี่ยนชื่อ Service / Project...", command=self.change_project_name)

        docker_menu = tk.Menu(menubar, tearoff=0)
        menubar.add_cascade(label="🐳 Docker Systems", menu=docker_menu)
        docker_menu.add_command(label="Docker PS (Active Containers)", command=lambda: self.run_quick_cmd("docker ps --format 'table {{.Names}}\\t{{.Image}}\\t{{.Ports}}'"))
        docker_menu.add_command(label="Docker PS -a (All Containers)", command=lambda: self.run_quick_cmd("docker ps -a --format 'table {{.Names}}\\t{{.Status}}\\t{{.Image}}'"))
        docker_menu.add_command(label="Docker Images", command=lambda: self.run_quick_cmd("docker images"))
        docker_menu.add_command(label="Docker Stats (CPU/RAM)", command=lambda: self.run_quick_cmd("docker stats --no-stream"))
        docker_menu.add_command(label="Docker Disk Usage (df)", command=lambda: self.run_quick_cmd("docker system df"))

        # --- Layout หลักแบ่งเป็น 2 ฝั่ง (Sidebar และ Main Content) ---
        self.paned = ttk.PanedWindow(root, orient=tk.HORIZONTAL)
        self.paned.pack(fill=tk.BOTH, expand=True)

        # --- Sidebar: จัดการเซิร์ฟเวอร์ & เมนูด่วน ---
        self.sidebar = tk.Frame(self.paned, bg="#252526", width=280, padx=10, pady=10)
        self.paned.add(self.sidebar, weight=1)

        tk.Label(self.sidebar, text="Servers (Hosts)", fg="white", bg="#252526", font=("Arial", 11, "bold")).pack(anchor="w", pady=5)
        
        tk.Label(self.sidebar, text="Host IP:", fg=self.fg_light, bg="#252526").pack(anchor="w")
        self.entry_host = tk.Entry(self.sidebar, width=28, bg="#333333", fg="white", insertbackground="white")
        self.entry_host.insert(0, "192.168.1.x")
        self.entry_host.pack(anchor="w", pady=2)

        tk.Label(self.sidebar, text="Username:", fg=self.fg_light, bg="#252526").pack(anchor="w")
        self.entry_user = tk.Entry(self.sidebar, width=28, bg="#333333", fg="white", insertbackground="white")
        self.entry_user.insert(0, "geomanss")
        self.entry_user.pack(anchor="w", pady=2)

        tk.Label(self.sidebar, text="Password:", fg=self.fg_light, bg="#252526").pack(anchor="w")
        self.entry_pass = tk.Entry(self.sidebar, width=28, bg="#333333", fg="white", show="*", insertbackground="white")
        self.entry_pass.pack(anchor="w", pady=2)

        self.btn_connect = tk.Button(self.sidebar, text="Connect SSH", command=self.test_connection, bg=self.accent_color, fg="white", width=24)
        self.btn_connect.pack(anchor="w", pady=10)

        # เมนู Bitcoin Node
        tk.Label(self.sidebar, text="Bitcoin Node:", fg="white", bg="#252526", font=("Arial", 10, "bold")).pack(anchor="w", pady=5)
        self.btn_btc_info = tk.Button(self.sidebar, text="🪙 Check Blockchain Info", command=lambda: self.run_quick_cmd("bitcoin-cli getblockchaininfo"), bg="#3c3c3c", fg="#CE9178", anchor="w", width=26)
        self.btn_btc_info.pack(anchor="w", pady=2)
        self.btn_btc_peers = tk.Button(self.sidebar, text="🌐 Check Bitcoin Peers", command=lambda: self.run_quick_cmd("bitcoin-cli getpeerinfo"), bg="#3c3c3c", fg="#CE9178", anchor="w", width=26)
        self.btn_btc_peers.pack(anchor="w", pady=2)

        tk.Label(self.sidebar, text="Quick Actions:", fg="white", bg="#252526", font=("Arial", 10, "bold")).pack(anchor="w", pady=5)
        self.lbl_current_deploy = tk.Label(self.sidebar, text=f"Target: chatroom (:7890)", fg="#4EC9B0", bg="#252526", font=("Arial", 9, "italic"))
        self.lbl_current_deploy.pack(anchor="w")

        self.btn_deploy = tk.Button(self.sidebar, text="🚀 Deploy Custom App", command=self.deploy_custom_app, bg="#2ea043", fg="white", width=24)
        self.btn_deploy.pack(anchor="w", pady=3)

        self.btn_upload = tk.Button(self.sidebar, text="📁 Upload (File / Folder)", command=self.upload_menu_dialog, bg="#d18616", fg="white", width=24)
        self.btn_upload.pack(anchor="w", pady=3)

        # --- Main Content: Notebook Tabs ---
        self.main_area = ttk.Notebook(self.paned)
        self.paned.add(self.main_area, weight=4)

        # Tab 1: Terminal Command Shell
        self.tab_terminal = tk.Frame(self.main_area, bg=self.bg_dark)
        self.main_area.add(self.tab_terminal, text="💻 Terminal Shell")
        
        self.term_output = scrolledtext.ScrolledText(self.tab_terminal, wrap=tk.WORD, bg="#111111", fg="#00ff00", insertbackground="white", font=("Consolas", 10))
        self.term_output.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)
        
        term_input_frame = tk.Frame(self.tab_terminal, bg=self.bg_dark)
        term_input_frame.pack(fill=tk.X, padx=5, pady=5)
        
        tk.Label(term_input_frame, text="Command:", fg="white", bg=self.bg_dark).pack(side=tk.LEFT)
        self.entry_cmd = tk.Entry(term_input_frame, bg="#333333", fg="white", insertbackground="white")
        self.entry_cmd.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=5)
        self.entry_cmd.bind("<Return>", lambda event: self.run_custom_command())
        
        self.btn_send_cmd = tk.Button(term_input_frame, text="Run", command=self.run_custom_command, bg=self.accent_color, fg="white")
        self.btn_send_cmd.pack(side=tk.RIGHT)

        # Tab 2: Remote File Explorer
        self.tab_explorer = tk.Frame(self.main_area, bg=self.bg_dark)
        self.main_area.add(self.tab_explorer, text="📂 Remote File Explorer")

        exp_top_frame = tk.Frame(self.tab_explorer, bg=self.bg_dark)
        exp_top_frame.pack(fill=tk.X, padx=5, pady=5)

        tk.Button(exp_top_frame, text="⬅️ Up", command=self.explorer_go_up, bg="#333333", fg="white").pack(side=tk.LEFT, padx=2)
        tk.Button(exp_top_frame, text="🔄 Refresh", command=self.load_remote_files, bg="#333333", fg="white").pack(side=tk.LEFT, padx=2)
        
        tk.Label(exp_top_frame, text="Path:", fg="white", bg=self.bg_dark).pack(side=tk.LEFT, padx=5)
        self.entry_path = tk.Entry(exp_top_frame, textvariable=self.current_remote_path, bg="#333333", fg="white", insertbackground="white")
        self.entry_path.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=5)
        self.entry_path.bind("<Return>", lambda e: self.load_remote_files())

        tk.Button(exp_top_frame, text="Go", command=self.load_remote_files, bg=self.accent_color, fg="white").pack(side=tk.LEFT, padx=2)

        # Treeview สำหรับแสดงไฟล์และโฟเดอร์
        self.tree_frame = tk.Frame(self.tab_explorer, bg=self.bg_dark)
        self.tree_frame.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)

        self.tree_files = ttk.Treeview(self.tree_frame, columns=("size", "type", "permissions"), selectmode="browse")
        self.tree_files.heading("#0", text="Name", anchor="w")
        self.tree_files.heading("size", text="Size (Bytes)", anchor="w")
        self.tree_files.heading("type", text="Type", anchor="w")
        self.tree_files.heading("permissions", text="Permissions", anchor="w")
        
        self.tree_files.column("#0", width=300, stretch=True)
        self.tree_files.column("size", width=100, stretch=False)
        self.tree_files.column("type", width=100, stretch=False)
        self.tree_files.column("permissions", width=100, stretch=False)

        tree_scroll = ttk.Scrollbar(self.tree_frame, orient=tk.VERTICAL, command=self.tree_files.yview)
        self.tree_files.configure(yscrollcommand=tree_scroll.set)

        self.tree_files.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        tree_scroll.pack(side=tk.RIGHT, fill=tk.Y)

        self.tree_files.bind("<Double-1>", self.explorer_on_double_click)
        
        # คลิกขวา (Context Menu) สำหรับดาวน์โหลดไฟล์หรือโฟเดอร์
        self.context_menu = tk.Menu(root, tearoff=0)
        self.context_menu.add_command(label="📥 Download (File / Folder)", command=self.download_selected_item)
        self.tree_files.bind("<Button-3>", self.show_context_menu)

        # Tab 3: Docker Live Logs
        self.tab_logs = tk.Frame(self.main_area, bg=self.bg_dark)
        self.main_area.add(self.tab_logs, text="📊 Docker Live Logs")

        log_top_frame = tk.Frame(self.tab_logs, bg=self.bg_dark)
        log_top_frame.pack(fill=tk.X, padx=5, pady=5)
        tk.Label(log_top_frame, text="Container Name:", fg="white", bg=self.bg_dark).pack(side=tk.LEFT)
        self.entry_container = tk.Entry(log_top_frame, width=20, bg="#333333", fg="white")
        self.entry_container.insert(0, "chatroom-app")
        self.entry_container.pack(side=tk.LEFT, padx=5)
        
        self.btn_stream = tk.Button(log_top_frame, text="Stream Logs", command=self.start_log_stream, bg="#1f6feb", fg="white")
        self.btn_stream.pack(side=tk.LEFT, padx=5)

        self.log_output = scrolledtext.ScrolledText(self.tab_logs, wrap=tk.WORD, bg="#111111", fg="#569CD6", insertbackground="white", font=("Consolas", 10))
        self.log_output.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)

        self.is_streaming = False

    def change_project_name(self):
        top = tk.Toplevel(self.root)
        top.title("Configure Deploy Target")
        top.geometry("320x180")
        top.configure(bg=self.bg_dark)

        tk.Label(top, text="Project / Service Name:", fg="white", bg=self.bg_dark).pack(anchor="w", padx=15, pady=5)
        e_name = tk.Entry(top, bg="#333333", fg="white", insertbackground="white", width=30)
        e_name.insert(0, self.project_name.get())
        e_name.pack(padx=15)

        tk.Label(top, text="Host Port:", fg="white", bg=self.bg_dark).pack(anchor="w", padx=15, pady=5)
        e_port = tk.Entry(top, bg="#333333", fg="white", insertbackground="white", width=30)
        e_port.insert(0, self.project_port.get())
        e_port.pack(padx=15)

        def save_config():
            self.project_name.set(e_name.get().strip())
            self.project_port.set(e_port.get().strip())
            self.lbl_current_deploy.config(text=f"Target: {self.project_name.get()} (:{self.project_port.get()})")
            self.entry_container.delete(0, tk.END)
            self.entry_container.insert(0, f"{self.project_name.get()}-app")
            top.destroy()

        tk.Button(top, text="Save", command=save_config, bg="#2ea043", fg="white", width=15).pack(pady=15)

    def connect_ssh(self):
        host = self.entry_host.get()
        user = self.entry_user.get()
        password = self.entry_pass.get()
        ssh = paramiko.SSHClient()
        ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        ssh.connect(host, username=user, password=password, timeout=5)
        return ssh

    def test_connection(self):
        try:
            ssh = self.connect_ssh()
            ssh.close()
            messagebox.showinfo("Connected", "เชื่อมต่อ Server สำเร็จแล้ว!")
            self.load_remote_files()
        except Exception as e:
            messagebox.showerror("Connection Failed", str(e))

    def run_quick_cmd(self, cmd):
        self.main_area.select(self.tab_terminal)
        self.term_output.insert(tk.END, f"\n$ {cmd}\n")
        threading.Thread(target=self._execute_remote_cmd, args=(cmd,), daemon=True).start()

    def run_custom_command(self):
        cmd = self.entry_cmd.get()
        if not cmd:
            return
        self.term_output.insert(tk.END, f"\n$ {cmd}\n")
        self.entry_cmd.delete(0, tk.END)
        threading.Thread(target=self._execute_remote_cmd, args=(cmd,), daemon=True).start()

    def _execute_remote_cmd(self, cmd):
        try:
            ssh = self.connect_ssh()
            password = self.entry_pass.get()
            
            if cmd.strip().startswith("sudo"):
                actual_cmd = cmd.strip()[4:].strip()
                stdin, stdout, stderr = ssh.exec_command(f"sudo -S {actual_cmd}")
                stdin.write(password + '\n')
                stdin.flush()
            else:
                stdin, stdout, stderr = ssh.exec_command(cmd)

            out = stdout.read().decode('utf-8', errors='ignore')
            err = stderr.read().decode('utf-8', errors='ignore')
            ssh.close()
            
            if out:
                self.root.after(0, lambda: self.term_output.insert(tk.END, out))
            if err:
                filtered_err = "\n".join([line for line in err.splitlines() if "password for" not in line])
                if filtered_err.strip():
                    self.root.after(0, lambda: self.term_output.insert(tk.END, f"[Error] {filtered_err}\n"))
        except Exception as e:
            self.root.after(0, lambda: self.term_output.insert(tk.END, f"[SSH Error] {str(e)}\n"))
        self.term_output.see(tk.END)

    # --- ฟังก์ชันจัดการ File Explorer และดาวน์โหลด (รองรับทั้งไฟล์และโฟเดอร์) ---
    def load_remote_files(self):
        target_path = self.current_remote_path.get().strip()
        threading.Thread(target=self._fetch_sftp_dir, args=(target_path,), daemon=True).start()

    def _fetch_sftp_dir(self, path):
        try:
            ssh = self.connect_ssh()
            sftp = ssh.open_sftp()
            sftp.chdir(path)
            actual_path = sftp.getcwd()
            self.current_remote_path.set(actual_path)
            
            files_attr = sftp.listdir_attr()
            sftp.close()
            ssh.close()

            self.root.after(0, lambda: self._update_treeview(files_attr))
        except Exception as e:
            self.root.after(0, lambda: messagebox.showerror("Explorer Error", str(e)))

    def _update_treeview(self, files_attr):
        for item in self.tree_files.get_children():
            self.tree_files.delete(item)

        for attr in files_attr:
            filename = attr.filename
            if filename in ['.', '..']:
                continue
            
            is_dir = stat.S_ISDIR(attr.st_mode) if hasattr(attr, 'st_mode') else False
            file_type = "Folder" if is_dir else "File"
            size = str(attr.st_size) if not is_dir else "-"
            perms = oct(attr.st_mode)[-3:] if hasattr(attr, 'st_mode') else "---"
            
            icon = "📁" if is_dir else "📄"
            self.tree_files.insert("", tk.END, text=f"{icon} {filename}", values=(size, file_type, perms))

    def explorer_on_double_click(self, event):
        selected_item = self.tree_files.selection()
        if not selected_item:
            return
        item = self.tree_files.item(selected_item)
        text = item["text"]
        values = item["values"]
        
        if values[1] == "Folder":
            folder_name = text.replace("📁 ", "").strip()
            current = self.current_remote_path.get().rstrip('/')
            new_path = f"{current}/{folder_name}"
            self.current_remote_path.set(new_path)
            self.load_remote_files()

    def explorer_go_up(self):
        current = self.current_remote_path.get().rstrip('/')
        parent = os.path.dirname(current)
        if parent:
            self.current_remote_path.set(parent)
            self.load_remote_files()

    def show_context_menu(self, event):
        item = self.tree_files.identify_row(event.y)
        if item:
            self.tree_files.selection_set(item)
            self.context_menu.tk_popup(event.x_root, event.y_root)

    def download_selected_item(self):
        selected_item = self.tree_files.selection()
        if not selected_item:
            return
        item = self.tree_files.item(selected_item)
        text = item["text"]
        values = item["values"]
        
        is_folder = (values[1] == "Folder")
        item_name = text.replace("📁 ", "").replace("📄 ", "").strip()
        current_dir = self.current_remote_path.get().rstrip('/')
        remote_item_path = f"{current_dir}/{item_name}"
        
        if is_folder:
            local_save_dir = filedialog.askdirectory(title=f"เลือกโฟลเดอร์ปลายทางเพื่อบันทึก '{item_name}'")
            if not local_save_dir:
                return
            threading.Thread(target=self._sftp_get_dir, args=(remote_item_path, local_save_dir), daemon=True).start()
        else:
            local_save_path = filedialog.asksaveasfilename(initialfile=item_name, title="บันทึกไฟล์ลงเครื่องของคุณ")
            if not local_save_path:
                return
            threading.Thread(target=self._sftp_get_file, args=(remote_item_path, local_save_path), daemon=True).start()

    def _sftp_get_file(self, remote_path, local_path):
        try:
            self.root.after(0, lambda: self.term_output.insert(tk.END, f"\n[SFTP] กำลังดาวน์โหลดไฟล์ {remote_path}...\n"))
            ssh = self.connect_ssh()
            sftp = ssh.open_sftp()
            sftp.get(remote_path, local_path)
            sftp.close()
            ssh.close()
            self.root.after(0, lambda: messagebox.showinfo("Success", f"ดาวน์โหลดไฟล์สำเร็จ:\n{local_path}"))
        except Exception as e:
            self.root.after(0, lambda: messagebox.showerror("Download Error", str(e)))

    def _sftp_get_dir(self, remote_dir, local_parent_dir):
        try:
            folder_name = os.path.basename(remote_dir)
            local_dest_dir = os.path.join(local_parent_dir, folder_name)
            os.makedirs(local_dest_dir, exist_ok=True)
            
            self.root.after(0, lambda: self.term_output.insert(tk.END, f"\n[SFTP] เริ่มดาวน์โหลดโฟลเดอร์ {remote_dir}...\n"))
            ssh = self.connect_ssh()
            sftp = ssh.open_sftp()
            
            def download_recursive(r_path, l_path):
                for entry in sftp.listdir_attr(r_path):
                    r_item = f"{r_path}/{entry.filename}"
                    l_item = os.path.join(l_path, entry.filename)
                    if stat.S_ISDIR(entry.st_mode):
                        os.makedirs(l_item, exist_ok=True)
                        download_recursive(r_item, l_item)
                    else:
                        sftp.get(r_item, l_item)
            
            download_recursive(remote_dir, local_dest_dir)
            sftp.close()
            ssh.close()
            self.root.after(0, lambda: messagebox.showinfo("Success", f"ดาวน์โหลดโฟลเดอร์สำเร็จ:\n{local_dest_dir}"))
        except Exception as e:
            self.root.after(0, lambda: messagebox.showerror("Download Folder Error", str(e)))

    # --- ฟังก์ชันอัปโหลด (รองรับทั้งไฟล์เดี่ยวและอัปโหลดทั้งโฟลเดอร์) ---
    def upload_menu_dialog(self):
        choice_win = tk.Toplevel(self.root)
        choice_win.title("เลือกรูปแบบการอัปโหลด")
        choice_win.geometry("300x140")
        choice_win.configure(bg=self.bg_dark)
        
        tk.Label(choice_win, text="คุณต้องการอัปโหลดอะไร?", fg="white", bg=self.bg_dark, font=("Arial", 10, "bold")).pack(pady=15)
        
        btn_f = tk.Button(choice_win, text="📄 อัปโหลดไฟล์เดี่ยว", width=22, bg=self.accent_color, fg="white", 
                          command=lambda: [choice_win.destroy(), self.upload_file_dialog()])
        btn_f.pack(pady=2)
        
        btn_d = tk.Button(choice_win, text="📁 อัปโหลดทั้งโฟลเดอร์", width=22, bg="#d18616", fg="white", 
                          command=lambda: [choice_win.destroy(), self.upload_folder_dialog()])
        btn_d.pack(pady=2)

    def upload_file_dialog(self):
        file_path = filedialog.askopenfilename(title="เลือกไฟล์ที่ต้องการอัปโหลด")
        if not file_path:
            return
        file_name = os.path.basename(file_path)
        target_dir = self.current_remote_path.get()
        remote_path = f"{target_dir}/{file_name}"
        threading.Thread(target=self._sftp_put_file, args=(file_path, remote_path), daemon=True).start()

    def _sftp_put_file(self, local_path, remote_path):
        try:
            self.root.after(0, lambda: self.term_output.insert(tk.END, f"\n[SFTP] กำลังอัปโหลดไฟล์ไปยัง {remote_path}...\n"))
            ssh = self.connect_ssh()
            sftp = ssh.open_sftp()
            sftp.put(local_path, remote_path)
            sftp.close()
            ssh.close()
            self.root.after(0, lambda: messagebox.showinfo("Success", f"อัปโหลดสำเร็จ: {remote_path}"))
            self.load_remote_files()
        except Exception as e:
            self.root.after(0, lambda: messagebox.showerror("SFTP Error", str(e)))

    def upload_folder_dialog(self):
        folder_path = filedialog.askdirectory(title="เลือกโฟลเดอร์ที่ต้องการอัปโหลด")
        if not folder_path:
            return
        target_dir = self.current_remote_path.get()
        threading.Thread(target=self._sftp_put_dir, args=(folder_path, target_dir), daemon=True).start()

    def _sftp_put_dir(self, local_dir, remote_parent_dir):
        try:
            folder_name = os.path.basename(folder_path := local_dir)
            remote_target_dir = f"{remote_parent_dir.rstrip('/')}/{folder_name}"
            
            self.root.after(0, lambda: self.term_output.insert(tk.END, f"\n[SFTP] เริ่มอัปโหลดโฟลเดอร์ไปยัง {remote_target_dir}...\n"))
            ssh = self.connect_ssh()
            sftp = ssh.open_sftp()
            
            try:
                sftp.stat(remote_target_dir)
            except IOError:
                sftp.mkdir(remote_target_dir)
                
            def upload_recursive(l_path, r_path):
                for item in os.listdir(l_path):
                    l_item = os.path.join(l_path, item)
                    r_item = f"{r_path}/{item}"
                    if os.path.isdir(l_item):
                        try:
                            sftp.stat(r_item)
                        except IOError:
                            sftp.mkdir(r_item)
                        upload_recursive(l_item, r_item)
                    else:
                        sftp.put(l_item, r_item)
                        
            upload_recursive(folder_path, remote_target_dir)
            sftp.close()
            ssh.close()
            self.root.after(0, lambda: messagebox.showinfo("Success", f"อัปโหลดโฟลเดอร์สำเร็จ:\n{remote_target_dir}"))
            self.load_remote_files()
        except Exception as e:
            self.root.after(0, lambda: messagebox.showerror("Upload Folder Error", str(e)))

    def deploy_custom_app(self):
        p_name = self.project_name.get()
        p_port = self.project_port.get()
        self.main_area.select(self.tab_terminal)
        self.term_output.insert(tk.END, f"\n--- กำลัง Deploy [{p_name}] บนพอร์ต {p_port} ---\n")
        
        commands = [
            f"mkdir -p ~/{p_name}",
            f"echo -e 'version: \"3.8\"\nservices:\n  {p_name}-app:\n    image: python:3.10-slim\n    container_name: {p_name}-app\n    restart: unless-stopped\n    ports:\n      - \"{p_port}:5000\"\n  {p_name}-tunnel:\n    image: cloudflare/cloudflared:latest\n    container_name: {p_name}-tunnel\n    restart: unless-stopped' > ~/{p_name}/docker-compose.yml",
            f"cd ~/{p_name} && docker compose up -d"
        ]
        threading.Thread(target=self._run_script_sequence, args=(commands, p_name), daemon=True).start()

    def _run_script_sequence(self, commands, p_name):
        try:
            ssh = self.connect_ssh()
            for cmd in commands:
                stdin, stdout, stderr = ssh.exec_command(cmd)
                stdout.channel.recv_exit_status()
            ssh.close()
            self.root.after(0, lambda: messagebox.showinfo("Success", f"Deploy [{p_name}] สำเร็จเรียบร้อย!"))
        except Exception as e:
            self.root.after(0, lambda: messagebox.showerror("Error", str(e)))

    def start_log_stream(self):
        if self.is_streaming:
            return
        self.is_streaming = True
        self.log_output.delete(1.0, tk.END)
        threading.Thread(target=self._fetch_docker_logs, daemon=True).start()

    def _fetch_docker_logs(self):
        try:
            container = self.entry_container.get()
            ssh = self.connect_ssh()
            stdin, stdout, stderr = ssh.exec_command(f"docker logs -f --tail 50 {container}")
            while self.is_streaming:
                line = stdout.readline()
                if not line:
                    break
                decoded = line.decode('utf-8', errors='ignore').strip()
                self.root.after(0, lambda d=decoded: self.log_output.insert(tk.END, d + "\n"))
                self.root.after(0, lambda: self.log_output.see(tk.END))
            ssh.close()
        except Exception as e:
            self.root.after(0, lambda: self.log_output.insert(tk.END, f"\n[Log Error]: {str(e)}\n"))
        self.is_streaming = False

if __name__ == "__main__":
    root = tk.Tk()
    app = TermiusMiniApp(root)
    root.mainloop()