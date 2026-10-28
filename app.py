#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Termius Mini Pro v3.0 — Modern SSH · SFTP · Docker · Deploy Commander
ติดตั้ง dependencies: pip install paramiko

ใหม่ใน v3.0
  • ทุกแท็บแยกเป็นหน้าต่างอิสระได้ (คลิกขวาที่แท็บ / ปุ่ม ⧉ / เมนู View) และ Dock กลับได้
  • เปิด Terminal เพิ่มได้หลายหน้าต่าง, Logs/Exec/Inspect ของ Docker เปิดเป็นหน้าต่างแยกได้
  • Docker ครบชุด: Containers · Images · Volumes · Networks · Compose · Stats · System
  • SSH Engine ใหม่: 1 connection + multiplex channel, Thread-pool, SFTP session cache,
    Auto-reconnect, Heartbeat/Latency, Streaming แบบ real-time, ปุ่ม Stop ฆ่าคำสั่งจริง,
    Terminal จำ working directory (cd ต่อเนื่อง)
  • Monitor: CPU/RAM/Disk/Network แบบ Live (1 round-trip), Processes, Services (systemctl), System Logs

⚠ หมายเหตุ: Profile/รหัสผ่านบันทึกแบบ plaintext ที่ ~/.termius_mini_pro.json
   แนะนำใช้ Private Key และไม่ Save รหัสผ่าน หากต้องการความปลอดภัยสูง
"""

import tkinter as tk
from tkinter import messagebox, filedialog, ttk, simpledialog
import paramiko
import threading
import os
import stat
import json
import time
import datetime
import posixpath
import io
import queue
import re
import codecs
from concurrent.futures import ThreadPoolExecutor

APP_VER = "3.0"
PROFILES_FILE = os.path.join(os.path.expanduser("~"), ".termius_mini_pro.json")
SUDO_PROMPT = "@@SUDOPW@@"
CWD_MARK = "@@CWD@@"
ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]|\x1b\][^\x07]*\x07")


# ══════════════════════════ THEME & HELPERS ══════════════════════════
class Theme:
    BG       = "#1a1b26"
    BG_DARK  = "#13131a"
    PANEL    = "#1f2335"
    PANEL_HI = "#2a2f47"
    BORDER   = "#2f344d"
    FG       = "#c0caf5"
    FG_DIM   = "#737aa2"
    BLUE     = "#7aa2f7"
    GREEN    = "#9ece6a"
    RED      = "#f7768e"
    ORANGE   = "#e0af68"
    CYAN     = "#7dcfff"
    INPUT    = "#101017"
    FONT      = ("Segoe UI", 10)
    FONT_SM   = ("Segoe UI", 9)
    FONT_BOLD = ("Segoe UI", 10, "bold")
    FONT_TITLE= ("Segoe UI", 14, "bold")
    MONO      = ("Consolas", 10)


def q(s):
    """Quote สำหรับ shell"""
    return "'" + str(s).replace("'", "'\\''") + "'"


def human_size(n):
    n = float(n or 0)
    for u in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or u == "TB":
            return f"{int(n)} {u}" if u == "B" else f"{n:.1f} {u}"
        n /= 1024


def strip_ansi(s):
    return ANSI_RE.sub("", s)


def jlines(text):
    """แปลงผลลัพธ์ `--format '{{json .}}'` (หนึ่ง JSON ต่อบรรทัด) เป็น list[dict]"""
    out = []
    for l in text.splitlines():
        l = l.strip()
        if l.startswith("{"):
            try:
                out.append(json.loads(l))
            except ValueError:
                pass
    return out


class LineBuf:
    """รวม chunk เป็นบรรทัดสมบูรณ์ (ไม่ตัดบรรทัดกลางคัน)"""
    def __init__(self):
        self.b = ""

    def feed(self, s):
        self.b += s.replace("\r\n", "\n").replace("\r", "\n")
        if "\n" not in self.b:
            return []
        head, _, self.b = self.b.rpartition("\n")
        return head.split("\n")

    def rest(self):
        r, self.b = self.b, ""
        return r


def field(parent, label, var, width=None, show=None):
    tk.Label(parent, text=label, fg=Theme.FG_DIM, bg=parent["bg"],
             font=Theme.FONT_SM).pack(anchor="w", pady=(8, 1))
    e = tk.Entry(parent, textvariable=var, bg=Theme.INPUT, fg=Theme.FG, show=show or "",
                 insertbackground=Theme.FG, relief=tk.FLAT, width=width or 34)
    e.pack(fill=tk.X, ipady=5)
    return e


# ══════════════════════════ WIDGETS ══════════════════════════
class ModernButton(tk.Button):
    """ปุ่ม Flat พร้อม Hover Effect"""
    def __init__(self, master, text="", command=None, bg=Theme.PANEL,
                 fg=Theme.FG, hover=None, font=Theme.FONT, padx=12, pady=6, **kw):
        hover = hover or Theme.PANEL_HI
        super().__init__(master, text=text, command=command, bg=bg, fg=fg,
                         activebackground=hover, activeforeground=fg,
                         relief=tk.FLAT, bd=0, cursor="hand2",
                         font=font, padx=padx, pady=pady, **kw)
        self._bg, self._hv = bg, hover
        self.bind("<Enter>", lambda e: self.config(bg=self._hv))
        self.bind("<Leave>", lambda e: self.config(bg=self._bg))


class LogText(tk.Text):
    """Text สำหรับแสดง log: อ่านอย่างเดียว, auto-scroll เมื่ออยู่ล่างสุด, จำกัดจำนวนบรรทัด"""
    MAX_LINES = 8000

    def __init__(self, master, **kw):
        opts = dict(bg=Theme.INPUT, fg="#a9b1d6", insertbackground=Theme.FG, font=Theme.MONO,
                    state=tk.DISABLED, relief=tk.FLAT, bd=0, padx=12, pady=8, wrap=tk.WORD,
                    highlightthickness=0)
        opts.update(kw)
        super().__init__(master, **opts)
        tags = {
            "prompt": dict(foreground=Theme.BLUE, font=("Consolas", 10, "bold")),
            "cmd":    dict(foreground="#e0e4f5", font=("Consolas", 10, "bold")),
            "out":    dict(foreground="#a9b1d6"),
            "err":    dict(foreground=Theme.RED),
            "ok":     dict(foreground=Theme.GREEN),
            "info":   dict(foreground=Theme.CYAN),
            "dim":    dict(foreground=Theme.FG_DIM),
            "head":   dict(foreground=Theme.BLUE, font=("Consolas", 11, "bold")),
        }
        for t, c in tags.items():
            self.tag_config(t, **c)
        self.bind("<Button-1>", lambda e: self.focus_set())

    def write(self, text, tag="out"):
        at_bottom = self.yview()[1] >= 0.999
        self.config(state=tk.NORMAL)
        self.insert(tk.END, text, tag)
        n = int(self.index("end-1c").split(".")[0])
        if n > self.MAX_LINES:
            self.delete("1.0", f"{n - self.MAX_LINES}.0")
        self.config(state=tk.DISABLED)
        if at_bottom:
            self.see(tk.END)

    def clear(self):
        self.config(state=tk.NORMAL)
        self.delete("1.0", tk.END)
        self.config(state=tk.DISABLED)

    def snapshot(self):
        res, cur = [], None
        for key, val, _ in self.dump("1.0", "end-1c", text=True, tag=True):
            if key == "tagon" and val != "sel":
                cur = val
            elif key == "tagoff" and val != "sel":
                cur = None
            elif key == "text":
                res.append((val, cur or "out"))
        return res

    def restore(self, snap):
        for text, tag in snap or []:
            self.write(text, tag)

    def text(self):
        return self.get("1.0", "end-1c")


class Gauge(tk.Frame):
    """มาตรวัด % แบบแถบสี"""
    def __init__(self, master, title):
        super().__init__(master, bg=Theme.PANEL, padx=14, pady=10)
        tk.Label(self, text=title, fg=Theme.FG_DIM, bg=Theme.PANEL, font=Theme.FONT_SM).pack(anchor="w")
        self.val = tk.Label(self, text="—", fg=Theme.FG, bg=Theme.PANEL, font=("Segoe UI", 18, "bold"))
        self.val.pack(anchor="w")
        self.cv = tk.Canvas(self, height=8, bg=Theme.BORDER, highlightthickness=0, width=120)
        self.cv.pack(fill=tk.X, pady=5)
        self.bar = self.cv.create_rectangle(0, 0, 0, 8, width=0, fill=Theme.GREEN)
        self.sub = tk.Label(self, text="", fg=Theme.FG_DIM, bg=Theme.PANEL, font=Theme.FONT_SM)
        self.sub.pack(anchor="w")

    def set(self, pct, text, sub=""):
        pct = max(0.0, min(100.0, pct))
        w = self.cv.winfo_width() or 120
        self.cv.coords(self.bar, 0, 0, w * pct / 100.0, 8)
        self.cv.itemconfig(self.bar, fill=Theme.GREEN if pct < 60 else Theme.ORANGE if pct < 85 else Theme.RED)
        self.val.config(text=text)
        self.sub.config(text=sub)


# ── Treeview helpers ──
def _sort_tree(tree, col):
    rev = tree._sort_rev.get(col, False)

    def key(r):
        v = r[0].replace("%", "").replace(",", "").strip()
        try:
            return (0, float(v), "")
        except ValueError:
            return (1, 0.0, r[0].lower())
    rows = sorted(((tree.set(i, col), i) for i in tree.get_children("")), key=key, reverse=rev)
    for idx, (_, i) in enumerate(rows):
        tree.move(i, "", idx)
    tree._sort_rev[col] = not rev


def make_table(parent, cols, tags=None, selectmode="extended", height=None):
    """cols: [(id, label, width, anchor, stretch)] → (frame, tree)"""
    f = tk.Frame(parent, bg=Theme.BG)
    kw = {"height": height} if height else {}
    tree = ttk.Treeview(f, columns=[c[0] for c in cols], show="headings", selectmode=selectmode, **kw)
    tree._data, tree._sort_rev = {}, {}
    for cid, label, w, anc, st in cols:
        tree.heading(cid, text=label, anchor="w", command=lambda c=cid: _sort_tree(tree, c))
        tree.column(cid, width=w, anchor=anc, stretch=st)
    vs = ttk.Scrollbar(f, orient=tk.VERTICAL, command=tree.yview)
    tree.configure(yscrollcommand=vs.set)
    tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
    vs.pack(side=tk.RIGHT, fill=tk.Y)
    for t, c in (tags or {}).items():
        tree.tag_configure(t, foreground=c)
    return f, tree


def fill_tree(tree, rows, keyfn, tagfn=None):
    """เติมตารางโดยรักษา selection + scroll เดิม"""
    sel = {keyfn(tree._data[i]) for i in tree.selection() if i in tree._data}
    y = tree.yview()[0]
    tree.delete(*tree.get_children())
    tree._data = {}
    new = []
    for r in rows:
        iid = tree.insert("", tk.END, values=r, tags=(tagfn(r),) if tagfn else ())
        tree._data[iid] = r
        if keyfn(r) in sel:
            new.append(iid)
    if new:
        tree.selection_set(new)
    tree.yview_moveto(y)


def sel_rows(tree):
    return [tree._data[i] for i in tree.selection() if i in tree._data]


def toolbar(parent, items):
    """items: [(text, cmd)] หรือ None = ตัวคั่น"""
    bar = tk.Frame(parent, bg=Theme.BG)
    bar.pack(fill=tk.X, padx=4, pady=(6, 2))
    for it in items:
        if it is None:
            tk.Frame(bar, bg=Theme.BORDER, width=1, height=22).pack(side=tk.LEFT, padx=5)
        else:
            ModernButton(bar, text=it[0], command=it[1], pady=4, font=Theme.FONT_SM).pack(side=tk.LEFT, padx=2)
    return bar


def popup_menu(parent, items):
    m = tk.Menu(parent, tearoff=0, bg=Theme.PANEL, fg=Theme.FG, bd=0,
                activebackground=Theme.PANEL_HI, activeforeground=Theme.BLUE)
    for it in items:
        if it is None:
            m.add_separator()
        else:
            m.add_command(label=it[0], command=it[1])
    return m


# ══════════════════════════ SSH ENGINE ══════════════════════════
class SSHManager:
    """
    จัดการ SSH ทั้งหมดในจุดเดียว
      • 1 connection / หลาย channel พร้อมกัน (ไม่ต้อง login ซ้ำ)
      • Auto-reconnect, keepalive, compression, window ใหญ่ขึ้นเพื่อความเร็ว
      • SFTP session cache (listing เร็ว) + new_sftp() สำหรับงานโอนไฟล์
      • run() = คำสั่งสั้น ๆ, stream() = real-time + Stop ได้
    """
    def __init__(self):
        self.client = None
        self.params = None
        self.lock = threading.RLock()
        self._sftp = None
        self._sftp_lock = threading.RLock()
        self.pool = ThreadPoolExecutor(max_workers=10, thread_name_prefix="ssh")
        self.sudo_pw = ""
        self.docker_sudo = False
        self.home = None

    # ---------- connection ----------
    def connected(self):
        c = self.client
        t = c.get_transport() if c else None
        return bool(t and t.is_active())

    def connect(self):
        if not self.params:
            raise RuntimeError("ยังไม่ได้เชื่อมต่อ — กรอกข้อมูลแล้วกด Connect ก่อน")
        with self.lock:
            self.close(keep_params=True)
            ssh = paramiko.SSHClient()
            ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            try:
                ssh.connect(compress=True, **self.params)
            except Exception:
                ssh.close()
                raise
            t = ssh.get_transport()
            if t:
                t.set_keepalive(15)
                try:
                    t.default_window_size = 8 * 1024 * 1024
                except Exception:
                    pass
            self.client = ssh
            self.home = None

    def ensure(self):
        """คืน client ที่พร้อมใช้ — ต่อใหม่อัตโนมัติถ้าหลุด"""
        with self.lock:
            if self.connected():
                return self.client
            if not self.params:
                raise RuntimeError("ยังไม่ได้เชื่อมต่อ — กรอกข้อมูลแล้วกด Connect ก่อน")
            last = None
            for _ in range(2):
                try:
                    self.connect()
                    return self.client
                except paramiko.AuthenticationException:
                    raise
                except Exception as e:
                    last = e
                    time.sleep(0.6)
            raise last

    def close(self, keep_params=False):
        with self._sftp_lock:
            try:
                if self._sftp:
                    self._sftp.close()
            except Exception:
                pass
            self._sftp = None
        c, self.client = self.client, None
        try:
            if c:
                c.close()
        except Exception:
            pass
        if not keep_params:
            self.params = None

    # ---------- command execution ----------
    def _prep(self, cmd, sudo, pty):
        """แปลงคำสั่งให้รองรับ sudo — คืน (คำสั่งจริง, รหัสผ่านที่ต้องส่ง)"""
        c = cmd.strip()
        if not sudo and not c.startswith("sudo "):
            return cmd, None
        inner = f"sh -c {q(c)}" if sudo else c[5:].lstrip()
        pw = self.sudo_pw
        if pw:
            flag = f"-p {q(SUDO_PROMPT)}" if pty else "-S -p ''"
        else:
            flag = "-n"
        return f"sudo {flag} {inner}", (pw or None)

    def _pump(self, chan, on_out, on_err, stop, timeout):
        dec_o = codecs.getincrementaldecoder("utf-8")("replace")
        dec_e = codecs.getincrementaldecoder("utf-8")("replace")
        t0, idle = time.time(), 0
        while True:
            got = False
            while chan.recv_ready():
                on_out(dec_o.decode(chan.recv(65536)))
                got = True
            while chan.recv_stderr_ready():
                (on_err or on_out)(dec_e.decode(chan.recv_stderr(65536)))
                got = True
            if (chan.exit_status_ready() or chan.closed) and not chan.recv_ready() and not chan.recv_stderr_ready():
                break
            if stop is not None and stop.is_set():
                try:
                    chan.close()
                except Exception:
                    pass
                return -2
            if timeout and time.time() - t0 > timeout:
                try:
                    chan.close()
                except Exception:
                    pass
                raise TimeoutError(f"คำสั่งใช้เวลาเกิน {timeout} วินาที")
            if got:
                idle = 0
            else:
                time.sleep(0.004 if idle < 25 else 0.03)
                idle += 1
        return chan.recv_exit_status()

    def run(self, cmd, timeout=60, sudo=False):
        """รันแล้วรอผล → (exit_code, stdout, stderr)"""
        ssh = self.ensure()
        chan = ssh.get_transport().open_session(timeout=15)
        full, pw = self._prep(cmd, sudo, False)
        chan.exec_command(full)
        try:
            if pw:
                chan.send(pw + "\n")
            chan.shutdown_write()
        except Exception:
            pass
        out, err = [], []
        code = self._pump(chan, out.append, err.append, None, timeout)
        return code, "".join(out), "".join(err)

    def stream(self, cmd, on_out, on_err=None, stop=None, sudo=False, pty=True,
               prefix="", suffix="", timeout=None):
        """รันแบบ real-time. pty=True → ปิด channel (Stop) แล้วโปรเซสฝั่งเซิร์ฟเวอร์ถูกฆ่าจริง"""
        ssh = self.ensure()
        chan = ssh.get_transport().open_session(timeout=15)
        if pty:
            chan.get_pty(term="dumb", width=220, height=50)
        full, pw = self._prep(cmd, sudo, pty)
        chan.exec_command(prefix + full + suffix)
        try:
            if pw and not pty:
                chan.send(pw + "\n")
            if not pty:
                chan.shutdown_write()
        except Exception:
            pass

        def out(s):
            if pty and pw and SUDO_PROMPT in s:
                try:
                    chan.send(pw + "\n")
                except Exception:
                    pass
                s = s.replace(SUDO_PROMPT, "")
            on_out(s)
        return self._pump(chan, out, None if pty else on_err, stop, timeout)

    # ---------- SFTP ----------
    def sftp(self):
        """SFTP session ที่ cache ไว้ (สำหรับ list/stat/อ่านไฟล์เล็ก)"""
        with self._sftp_lock:
            s = self._sftp
            if s is not None:
                try:
                    if not s.sock.closed and self.connected():
                        return s
                except Exception:
                    pass
            self._sftp = self.ensure().open_sftp()
            return self._sftp

    def new_sftp(self):
        """SFTP session ใหม่ (สำหรับโอนไฟล์ใหญ่ — ไม่บล็อกการ list)"""
        return self.ensure().open_sftp()


# ══════════════════════════ STREAM VIEW / WINDOWS ══════════════════════════
class StreamView(tk.Frame):
    """กล่อง log แบบ real-time (ใช้ทั้งในแท็บและหน้าต่างแยก) — รองรับ Filter + Stop"""
    def __init__(self, master, app, on_state=None):
        super().__init__(master, bg=Theme.BG)
        self.app, self.on_state = app, on_state
        self.stop_ev = None
        self._flt = ""
        self.out = LogText(self)
        self.out.pack(fill=tk.BOTH, expand=True)

    @property
    def running(self):
        return self.stop_ev is not None

    def set_filter(self, s):
        self._flt = (s or "").lower()

    def start(self, cmd, sudo=False, pty=True, clear=True, show_cmd=True):
        self.stop()
        if clear:
            self.out.clear()
        if show_cmd:
            self.out.write(f"$ {cmd}\n", "prompt")
        ev = threading.Event()
        self.stop_ev = ev
        if self.on_state:
            self.on_state(True)
        ui, ssh = self.app.ui, self.app.ssh

        def work():
            lb, lb2 = LineBuf(), LineBuf()

            def emit(lines, tag):
                f = self._flt
                if f:
                    lines = [l for l in lines if f in l.lower()]
                if lines:
                    ui(self.out.write, "\n".join(lines) + "\n", tag)
            code = None
            try:
                code = ssh.stream(cmd, lambda s: emit(lb.feed(strip_ansi(s)), "out"),
                                  lambda s: emit(lb2.feed(strip_ansi(s)), "err"),
                                  stop=ev, sudo=sudo, pty=pty)
            except Exception as e:
                ui(self.out.write, f"[Error] {e}\n", "err")
            for b, tag in ((lb, "out"), (lb2, "err")):
                r = b.rest()
                if r:
                    emit([r], tag)
            ui(self._finished, ev, code)
        threading.Thread(target=work, daemon=True).start()

    def _finished(self, ev, code):
        if self.stop_ev is ev:
            self.stop_ev = None
            if self.on_state:
                self.on_state(False)
            if code not in (None, 0, -2):
                self.out.write(f"[exit {code}]\n", "dim")

    def stop(self):
        if self.stop_ev:
            self.stop_ev.set()

    def destroy(self):
        self.stop()
        super().destroy()


class ConsoleWindow(tk.Toplevel):
    """หน้าต่างแยก: แสดงผลคำสั่งแบบ real-time (logs / pull / inspect / events …)
       ถ้ามี wrap → มีช่องพิมพ์คำสั่งต่อเนื่อง (เช่น docker exec)"""
    def __init__(self, app, title, cmd=None, wrap=None, sudo=False, pty=True, size="940x600"):
        super().__init__(app.root)
        self.app, self.cmd, self.wrap, self.sudo, self.pty = app, cmd, wrap, sudo, pty
        self.title(f"{title} — Termius Mini Pro")
        self.geometry(size)
        self.configure(bg=Theme.BG)
        bar = tk.Frame(self, bg=Theme.BG)
        bar.pack(fill=tk.X, padx=8, pady=(8, 4))
        self.btn_stop = ModernButton(bar, text="⏹ Stop", command=lambda: self.view.stop(),
                                     pady=4, font=Theme.FONT_SM, hover=Theme.RED)
        self.btn_stop.pack(side=tk.LEFT, padx=2)
        if cmd:
            ModernButton(bar, text="⟳ Run ใหม่", command=self.rerun, pady=4,
                         font=Theme.FONT_SM).pack(side=tk.LEFT, padx=2)
        ModernButton(bar, text="🧹 Clear", command=lambda: self.view.out.clear(), pady=4,
                     font=Theme.FONT_SM).pack(side=tk.LEFT, padx=2)
        ModernButton(bar, text="💾 Save", command=self.save, pady=4,
                     font=Theme.FONT_SM).pack(side=tk.LEFT, padx=2)
        tk.Label(bar, text="Filter:", fg=Theme.FG_DIM, bg=Theme.BG, font=Theme.FONT_SM).pack(side=tk.LEFT, padx=(14, 4))
        self.v_f = tk.StringVar()
        tk.Entry(bar, textvariable=self.v_f, bg=Theme.INPUT, fg=Theme.FG, insertbackground=Theme.FG,
                 relief=tk.FLAT, width=28).pack(side=tk.LEFT, ipady=3)
        self.v_f.trace_add("write", lambda *_: self.view.set_filter(self.v_f.get()))
        self.view = StreamView(self, app, on_state=self._state)
        self.view.pack(fill=tk.BOTH, expand=True, padx=8, pady=4)
        if wrap:
            row = tk.Frame(self, bg=Theme.BG)
            row.pack(fill=tk.X, padx=8, pady=(0, 8))
            tk.Label(row, text="❯", fg=Theme.BLUE, bg=Theme.BG, font=("Consolas", 12, "bold")).pack(side=tk.LEFT)
            self.e = tk.Entry(row, bg=Theme.INPUT, fg="#e0e4f5", insertbackground=Theme.FG,
                              relief=tk.FLAT, font=Theme.MONO)
            self.e.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=8, ipady=7)
            self.e.bind("<Return>", self.send)
            self.e.focus_set()
        if cmd:
            self.view.start(cmd, sudo=sudo, pty=pty)

    def _state(self, running):
        self.btn_stop.config(state=tk.NORMAL if running else tk.DISABLED)

    def rerun(self):
        self.view.start(self.cmd, sudo=self.sudo, pty=self.pty)

    def send(self, _=None):
        c = self.e.get().strip()
        if not c:
            return
        self.e.delete(0, tk.END)
        self.view.out.write(f"❯ {c}\n", "prompt")
        self.view.start(self.wrap(c), sudo=self.sudo, pty=False, clear=False, show_cmd=False)

    def save(self):
        p = filedialog.asksaveasfilename(defaultextension=".log", initialfile="output.log", parent=self)
        if p:
            with open(p, "w", encoding="utf-8") as f:
                f.write(self.view.out.text())


class EditorWindow(tk.Toplevel):
    """แก้ไขไฟล์บนเซิร์ฟเวอร์โดยตรง (SFTP) — Ctrl+S เพื่อบันทึก"""
    def __init__(self, app, path):
        super().__init__(app.root)
        self.app, self.path, self.readonly = app, path, False
        self.title(f"📝 {path} — Termius Mini Pro")
        self.geometry("960x640")
        self.configure(bg=Theme.BG)
        bar = tk.Frame(self, bg=Theme.BG)
        bar.pack(fill=tk.X, padx=8, pady=(8, 4))
        ModernButton(bar, text="💾 Save (Ctrl+S)", command=self.save, bg=Theme.GREEN, fg="#10131a",
                     hover="#b9f27a", pady=4, font=Theme.FONT_SM).pack(side=tk.LEFT)
        ModernButton(bar, text="⟳ Reload", command=self.load, pady=4, font=Theme.FONT_SM).pack(side=tk.LEFT, padx=6)
        self.lbl = tk.Label(bar, text=path, fg=Theme.FG_DIM, bg=Theme.BG, font=Theme.FONT_SM)
        self.lbl.pack(side=tk.LEFT, padx=8)
        f = tk.Frame(self, bg=Theme.BG)
        f.pack(fill=tk.BOTH, expand=True, padx=8, pady=(0, 8))
        self.txt = tk.Text(f, bg=Theme.INPUT, fg=Theme.FG, insertbackground=Theme.FG, font=Theme.MONO,
                           relief=tk.FLAT, undo=True, wrap=tk.NONE, padx=10, pady=8)
        vs = ttk.Scrollbar(f, orient=tk.VERTICAL, command=self.txt.yview)
        hs = ttk.Scrollbar(f, orient=tk.HORIZONTAL, command=self.txt.xview)
        self.txt.configure(yscrollcommand=vs.set, xscrollcommand=hs.set)
        vs.pack(side=tk.RIGHT, fill=tk.Y)
        hs.pack(side=tk.BOTTOM, fill=tk.X)
        self.txt.pack(fill=tk.BOTH, expand=True)
        self.bind("<Control-s>", lambda e: self.save())
        self.load()

    def load(self):
        def work():
            s = self.app.ssh.new_sftp()
            try:
                if s.stat(self.path).st_size > 2 * 1024 * 1024:
                    raise IOError("ไฟล์ใหญ่เกิน 2 MB — ใช้ Download แทน")
                with s.open(self.path, "rb") as fh:
                    return fh.read()
            finally:
                s.close()

        def done(b):
            self.readonly = False
            try:
                t = b.decode("utf-8")
            except UnicodeDecodeError:
                t, self.readonly = b.decode("utf-8", "replace"), True
            self.txt.delete("1.0", tk.END)
            self.txt.insert("1.0", t)
            self.txt.edit_reset()
            self.lbl.config(text=self.path + ("   ⚠ ไม่ใช่ UTF-8 — ปิดการบันทึก" if self.readonly else ""))

        def fail(e):
            messagebox.showerror("Edit", str(e), parent=self)
            self.destroy()
        self.app.bg(work, done, fail)

    def save(self):
        if self.readonly:
            messagebox.showwarning("Save", "ไฟล์ไม่ใช่ UTF-8 — ไม่อนุญาตให้บันทึกทับ", parent=self)
            return
        data = self.txt.get("1.0", "end-1c").encode("utf-8")

        def work():
            s = self.app.ssh.new_sftp()
            try:
                s.putfo(io.BytesIO(data), self.path)
            finally:
                s.close()
        self.app.bg(work, lambda _: self.lbl.config(text=f"✔ บันทึกแล้ว {datetime.datetime.now():%H:%M:%S} — {self.path}"),
                    lambda e: messagebox.showerror("Save", str(e), parent=self))


# ══════════════════════════ PANEL BASE ══════════════════════════
class Panel(tk.Frame):
    """แท็บทุกอันสืบจากคลาสนี้ → วางในแท็บ หรือ แยกเป็นหน้าต่างอิสระได้"""
    NAME, TITLE = "", ""

    def __init__(self, parent, app, windowed=False, popbtn=True):
        super().__init__(parent, bg=Theme.BG)
        self.app, self.windowed = app, windowed
        self.bar = tk.Frame(self, bg=Theme.BG)
        self.bar.pack(fill=tk.X, padx=8, pady=(8, 4))
        if popbtn:
            ModernButton(self.bar, text="⧉ Dock กลับ" if windowed else "⧉ แยกหน้าต่าง",
                         command=lambda: app.toggle_window(self.NAME), pady=4,
                         font=Theme.FONT_SM).pack(side=tk.RIGHT)
        self.build()

    def build(self): pass
    def refresh(self): pass
    def on_connected(self): pass
    def on_disconnected(self): pass
    def snapshot(self): return None
    def restore(self, snap): pass
    def shutdown(self): pass

    def destroy(self):
        try:
            self.shutdown()
        except Exception:
            pass
        super().destroy()


# ══════════════════════════ TERMINAL ══════════════════════════
class TerminalPanel(Panel):
    NAME, TITLE = "terminal", "💻 Terminal"

    def build(self):
        self.cwd, self.stops = "~", set()
        self.history, self.hidx = [], 0
        bar = self.bar
        ModernButton(bar, text="🧹 Clear", command=self.clear, pady=4).pack(side=tk.LEFT)
        ModernButton(bar, text="💾 Save Log", command=self.save, pady=4).pack(side=tk.LEFT, padx=4)
        ModernButton(bar, text="⏹ Stop", command=self.stop_all, pady=4, hover=Theme.RED).pack(side=tk.LEFT)
        ModernButton(bar, text="🗔 Terminal ใหม่ (หน้าต่างแยก)", command=self.app.new_terminal_window,
                     pady=4).pack(side=tk.LEFT, padx=8)
        tk.Label(bar, text="↑/↓ ประวัติ • Enter รัน • Ctrl+C หยุด", fg=Theme.FG_DIM, bg=Theme.BG,
                 font=Theme.FONT_SM).pack(side=tk.RIGHT, padx=8)
        self.out = LogText(self)
        self.out.pack(fill=tk.BOTH, expand=True, padx=8, pady=4)
        row = tk.Frame(self, bg=Theme.BG)
        row.pack(fill=tk.X, padx=8, pady=(0, 8))
        self.lbl_cwd = tk.Label(row, text="~", fg=Theme.CYAN, bg=Theme.BG, font=("Consolas", 10, "bold"))
        self.lbl_cwd.pack(side=tk.LEFT)
        tk.Label(row, text=" ❯", fg=Theme.BLUE, bg=Theme.BG, font=("Consolas", 12, "bold")).pack(side=tk.LEFT)
        self.e_cmd = tk.Entry(row, bg=Theme.INPUT, fg="#e0e4f5", insertbackground=Theme.FG,
                              relief=tk.FLAT, font=Theme.MONO)
        self.e_cmd.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=8, ipady=7)
        self.e_cmd.bind("<Return>", lambda e: self.submit())
        self.e_cmd.bind("<Up>", self._nav)
        self.e_cmd.bind("<Down>", self._nav)
        self.e_cmd.bind("<Control-c>", lambda e: self.stop_all())
        ModernButton(row, text="Run ⏎", command=self.submit, bg=Theme.BLUE, fg="#16161e",
                     hover="#89ddff", font=Theme.FONT_BOLD).pack(side=tk.RIGHT)

    def welcome(self):
        w = self.out.write
        w("╔══════════════════════════════════════════╗\n", "prompt")
        w(f"║   ⚡ TERMius MINI PRO — v{APP_VER}              ║\n", "prompt")
        w("║   SSH · SFTP · Docker · Deploy Commander  ║\n", "prompt")
        w("╚══════════════════════════════════════════╝\n", "prompt")
        w("  พร้อมใช้งาน — เชื่อมต่อ SSH จากแถบด้านซ้าย\n", "info")
        w("  Tips: 'cd' จำตำแหน่งต่อเนื่อง • 'sudo ...' ส่งรหัสผ่านให้อัตโนมัติ • ⏹ Stop ฆ่าคำสั่งจริง\n\n", "dim")

    def snapshot(self):
        return {"log": self.out.snapshot(), "cwd": self.cwd, "hist": self.history}

    def restore(self, snap):
        if snap:
            self.out.restore(snap["log"])
            self.cwd, self.history = snap["cwd"], snap["hist"]
            self.hidx = len(self.history)
            self.lbl_cwd.config(text=self.cwd)

    def shutdown(self):
        self.stop_all()

    def clear(self):
        self.out.clear()

    def save(self):
        p = filedialog.asksaveasfilename(defaultextension=".log", initialfile="session.log",
                                         title="บันทึก Terminal Log")
        if p:
            with open(p, "w", encoding="utf-8") as f:
                f.write(self.out.text())

    def stop_all(self):
        for ev in list(self.stops):
            ev.set()
        return "break"

    def _nav(self, event):
        if not self.history:
            return "break"
        self.hidx = max(0, self.hidx - 1) if event.keysym == "Up" else min(len(self.history), self.hidx + 1)
        self.e_cmd.delete(0, tk.END)
        if self.hidx < len(self.history):
            self.e_cmd.insert(0, self.history[self.hidx])
        return "break"

    def submit(self):
        cmd = self.e_cmd.get().strip()
        if not cmd:
            return
        self.history.append(cmd)
        self.hidx = len(self.history)
        self.e_cmd.delete(0, tk.END)
        self.run(cmd)

    def run(self, cmd):
        self.out.write(f"\n{self.cwd} ❯ {cmd}\n", "prompt")
        ev = threading.Event()
        self.stops.add(ev)
        ssh, ui = self.app.ssh, self.app.ui
        prefix = "cd; " if self.cwd in ("~", "") else f"cd {q(self.cwd)} 2>/dev/null; "
        suffix = '\n__rc=$?; printf "\\n' + CWD_MARK + '%s\\n" "$(pwd)"; exit $__rc'

        def work():
            lb, newcwd = LineBuf(), [None]

            def emit(lines):
                keep = []
                for l in lines:
                    if CWD_MARK in l:
                        newcwd[0] = l.split(CWD_MARK, 1)[1].strip()
                    else:
                        keep.append(l)
                if keep:
                    ui(self.out.write, "\n".join(keep) + "\n", "out")
            code = None
            try:
                code = ssh.stream(cmd, lambda s: emit(lb.feed(strip_ansi(s))), stop=ev,
                                  pty=True, prefix=prefix, suffix=suffix)
            except Exception as e:
                ui(self.out.write, f"[SSH Error] {e}\n", "err")
            r = lb.rest()
            if r:
                emit([r])
            ui(self._done, ev, code, newcwd[0])
        threading.Thread(target=work, daemon=True).start()

    def _done(self, ev, code, newcwd):
        self.stops.discard(ev)
        if newcwd:
            self.cwd = newcwd
            self.lbl_cwd.config(text=newcwd)
        if code == -2:
            self.out.write("^C (หยุดแล้ว)\n", "dim")
        elif code not in (None, 0):
            self.out.write(f"[exit {code}]\n", "err")


# ══════════════════════════ FILES (SFTP) ══════════════════════════
class FilesPanel(Panel):
    NAME, TITLE = "files", "📂 Files (SFTP)"

    def build(self):
        a = self.app
        self.rows = {}
        for t, c in (("⬆️ Up", self.go_up), ("🏠 Home", self.go_home), ("🔄 Refresh (F5)", self.refresh)):
            ModernButton(self.bar, text=t, command=c, pady=4, font=Theme.FONT_SM).pack(side=tk.LEFT, padx=2)
        toolbar(self, [("➕ Folder", self.mkdir), ("✏️ Rename", self.rename), ("🗑️ Delete", self.delete),
                       ("🔐 chmod", self.chmod), None, ("📝 Edit", self.edit),
                       ("⬇️ Download", self.download), ("⬆️ Upload File", self.upload),
                       ("📂 Upload Folder", self.upload_folder)])
        prow = tk.Frame(self, bg=Theme.BG)
        prow.pack(fill=tk.X, padx=8, pady=4)
        tk.Label(prow, text="Path:", fg=Theme.FG_DIM, bg=Theme.BG, font=Theme.FONT_SM).pack(side=tk.LEFT)
        e = tk.Entry(prow, textvariable=a.current_remote_path, bg=Theme.INPUT, fg=Theme.FG,
                     insertbackground=Theme.FG, relief=tk.FLAT, font=Theme.MONO)
        e.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=6, ipady=4)
        e.bind("<Return>", lambda ev: self.refresh())
        ModernButton(prow, text="Go", command=self.refresh, bg=Theme.BLUE, fg="#16161e",
                     hover="#89ddff", pady=2).pack(side=tk.LEFT)

        tf = tk.Frame(self, bg=Theme.BG)
        tf.pack(fill=tk.BOTH, expand=True, padx=8, pady=4)
        cols = ("size", "mtime", "type", "perms")
        self.tree = ttk.Treeview(tf, columns=cols, selectmode="browse")
        self.tree.heading("#0", text="Name", anchor="w")
        for cid, label, w, anc in (("size", "Size", 90, "e"), ("mtime", "Modified", 135, "w"),
                                   ("type", "Type", 80, "w"), ("perms", "Permissions", 130, "w")):
            self.tree.heading(cid, text=label, anchor="w")
            self.tree.column(cid, width=w, anchor=anc, stretch=False)
        self.tree.column("#0", width=320, stretch=True)
        vs = ttk.Scrollbar(tf, orient=tk.VERTICAL, command=self.tree.yview)
        self.tree.configure(yscrollcommand=vs.set)
        self.tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        vs.pack(side=tk.RIGHT, fill=tk.Y)
        self.tree.bind("<Double-1>", self.open)
        self.tree.bind("<Button-3>", self._ctx)
        self.tree.bind("<Delete>", lambda e: self.delete())
        self.lbl = tk.Label(self, text="—", fg=Theme.FG_DIM, bg=Theme.BG, font=Theme.FONT_SM, anchor="w")
        self.lbl.pack(fill=tk.X, padx=10, pady=(0, 6))
        self.menu = popup_menu(self, [
            ("📂 Open / Navigate", self.open), ("📝 Edit", self.edit), ("⬇️ Download", self.download),
            ("📋 Copy Full Path", self.copy_path), None, ("✏️ Rename", self.rename),
            ("🔐 chmod", self.chmod), ("🗑️ Delete", self.delete)])

    def on_connected(self):
        self.refresh()

    # ---------- listing ----------
    def refresh(self, path=None):
        a = self.app
        p = (path or a.current_remote_path.get()).strip() or "."

        def work():
            s = a.ssh.sftp()
            home = a.ssh.home or s.normalize(".")
            a.ssh.home = home
            real = home if p in ("", "~") else (home + p[1:] if p.startswith("~/") else p)
            actual = s.normalize(real)
            return actual, s.listdir_attr(actual)
        a.bg(work, lambda r: self.render(*r), lambda e: messagebox.showerror("File Explorer", str(e)))

    def render(self, path, items):
        self.app.current_remote_path.set(path)
        self.tree.delete(*self.tree.get_children())
        self.rows = {}
        rows = []
        for at in items:
            if at.filename in (".", ".."):
                continue
            m = at.st_mode or 0
            kind = "dir" if stat.S_ISDIR(m) else ("link" if stat.S_ISLNK(m) else "file")
            rows.append((at.filename, kind, at))
        rows.sort(key=lambda r: (r[1] != "dir", r[0].lower()))
        for name, kind, at in rows:
            icon = {"dir": "📁", "link": "🔗", "file": "📄"}[kind]
            size = "—" if kind == "dir" else human_size(at.st_size)
            mt = datetime.datetime.fromtimestamp(at.st_mtime).strftime("%Y-%m-%d %H:%M") if at.st_mtime else "—"
            perms = stat.filemode(at.st_mode) if at.st_mode else "—"
            iid = self.tree.insert("", tk.END, text=f"{icon} {name}",
                                   values=(size, mt, {"dir": "Folder", "link": "Link", "file": "File"}[kind], perms))
            self.rows[iid] = (name, kind)
        self.lbl.config(text=f"{len(rows)} items  •  {path}")

    def sel(self):
        s = self.tree.selection()
        if not s:
            return None
        name, kind = self.rows[s[0]]
        return name, kind, posixpath.join(self.app.current_remote_path.get(), name)

    def _ctx(self, e):
        iid = self.tree.identify_row(e.y)
        if iid:
            self.tree.selection_set(iid)
            self.menu.tk_popup(e.x_root, e.y_root)

    def open(self, event=None):
        s = self.sel()
        if not s:
            return
        name, kind, path = s
        if kind == "dir":
            self.refresh(path)
        elif kind == "link":
            self.app.bg(lambda: stat.S_ISDIR(self.app.ssh.sftp().stat(path).st_mode),
                        lambda isdir: self.refresh(path) if isdir else self.download())
        else:
            self.download()

    def go_up(self):
        cur = self.app.current_remote_path.get().rstrip("/")
        parent = posixpath.dirname(cur)
        if parent and parent != cur:
            self.refresh(parent)
        elif cur and cur != "/":
            self.refresh("/")

    def go_home(self):
        self.refresh("~")

    def copy_path(self):
        s = self.sel()
        if s:
            self.clipboard_clear()
            self.clipboard_append(s[2])

    # ---------- file operations ----------
    def _remote(self, cmd, then_refresh=True):
        self.app.bg(lambda: self.app.ssh.run(cmd, timeout=120),
                    lambda r: ((messagebox.showerror("Error", (r[2] or r[1]).strip()[:400]) if r[0] != 0 else None),
                               self.refresh() if then_refresh else None),
                    lambda e: messagebox.showerror("Error", str(e)))

    def mkdir(self):
        name = simpledialog.askstring("New Folder", "ชื่อโฟลเดอร์ใหม่:", parent=self)
        if name:
            self._remote(f"mkdir -p {q(posixpath.join(self.app.current_remote_path.get(), name))}")

    def rename(self):
        s = self.sel()
        if not s:
            return
        new = simpledialog.askstring("Rename", f"ชื่อใหม่สำหรับ '{s[0]}':", initialvalue=s[0], parent=self)
        if new and new != s[0]:
            self._remote(f"mv {q(s[2])} {q(posixpath.join(self.app.current_remote_path.get(), new))}")

    def delete(self):
        s = self.sel()
        if not s:
            return
        if messagebox.askyesno("ยืนยันการลบ", f"ลบ '{s[0]}' ถาวรหรือไม่?\n\n{s[2]}"):
            self._remote(f"rm -rf {q(s[2])}" if s[1] == "dir" else f"rm -f {q(s[2])}")

    def chmod(self):
        s = self.sel()
        if not s:
            return
        mode = simpledialog.askstring("chmod", f"สิทธิ์ใหม่ของ '{s[0]}' (เช่น 755, 644):", parent=self)
        if mode and re.fullmatch(r"[0-7]{3,4}", mode.strip()):
            self._remote(f"chmod {mode.strip()} {q(s[2])}")
        elif mode:
            messagebox.showwarning("chmod", "รูปแบบไม่ถูกต้อง — ใช้เลขฐาน 8 เช่น 755")

    def edit(self):
        s = self.sel()
        if s and s[1] != "dir":
            EditorWindow(self.app, s[2])

    # ---------- transfers ----------
    def _cb(self, label):
        last = [0.0]

        def cb(sent, total):
            now = time.time()
            if now - last[0] < 0.1 and sent < total:
                return
            last[0] = now
            pct = sent * 100.0 / total if total else 0
            self.app.ui(self.app.progress, pct, f"{label} {pct:.0f}% ({human_size(sent)}/{human_size(total)})")
        return cb

    def _transfer(self, fn, title):
        ssh, app = self.app.ssh, self.app

        def work():
            s = ssh.new_sftp()
            try:
                fn(s)
            finally:
                s.close()
        app.bg(work, lambda _: (app.progress_done(), self.refresh()),
               lambda e: (app.progress_done(), messagebox.showerror(title, str(e))))

    def download(self):
        s = self.sel()
        if not s:
            return
        name, kind, remote = s
        log = self.app.log
        if kind == "dir":
            dest = filedialog.askdirectory(title=f"เลือกที่บันทึกโฟลเดอร์ '{name}'")
            if not dest:
                return
            ldir = os.path.join(dest, name)

            def count(sf, p):
                return sum(count(sf, f"{p}/{a.filename}") if stat.S_ISDIR(a.st_mode) else 1
                           for a in sf.listdir_attr(p))

            def fn(sf):
                total, done = max(1, count(sf, remote)), [0]
                os.makedirs(ldir, exist_ok=True)
                log(f"\n[SFTP] ⬇ ดาวน์โหลดโฟลเดอร์ {remote} ({total} ไฟล์)\n", "info")

                def rec(rd, ld):
                    for a in sf.listdir_attr(rd):
                        ri, li = f"{rd}/{a.filename}", os.path.join(ld, a.filename)
                        if stat.S_ISDIR(a.st_mode):
                            os.makedirs(li, exist_ok=True)
                            rec(ri, li)
                        else:
                            sf.get(ri, li)
                            done[0] += 1
                            self.app.ui(self.app.progress, done[0] * 100.0 / total, f"⬇ [{done[0]}/{total}] {a.filename}")
                rec(remote, ldir)
                log(f"[SFTP] ✔ ดาวน์โหลดครบ → {ldir}\n", "ok")
            self._transfer(fn, "Download Folder")
        else:
            dest = filedialog.asksaveasfilename(initialfile=name, title="บันทึกไฟล์")
            if not dest:
                return

            def fn(sf):
                log(f"\n[SFTP] ⬇ ดาวน์โหลด {remote}\n", "info")
                sf.get(remote, dest, callback=self._cb("⬇"))
                log(f"[SFTP] ✔ บันทึกที่ {dest}\n", "ok")
            self._transfer(fn, "Download")

    def upload(self):
        fp = filedialog.askopenfilename(title="เลือกไฟล์ที่ต้องการอัปโหลด")
        if not fp:
            return
        remote = posixpath.join(self.app.current_remote_path.get(), os.path.basename(fp))

        def fn(sf):
            self.app.log(f"\n[SFTP] ⬆ อัปโหลด {os.path.basename(fp)} → {remote}\n", "info")
            sf.put(fp, remote, callback=self._cb("⬆"), confirm=False)
            self.app.log(f"[SFTP] ✔ อัปโหลดเสร็จ: {remote}\n", "ok")
        self._transfer(fn, "Upload")

    def upload_folder(self):
        src = filedialog.askdirectory(title="เลือกโฟลเดอร์ที่ต้องการอัปโหลด")
        if not src:
            return
        rdir = posixpath.join(self.app.current_remote_path.get(), os.path.basename(src.rstrip("/\\")))

        def mkdir_p(sf, path):
            cur = ""
            for p in path.strip("/").split("/"):
                cur += "/" + p
                try:
                    sf.stat(cur)
                except IOError:
                    sf.mkdir(cur)

        def fn(sf):
            total, done = max(1, sum(len(fs) for _, _, fs in os.walk(src))), [0]
            self.app.log(f"\n[SFTP] ⬆ อัปโหลดโฟลเดอร์ → {rdir} ({total} ไฟล์)\n", "info")
            mkdir_p(sf, rdir)
            for root, dirs, files in os.walk(src):
                rel = os.path.relpath(root, src).replace("\\", "/")
                rd = rdir if rel == "." else f"{rdir}/{rel}"
                mkdir_p(sf, rd)
                for fn_ in files:
                    sf.put(os.path.join(root, fn_), f"{rd}/{fn_}", confirm=False)
                    done[0] += 1
                    self.app.ui(self.app.progress, done[0] * 100.0 / total, f"⬆ [{done[0]}/{total}] {fn_}")
            self.app.log(f"[SFTP] ✔ อัปโหลดครบ {total} ไฟล์\n", "ok")
        self._transfer(fn, "Upload Folder")


# ══════════════════════════ MONITOR / SERVER CONTROL ══════════════════════════
OVERVIEW_CMD = r"""
echo @@HOST; hostname
echo @@OS; (. /etc/os-release 2>/dev/null && echo "$PRETTY_NAME"); uname -r
echo @@UP; uptime -p 2>/dev/null || uptime
echo @@LOAD; cat /proc/loadavg
echo @@NPROC; nproc
echo @@CPU1; head -1 /proc/stat
sleep 0.4
echo @@CPU2; head -1 /proc/stat
echo @@MEM; grep -E '^(MemTotal|MemAvailable|SwapTotal|SwapFree):' /proc/meminfo
echo @@DISK; df -PhT -x tmpfs -x devtmpfs -x squashfs -x overlay -x efivarfs 2>/dev/null | tail -n +2
echo @@NET; tail -n +3 /proc/net/dev
echo @@END
"""

LOG_PRESETS = {
    "journalctl — ล่าสุด + follow": ("journalctl -n 200 -f --no-pager", True),
    "journalctl — เฉพาะ error": ("journalctl -p err -n 300 --no-pager", True),
    "auth.log / secure — follow": ("tail -n 200 -f /var/log/auth.log 2>/dev/null || tail -n 200 -f /var/log/secure", True),
    "syslog / messages — follow": ("tail -n 200 -f /var/log/syslog 2>/dev/null || tail -n 200 -f /var/log/messages", True),
    "dmesg (ล่าสุด)": ("dmesg | tail -n 300", True),
    "Login ล่าสุด": ("last -n 30", False),
    "Login ที่ล้มเหลว": ("lastb -n 30", True),
}


def parse_sections(text):
    sec, cur = {}, None
    for line in text.splitlines():
        if line.startswith("@@"):
            cur = line[2:].strip()
            sec[cur] = []
        elif cur:
            sec[cur].append(line)
    return sec


class MonitorPanel(Panel):
    NAME, TITLE = "monitor", "📈 Monitor"

    def build(self):
        self._job, self._busy, self._net_prev = None, False, None
        self._procs, self._svcs = [], []
        bar = self.bar
        ModernButton(bar, text="🔄 Refresh", command=self.refresh, bg=Theme.BLUE, fg="#16161e",
                     hover="#89ddff", font=Theme.FONT_BOLD).pack(side=tk.LEFT)
        self.v_auto = tk.BooleanVar(value=True)
        ttk.Checkbutton(bar, text="Live", variable=self.v_auto).pack(side=tk.LEFT, padx=(12, 2))
        self.v_int = tk.StringVar(value="5")
        ttk.Combobox(bar, textvariable=self.v_int, values=["2", "3", "5", "10", "30", "60"],
                     width=4, state="readonly").pack(side=tk.LEFT)
        tk.Label(bar, text="วินาที", fg=Theme.FG_DIM, bg=Theme.BG, font=Theme.FONT_SM).pack(side=tk.LEFT, padx=4)

        self.nb = ttk.Notebook(self)
        self.nb.pack(fill=tk.BOTH, expand=True, padx=8, pady=(0, 8))
        self.t_over, self.t_proc, self.t_svc, self.t_log = (tk.Frame(self.nb, bg=Theme.BG) for _ in range(4))
        for f, t in ((self.t_over, "📊 Overview"), (self.t_proc, "⚙️ Processes"),
                     (self.t_svc, "🧩 Services"), (self.t_log, "📜 System Logs")):
            self.nb.add(f, text=f"  {t}  ")
        self._build_overview()
        self._build_procs()
        self._build_services()
        self._build_logs()
        self.nb.bind("<<NotebookTabChanged>>", lambda e: self.refresh())
        self._job = self.after(1500, self._tick)

    def shutdown(self):
        if self._job:
            try:
                self.after_cancel(self._job)
            except Exception:
                pass
        if hasattr(self, "logview"):
            self.logview.stop()

    def on_connected(self):
        self._net_prev = None
        self.refresh()

    def refresh(self):
        if not self.app.ssh.connected():
            return
        i = self.nb.index(self.nb.select())
        (self.load_overview, self.load_procs, self.load_services, lambda: None)[i]()

    def _tick(self):
        try:
            if (self.v_auto.get() and self.winfo_ismapped() and self.app.ssh.connected() and not self._busy):
                i = self.nb.index(self.nb.select())
                if i == 0:
                    self.load_overview()
                elif i == 1:
                    self.load_procs()
            self._job = self.after(max(1, int(self.v_int.get() or 5)) * 1000, self._tick)
        except (tk.TclError, ValueError):
            pass

    # ---------- Overview ----------
    def _build_overview(self):
        f = self.t_over
        gr = tk.Frame(f, bg=Theme.BG)
        gr.pack(fill=tk.X, padx=8, pady=8)
        self.g = {}
        for i, k in enumerate(("CPU", "Memory", "Swap", "Disk /")):
            g = Gauge(gr, k)
            g.grid(row=0, column=i, sticky="ew", padx=4)
            gr.columnconfigure(i, weight=1)
            self.g[k] = g
        info = tk.Frame(f, bg=Theme.PANEL, padx=14, pady=10)
        info.pack(fill=tk.X, padx=12, pady=(0, 8))
        self.info = {}
        for i, k in enumerate(("Host", "OS / Kernel", "Uptime", "Load avg", "Network", "Cores")):
            tk.Label(info, text=k, fg=Theme.FG_DIM, bg=Theme.PANEL, font=Theme.FONT_SM).grid(
                row=i // 3, column=(i % 3) * 2, sticky="w", padx=(0, 8), pady=2)
            l = tk.Label(info, text="—", fg=Theme.FG, bg=Theme.PANEL, font=Theme.FONT_SM)
            l.grid(row=i // 3, column=(i % 3) * 2 + 1, sticky="w", padx=(0, 24), pady=2)
            self.info[k] = l
        toolbar(f, [
            ("🌐 Open Ports", lambda: self.app.run_in_terminal("sudo ss -tulpn | grep LISTEN | head -25")),
            ("⚙️ Top CPU", lambda: self.app.run_in_terminal("ps aux --sort=-%cpu | head -12")),
            ("👥 Who", lambda: self.app.run_in_terminal("who -a")),
            ("📜 Logins", lambda: self.app.run_in_terminal("last -n 10")),
            None,
            ("🔄 Reboot", self.app.server_reboot), ("⏻ Power Off", self.app.server_shutdown)])
        fr, self.tree_disk = make_table(f, [
            ("fs", "Filesystem", 200, "w", True), ("type", "Type", 70, "w", False),
            ("size", "Size", 80, "e", False), ("used", "Used", 80, "e", False),
            ("avail", "Avail", 80, "e", False), ("pct", "Use%", 70, "e", False),
            ("mount", "Mounted on", 220, "w", True)], tags={"warn": Theme.ORANGE, "crit": Theme.RED})
        fr.pack(fill=tk.BOTH, expand=True, padx=8, pady=6)

    def load_overview(self):
        self._busy = True

        def done(r):
            self._busy = False
            code, out, err = r
            if "@@END" in out:
                self._apply(parse_sections(out))
            else:
                self.app.set_status("err", (err or out).strip()[:80] or "อ่านข้อมูลระบบไม่ได้")

        def fail(e):
            self._busy = False
            self.app.show_error(e)
        self.app.bg(lambda: self.app.ssh.run(OVERVIEW_CMD, timeout=25), done, fail)

    def _apply(self, s):
        g, first = self.g, (lambda k: (s.get(k) or [""])[0].strip())
        n, load = first("NPROC"), first("LOAD").split()[:3]
        try:
            a = [int(x) for x in s["CPU1"][0].split()[1:]]
            b = [int(x) for x in s["CPU2"][0].split()[1:]]
            cpu = 100.0 * (1 - ((b[3] + b[4]) - (a[3] + a[4])) / max(1, sum(b[:8]) - sum(a[:8])))
            g["CPU"].set(cpu, f"{cpu:.0f}%", f"{n} cores • load {' '.join(load)}")
        except Exception:
            pass
        try:
            mem = {}
            for l in s.get("MEM", []):
                k, _, v = l.partition(":")
                mem[k] = int(v.split()[0]) * 1024
            mt, ma = mem["MemTotal"], mem["MemAvailable"]
            g["Memory"].set((mt - ma) * 100.0 / mt, f"{(mt - ma) * 100.0 / mt:.0f}%",
                            f"{human_size(mt - ma)} / {human_size(mt)}")
            st, sf = mem.get("SwapTotal", 0), mem.get("SwapFree", 0)
            g["Swap"].set((st - sf) * 100.0 / st if st else 0, f"{(st - sf) * 100.0 / st:.0f}%" if st else "—",
                          f"{human_size(st - sf)} / {human_size(st)}" if st else "ไม่มี swap")
        except Exception:
            pass
        rows = []
        for l in s.get("DISK", []):
            p = l.split(None, 6)
            if len(p) == 7:
                rows.append(tuple(p))
        fill_tree(self.tree_disk, rows, lambda r: r[6],
                  lambda r: "crit" if r[5].rstrip("%").isdigit() and int(r[5].rstrip("%")) >= 90 else
                  ("warn" if r[5].rstrip("%").isdigit() and int(r[5].rstrip("%")) >= 80 else ""))
        root = next((r for r in rows if r[6] == "/"), None)
        if root and root[5].rstrip("%").isdigit():
            g["Disk /"].set(int(root[5].rstrip("%")), root[5], f"{root[3]} / {root[2]}")
        rx = tx = 0
        for l in s.get("NET", []):
            if ":" in l:
                ifc, _, rest = l.partition(":")
                if ifc.strip() != "lo":
                    f = rest.split()
                    rx, tx = rx + int(f[0]), tx + int(f[8])
        now, net = time.time(), "—"
        if self._net_prev and now > self._net_prev[0]:
            dt = now - self._net_prev[0]
            net = f"↓ {human_size(max(0, rx - self._net_prev[1]) / dt)}/s   ↑ {human_size(max(0, tx - self._net_prev[2]) / dt)}/s"
        self._net_prev = (now, rx, tx)
        os_l = s.get("OS", ["", ""])
        self.info["Host"].config(text=first("HOST"))
        self.info["OS / Kernel"].config(text=f"{os_l[0].strip()} • {os_l[1].strip() if len(os_l) > 1 else ''}")
        self.info["Uptime"].config(text=first("UP"))
        self.info["Load avg"].config(text=" ".join(load))
        self.info["Network"].config(text=net)
        self.info["Cores"].config(text=n)

    # ---------- Processes ----------
    def _build_procs(self):
        f = self.t_proc
        tb = toolbar(f, [("🔄 Refresh", self.load_procs), None,
                         ("⚠️ Kill (TERM)", lambda: self.kill("TERM")),
                         ("☠️ Force Kill (-9)", lambda: self.kill("KILL"))])
        tk.Label(tb, text="เรียงตาม:", fg=Theme.FG_DIM, bg=Theme.BG, font=Theme.FONT_SM).pack(side=tk.LEFT, padx=(14, 4))
        self.v_sort = tk.StringVar(value="CPU")
        cb = ttk.Combobox(tb, textvariable=self.v_sort, values=["CPU", "MEM"], width=6, state="readonly")
        cb.pack(side=tk.LEFT)
        cb.bind("<<ComboboxSelected>>", lambda e: self.load_procs())
        tk.Label(tb, text="ค้นหา:", fg=Theme.FG_DIM, bg=Theme.BG, font=Theme.FONT_SM).pack(side=tk.LEFT, padx=(14, 4))
        self.v_pf = tk.StringVar()
        tk.Entry(tb, textvariable=self.v_pf, bg=Theme.INPUT, fg=Theme.FG, insertbackground=Theme.FG,
                 relief=tk.FLAT, width=22).pack(side=tk.LEFT, ipady=3)
        self.v_pf.trace_add("write", lambda *_: self._show_procs())
        fr, self.tree_p = make_table(f, [
            ("pid", "PID", 70, "e", False), ("user", "User", 110, "w", False), ("cpu", "CPU%", 70, "e", False),
            ("mem", "MEM%", 70, "e", False), ("rss", "RSS", 90, "e", False), ("cmd", "Command", 500, "w", True)])
        fr.pack(fill=tk.BOTH, expand=True, padx=4, pady=4)

    def load_procs(self):
        key = "-pmem" if self.v_sort.get() == "MEM" else "-pcpu"
        cmd = f"ps -eo pid=,user=,pcpu=,pmem=,rss=,args= --sort={key} | head -120"

        def done(r):
            rows = []
            for l in r[1].splitlines():
                p = l.split(None, 5)
                if len(p) == 6:
                    rows.append((p[0], p[1], p[2], p[3], human_size(int(p[4]) * 1024) if p[4].isdigit() else p[4], p[5][:200]))
            self._procs = rows
            self._show_procs()
        self.app.bg(lambda: self.app.ssh.run(cmd, timeout=20), done)

    def _show_procs(self):
        f = self.v_pf.get().lower()
        fill_tree(self.tree_p, [r for r in self._procs if not f or f in " ".join(r).lower()], lambda r: r[0])

    def kill(self, sig):
        rows = sel_rows(self.tree_p)
        if not rows:
            return
        pids = " ".join(r[0] for r in rows)
        if not messagebox.askyesno("Kill", f"ส่งสัญญาณ {sig} ไปยัง PID: {pids} ?\n\n{rows[0][5][:80]}"):
            return

        def work():
            code, _, err = self.app.ssh.run(f"kill -{sig} {pids}")
            if code != 0:
                code, _, err = self.app.ssh.run(f"sudo kill -{sig} {pids}")
            return code, err
        self.app.bg(work, lambda r: (self.app.set_status("ok" if r[0] == 0 else "err",
                                                          f"kill -{sig} {pids}" if r[0] == 0 else r[1].strip()[:80]),
                                     self.load_procs()))

    # ---------- Services ----------
    def _build_services(self):
        f = self.t_svc
        tb = toolbar(f, [("🔄 Refresh", self.load_services), None,
                         ("▶ Start", lambda: self.svc("start")), ("⏹ Stop", lambda: self.svc("stop")),
                         ("🔁 Restart", lambda: self.svc("restart")), ("♻ Reload", lambda: self.svc("reload")), None,
                         ("✅ Enable", lambda: self.svc("enable")), ("🚫 Disable", lambda: self.svc("disable")), None,
                         ("ℹ Status", self.svc_status), ("📜 Logs", self.svc_logs),
                         ("⟲ daemon-reload", lambda: self.app.run_in_terminal("sudo systemctl daemon-reload"))])
        self.v_svc_run = tk.BooleanVar(value=False)
        ttk.Checkbutton(tb, text="เฉพาะที่รันอยู่", variable=self.v_svc_run,
                        command=self._show_svcs).pack(side=tk.LEFT, padx=(12, 4))
        self.v_sf = tk.StringVar()
        tk.Entry(tb, textvariable=self.v_sf, bg=Theme.INPUT, fg=Theme.FG, insertbackground=Theme.FG,
                 relief=tk.FLAT, width=20).pack(side=tk.LEFT, ipady=3)
        self.v_sf.trace_add("write", lambda *_: self._show_svcs())
        fr, self.tree_s = make_table(f, [
            ("unit", "Service", 260, "w", False), ("load", "Load", 70, "w", False),
            ("active", "Active", 80, "w", False), ("sub", "Sub", 90, "w", False),
            ("desc", "Description", 420, "w", True)],
            tags={"run": Theme.GREEN, "fail": Theme.RED, "off": Theme.FG_DIM}, selectmode="browse")
        fr.pack(fill=tk.BOTH, expand=True, padx=4, pady=4)

    def load_services(self):
        cmd = "systemctl list-units --type=service --all --no-pager --no-legend --plain 2>/dev/null"

        def done(r):
            rows = []
            for l in r[1].splitlines():
                p = l.split(None, 4)
                if len(p) >= 4:
                    rows.append((p[0], p[1], p[2], p[3], p[4] if len(p) > 4 else ""))
            self._svcs = rows
            self._show_svcs()
        self.app.bg(lambda: self.app.ssh.run(cmd, timeout=25), done)

    def _show_svcs(self):
        f, only = self.v_sf.get().lower(), self.v_svc_run.get()
        rows = [r for r in self._svcs if (not only or r[3] == "running") and (not f or f in (r[0] + r[4]).lower())]
        fill_tree(self.tree_s, rows, lambda r: r[0],
                  lambda r: "fail" if r[2] == "failed" else ("run" if r[3] == "running" else "off"))

    def _unit(self):
        r = sel_rows(self.tree_s)
        return r[0][0] if r else None

    def svc(self, action):
        u = self._unit()
        if not u:
            return
        if action in ("stop", "disable") and not messagebox.askyesno("ยืนยัน", f"{action} {u} ?"):
            return
        self.app.bg(lambda: self.app.ssh.run(f"systemctl {action} {q(u)}", sudo=True, timeout=60),
                    lambda r: (self.app.set_status("ok" if r[0] == 0 else "err",
                                                   f"systemctl {action} {u}" if r[0] == 0 else (r[2] or r[1]).strip()[:90]),
                               self.load_services()))

    def svc_status(self):
        u = self._unit()
        if u:
            ConsoleWindow(self.app, f"Status: {u}", cmd=f"systemctl status {q(u)} --no-pager -l", sudo=True, pty=False)

    def svc_logs(self):
        u = self._unit()
        if u:
            ConsoleWindow(self.app, f"Logs: {u}", cmd=f"journalctl -u {q(u)} -n 200 -f --no-pager", sudo=True)

    # ---------- System logs ----------
    def _build_logs(self):
        f = self.t_log
        tb = tk.Frame(f, bg=Theme.BG)
        tb.pack(fill=tk.X, padx=4, pady=(6, 2))
        self.v_preset = tk.StringVar(value=list(LOG_PRESETS)[0])
        ttk.Combobox(tb, textvariable=self.v_preset, values=list(LOG_PRESETS), width=34,
                     state="readonly").pack(side=tk.LEFT, padx=2)
        self.btn_log = ModernButton(tb, text="▶ Run", command=self._toggle_log, bg=Theme.BLUE, fg="#16161e",
                                    hover="#89ddff", pady=4, font=Theme.FONT_SM)
        self.btn_log.pack(side=tk.LEFT, padx=4)
        ModernButton(tb, text="🗔 หน้าต่างแยก", command=self._log_window, pady=4,
                     font=Theme.FONT_SM).pack(side=tk.LEFT)
        tk.Label(tb, text="Filter:", fg=Theme.FG_DIM, bg=Theme.BG, font=Theme.FONT_SM).pack(side=tk.LEFT, padx=(14, 4))
        self.v_lf = tk.StringVar()
        tk.Entry(tb, textvariable=self.v_lf, bg=Theme.INPUT, fg=Theme.FG, insertbackground=Theme.FG,
                 relief=tk.FLAT, width=22).pack(side=tk.LEFT, ipady=3)
        self.logview = StreamView(f, self.app, on_state=lambda r: self.btn_log.config(text="⏹ Stop" if r else "▶ Run"))
        self.logview.pack(fill=tk.BOTH, expand=True, padx=4, pady=4)
        self.v_lf.trace_add("write", lambda *_: self.logview.set_filter(self.v_lf.get()))

    def _toggle_log(self):
        if self.logview.running:
            self.logview.stop()
            return
        cmd, sudo = LOG_PRESETS[self.v_preset.get()]
        self.logview.start(cmd, sudo=sudo, pty=True)

    def _log_window(self):
        cmd, sudo = LOG_PRESETS[self.v_preset.get()]
        ConsoleWindow(self.app, self.v_preset.get(), cmd=cmd, sudo=sudo)


# ══════════════════════════ DEPLOY ══════════════════════════
class DeployPanel(Panel):
    NAME, TITLE = "deploy", "🚀 Deploy"

    def build(self):
        a = self.app
        self._traces = []
        self.bar.pack_forget()
        self.bar.pack(fill=tk.X, padx=8, pady=(4, 0))
        wrap = tk.Frame(self, bg=Theme.BG)
        wrap.pack(fill=tk.BOTH, expand=True, padx=16, pady=8)
        left = tk.Frame(wrap, bg=Theme.BG, width=380)
        left.pack(side=tk.LEFT, fill=tk.Y, padx=(0, 16))
        left.pack_propagate(False)
        right = tk.Frame(wrap, bg=Theme.BG)
        right.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        def sec(title):
            tk.Label(left, text=title, fg=Theme.FG_DIM, bg=Theme.BG,
                     font=("Segoe UI", 9, "bold")).pack(anchor="w", pady=(12, 2))

        tk.Label(left, text="🚀 Deploy Center", fg="white", bg=Theme.BG, font=Theme.FONT_TITLE).pack(anchor="w")
        sec("📦 APPLICATION")
        field(left, "Project / Service Name", a.dep_name)
        field(left, "Docker Image", a.dep_image)
        field(left, "Host Port", a.dep_hport, width=10)
        field(left, "Container Port", a.dep_cport, width=10)
        sec("📄 COMPOSE FILE (บนเซิร์ฟเวอร์)")
        field(left, "โฟลเดอร์ปลายทาง (Remote Directory)", a.dep_dir)
        field(left, "ชื่อไฟล์", a.dep_fname)
        pb = tk.Frame(left, bg=Theme.BG)
        pb.pack(fill=tk.X, pady=(4, 0))
        ModernButton(pb, text="📂 ใช้ Path จาก Explorer", command=self.use_explorer_path, pady=3,
                     font=Theme.FONT_SM).pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 4))
        ModernButton(pb, text="🏠 ~/{ชื่อโปรเจกต์}", command=self.use_home_project, pady=3,
                     font=Theme.FONT_SM).pack(side=tk.LEFT)
        self.lbl_full = tk.Label(left, text="", fg=Theme.CYAN, bg=Theme.BG, font=("Consolas", 9), anchor="w")
        self.lbl_full.pack(anchor="w", pady=(6, 0))
        sec("🌐 EXTRA SERVICES")
        ttk.Checkbutton(left, text="เพิ่ม Cloudflare Tunnel service", variable=a.dep_tunnel).pack(anchor="w")
        field(left, "Tunnel Token (ถ้ามี)", a.dep_token)
        sec("⚙️ ADVANCED (ไม่บังคับ)")

        def small_text(label, hint, content, attr):
            tk.Label(left, text=label, fg=Theme.FG_DIM, bg=Theme.BG, font=Theme.FONT_SM).pack(anchor="w", pady=(4, 1))
            t = tk.Text(left, height=3, bg=Theme.INPUT, fg=Theme.FG, insertbackground=Theme.FG,
                        relief=tk.FLAT, font=Theme.FONT_SM, padx=8, pady=5)
            t.pack(fill=tk.X)
            t.insert("1.0", content)
            tk.Label(left, text=hint, fg=Theme.FG_DIM, bg=Theme.BG, font=("Segoe UI", 8, "italic")).pack(anchor="w")

            def changed(_=None):
                setattr(a, attr, t.get("1.0", "end-1c"))
                self.update_preview()
            t.bind("<KeyRelease>", changed)
            return t
        self.txt_env = small_text("Environment Variables", "รูปแบบ: KEY=value (หนึ่งตัวแปร/บรรทัด)", a.dep_env_text, "dep_env_text")
        self.txt_vol = small_text("Volumes", "รูปแบบ: /host/path:/container/path (หนึ่งเส้นทาง/บรรทัด)", a.dep_vol_text, "dep_vol_text")

        ModernButton(left, text="🚀 Deploy Now", command=self.deploy_now, bg=Theme.GREEN, fg="#10131a",
                     hover="#b9f27a", font=Theme.FONT_BOLD, pady=9).pack(fill=tk.X, pady=(14, 4))
        br = tk.Frame(left, bg=Theme.BG)
        br.pack(fill=tk.X)
        ModernButton(br, text="💾 บันทึกไฟล์ Compose เท่านั้น", command=self.save_only, pady=5,
                     font=Theme.FONT_SM).pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 4))
        ModernButton(br, text="📋 Copy", command=self.copy, pady=5, font=Theme.FONT_SM).pack(side=tk.LEFT)

        tk.Label(right, text="📄 docker-compose.yml (Preview สด)", fg=Theme.FG_DIM, bg=Theme.BG,
                 font=Theme.FONT_SM).pack(anchor="w", pady=(0, 4))
        self.preview = tk.Text(right, bg=Theme.INPUT, fg=Theme.FG, font=Theme.MONO, relief=tk.FLAT, padx=14, pady=10)
        self.preview.pack(fill=tk.BOTH, expand=True)
        tk.Label(right, text="⌨️ คำสั่งที่จะรันบนเซิร์ฟเวอร์", fg=Theme.FG_DIM, bg=Theme.BG,
                 font=Theme.FONT_SM).pack(anchor="w", pady=(8, 4))
        self.cmd_preview = tk.Text(right, bg=Theme.INPUT, fg=Theme.ORANGE, font=Theme.MONO, height=4,
                                   relief=tk.FLAT, padx=12, pady=8)
        self.cmd_preview.pack(fill=tk.X)

        for v in (a.dep_name, a.dep_hport, a.dep_cport, a.dep_image, a.dep_token, a.dep_dir, a.dep_fname):
            self._traces.append((v, v.trace_add("write", lambda *_: self.update_preview())))
        self._traces.append((a.dep_tunnel, a.dep_tunnel.trace_add("write", lambda *_: self.update_preview())))
        self.update_preview()

    def shutdown(self):
        for v, tid in self._traces:
            try:
                v.trace_remove("write", tid)
            except Exception:
                pass

    def use_explorer_path(self):
        p = self.app.current_remote_path.get().strip()
        if p and p != "~":
            self.app.dep_dir.set(p)
        else:
            self.use_home_project()

    def use_home_project(self):
        self.app.dep_dir.set(f"~/{self.app.dep_name.get().strip() or 'app'}")

    def update_preview(self):
        a = self.app
        for w, txt in ((self.preview, a.build_compose()), (self.cmd_preview, self._cmd_preview())):
            w.config(state=tk.NORMAL)
            w.delete("1.0", tk.END)
            w.insert(tk.END, txt)
            w.config(state=tk.DISABLED)
        self.lbl_full.config(text=f"→ ไฟล์จะถูกสร้างที่: {a.remote_compose_path()}")

    def _cmd_preview(self):
        a = self.app
        p, n = a.remote_compose_path(), a.dep_name.get().strip() or "app"
        return f"$ {a.compose_bin} -f {p} -p {n} up -d\n$ {a.compose_bin} -f {p} ps"

    def copy(self):
        self.clipboard_clear()
        self.clipboard_append(self.app.build_compose())
        messagebox.showinfo("Copied", "คัดลอก docker-compose.yml ไปยัง Clipboard แล้ว")

    # ---------- เขียนไฟล์ Compose ผ่าน SFTP (รันใน worker thread) ----------
    def _write_compose_file(self):
        a = self.app
        s = a.ssh.sftp()
        home = a.ssh.home or s.normalize(".")
        a.ssh.home = home
        raw = a.remote_compose_path()
        full = home + raw[1:] if raw == "~" or raw.startswith("~/") else (raw if raw.startswith("/") else home + "/" + raw)
        cur = ""
        for p in posixpath.dirname(full).strip("/").split("/"):
            cur += "/" + p
            try:
                s.stat(cur)
            except IOError:
                s.mkdir(cur)
        try:
            s.stat(full)
            exists = True
        except IOError:
            exists = False
        if exists:
            ans, ev = {"ok": False}, threading.Event()

            def ask():
                ans["ok"] = messagebox.askyesno("ยืนยันการเขียนทับ",
                                                f"ไฟล์นี้มีอยู่แล้วบนเซิร์ฟเวอร์:\n{full}\n\nเขียนทับด้วยค่าใหม่หรือไม่?")
                ev.set()
            a.ui(ask)
            ev.wait()
            if not ans["ok"]:
                return None
        s.putfo(io.BytesIO(a.build_compose().encode("utf-8")), full)
        return full, s.stat(full).st_size

    def save_only(self):
        a = self.app
        if not a.dep_name.get().strip():
            messagebox.showwarning("Deploy", "กรุณาตั้งชื่อโปรเจกต์ก่อน")
            return
        a.show("terminal")

        def work():
            a.log("\n[Compose] 💾 กำลังสร้างไฟล์ Compose...\n", "info")
            res = self._write_compose_file()
            if res is None:
                a.log("[Compose] ยกเลิก — ไม่ได้เขียนไฟล์\n", "dim")
                return
            full, size = res
            a.log(f"[Compose] ✔ สร้างไฟล์สำเร็จ: {full} ({human_size(size)})\n", "ok")
            a.ui(messagebox.showinfo, "Save Compose",
                 f"สร้างไฟล์สำเร็จ:\n{full}\n\nรันเองได้ด้วยคำสั่ง:\n{a.compose_bin} -f {full} up -d")
        a.bg(work)

    def deploy_now(self):
        a = self.app
        name = a.dep_name.get().strip()
        if not name:
            messagebox.showwarning("Deploy", "กรุณาตั้งชื่อโปรเจกต์ก่อน")
            return
        a.show("terminal")

        def work():
            a.log(f"\n═══ 🚀 Deploy '{name}' — Step 1: สร้างไฟล์ Compose ═══\n", "info")
            res = self._write_compose_file()
            if res is None:
                a.log("[Deploy] ยกเลิก — ไม่ได้เขียนไฟล์\n", "dim")
                return
            full, size = res
            a.log(f"[Deploy] ✔ ไฟล์พร้อมแล้ว: {full} ({human_size(size)})\n", "ok")
            a.log(f"\n═══ Step 2: Deploy จากไฟล์ ═══\n$ {a.compose_bin} -f {full} -p {name} up -d\n\n", "prompt")
            cb = a.compose_bin
            alt = "docker-compose" if cb == "docker compose" else "docker compose"
            up = f"({cb} -f {q(full)} -p {q(name)} up -d || {alt} -f {q(full)} -p {q(name)} up -d)"
            ps = f"({cb} -f {q(full)} ps || {alt} -f {q(full)} ps)"
            lbs = [LineBuf(), LineBuf()]

            def mk(i):
                def f(s):
                    ls = lbs[i].feed(strip_ansi(s))
                    if ls:
                        a.log("\n".join(ls) + "\n", "out" if i == 0 else "dim")
                return f
            code = a.ssh.stream(f"{up} && {ps}", mk(0), mk(1), pty=False, sudo=a.ssh.docker_sudo)
            if code == 0:
                a.log(f"\n[OK] ✅ Deploy '{name}' สำเร็จ!\n", "ok")
                a.ui(messagebox.showinfo, "Deploy", f"Deploy '{name}' สำเร็จ!\n\nไฟล์ compose: {full}")
                a.ui(a.panel("docker").refresh)
            else:
                a.log(f"\n[FAIL] Deploy ล้มเหลว (exit {code})\n", "err")
                a.ui(messagebox.showerror, "Deploy", f"Deploy ล้มเหลว (exit {code}) — ดูรายละเอียดใน Terminal")
        a.bg(work)


# ══════════════════════════ DOCKER ══════════════════════════
class RunDialog(tk.Toplevel):
    """ฟอร์ม docker run — สร้างคำสั่งสดและรันผ่านหน้าต่างแยก"""
    def __init__(self, app, image=""):
        super().__init__(app.root)
        self.app = app
        self.title("➕ Run New Container")
        self.configure(bg=Theme.BG)
        self.geometry("560x760")
        self.v = {k: tk.StringVar(value=d) for k, d in (
            ("image", image), ("name", ""), ("ports", ""), ("network", ""),
            ("restart", "unless-stopped"), ("cmd", ""), ("extra", ""))}
        self.v_rm, self.v_priv = tk.BooleanVar(value=False), tk.BooleanVar(value=False)
        wrap = tk.Frame(self, bg=Theme.BG)
        wrap.pack(fill=tk.BOTH, expand=True, padx=16, pady=10)
        field(wrap, "Image *", self.v["image"])
        field(wrap, "ชื่อ Container", self.v["name"])
        field(wrap, "Ports (host:container คั่นด้วย , หรือเว้นวรรค)", self.v["ports"])
        field(wrap, "Network (ถ้ามี)", self.v["network"])
        tk.Label(wrap, text="Restart policy", fg=Theme.FG_DIM, bg=Theme.BG, font=Theme.FONT_SM).pack(anchor="w", pady=(8, 1))
        ttk.Combobox(wrap, textvariable=self.v["restart"], state="readonly",
                     values=["no", "always", "unless-stopped", "on-failure"]).pack(fill=tk.X)
        field(wrap, "Command (ถ้าต้องการแทนค่าเริ่มต้นของ image)", self.v["cmd"])
        field(wrap, "Flags เพิ่มเติม (เช่น --memory 512m)", self.v["extra"])
        self.t_env = self._text(wrap, "Environment (KEY=value บรรทัดละตัว)")
        self.t_vol = self._text(wrap, "Volumes (/host:/container บรรทัดละตัว)")
        cf = tk.Frame(wrap, bg=Theme.BG)
        cf.pack(anchor="w", pady=6)
        ttk.Checkbutton(cf, text="--rm (ลบเมื่อหยุด)", variable=self.v_rm).pack(side=tk.LEFT, padx=(0, 12))
        ttk.Checkbutton(cf, text="--privileged", variable=self.v_priv).pack(side=tk.LEFT)
        self.lbl = tk.Label(wrap, text="", fg=Theme.ORANGE, bg=Theme.BG, font=("Consolas", 9),
                            justify=tk.LEFT, wraplength=520, anchor="w")
        self.lbl.pack(fill=tk.X, pady=6)
        ModernButton(wrap, text="🚀 Run", command=self.go, bg=Theme.GREEN, fg="#10131a",
                     hover="#b9f27a", font=Theme.FONT_BOLD, pady=8).pack(fill=tk.X)
        for v in list(self.v.values()) + [self.v_rm, self.v_priv]:
            v.trace_add("write", lambda *_: self.preview())
        self.preview()

    def _text(self, parent, label):
        tk.Label(parent, text=label, fg=Theme.FG_DIM, bg=Theme.BG, font=Theme.FONT_SM).pack(anchor="w", pady=(8, 1))
        t = tk.Text(parent, height=3, bg=Theme.INPUT, fg=Theme.FG, insertbackground=Theme.FG,
                    relief=tk.FLAT, font=Theme.FONT_SM, padx=8, pady=5)
        t.pack(fill=tk.X)
        t.bind("<KeyRelease>", lambda e: self.preview())
        return t

    def build(self):
        v = {k: x.get().strip() for k, x in self.v.items()}
        p = ["docker run -d"]
        if v["name"]:
            p.append(f"--name {q(v['name'])}")
        p += [f"-p {x}" for x in re.split(r"[,\s]+", v["ports"]) if x]
        if v["restart"] != "no":
            p.append(f"--restart {v['restart']}")
        if v["network"]:
            p.append(f"--network {q(v['network'])}")
        p += [f"-e {q(l.strip())}" for l in self.t_env.get("1.0", tk.END).splitlines() if l.strip()]
        p += [f"-v {q(l.strip())}" for l in self.t_vol.get("1.0", tk.END).splitlines() if l.strip()]
        if self.v_rm.get():
            p.append("--rm")
        if self.v_priv.get():
            p.append("--privileged")
        if v["extra"]:
            p.append(v["extra"])
        p.append(v["image"] or "<image>")
        if v["cmd"]:
            p.append(v["cmd"])
        return " ".join(p)

    def preview(self):
        self.lbl.config(text=self.build())

    def go(self):
        if not self.v["image"].get().strip():
            messagebox.showwarning("Run", "กรุณากรอก Image", parent=self)
            return
        cmd = self.build()
        self.destroy()
        ConsoleWindow(self.app, "docker run", cmd=cmd, sudo=self.app.ssh.docker_sudo, pty=False, size="820x420")
        self.app.root.after(2500, self.app.panel("docker").refresh)


class DockerPanel(Panel):
    NAME, TITLE = "docker", "🐳 Docker"

    def build(self):
        a = self.app
        self._jobs, self._stats_busy, self.c_rows, self._flt = [], False, [], ""
        bar = self.bar
        ModernButton(bar, text="🔄 Refresh", command=self.refresh, bg=Theme.BLUE, fg="#16161e",
                     hover="#89ddff", font=Theme.FONT_BOLD, pady=4).pack(side=tk.LEFT)
        ttk.Checkbutton(bar, text="ใช้ sudo docker", variable=a.docker_sudo).pack(side=tk.LEFT, padx=12)
        self.v_all = tk.BooleanVar(value=True)
        ttk.Checkbutton(bar, text="แสดงทุก container (-a)", variable=self.v_all,
                        command=self.load_containers).pack(side=tk.LEFT)
        self.lbl_ver = tk.Label(bar, text="", fg=Theme.CYAN, bg=Theme.BG, font=Theme.FONT_SM)
        self.lbl_ver.pack(side=tk.LEFT, padx=14)
        self.lbl_msg = tk.Label(bar, text="", fg=Theme.FG_DIM, bg=Theme.BG, font=Theme.FONT_SM)
        self.lbl_msg.pack(side=tk.LEFT)

        self.nb = ttk.Notebook(self)
        self.nb.pack(fill=tk.BOTH, expand=True, padx=8, pady=(0, 8))
        self.tabs, order = {}, ("containers", "images", "volumes", "networks", "compose", "stats", "system")
        for key, title in zip(order, ("📦 Containers", "🖼 Images", "💽 Volumes", "🔗 Networks",
                                      "🧩 Compose", "📊 Stats", "⚙️ System")):
            f = tk.Frame(self.nb, bg=Theme.BG)
            self.nb.add(f, text=f"  {title}  ")
            self.tabs[key] = f
        self.order = order
        self._tab_containers(self.tabs["containers"])
        self._tab_images(self.tabs["images"])
        self._tab_volumes(self.tabs["volumes"])
        self._tab_networks(self.tabs["networks"])
        self._tab_compose(self.tabs["compose"])
        self._tab_stats(self.tabs["stats"])
        self._tab_system(self.tabs["system"])
        self.nb.bind("<<NotebookTabChanged>>", lambda e: self.refresh())
        self._jobs.append(self.after(3000, self._stats_tick))

    def shutdown(self):
        for j in self._jobs:
            try:
                self.after_cancel(j)
            except Exception:
                pass
        if hasattr(self, "logs"):
            self.logs.stop()

    def goto(self, key):
        self.nb.select(self.tabs[key])

    def on_connected(self):
        def work():
            code, out, _ = self.app.ssh.run("docker version --format '{{.Server.Version}}'",
                                            sudo=self.app.ssh.docker_sudo)
            return out.strip() if code == 0 else ""
        self.app.bg(work, lambda v: self.lbl_ver.config(
            text=f"Docker {v}" if v else "⚠ เข้าถึง docker ไม่ได้ — ลองติ๊ก 'ใช้ sudo docker'"))
        self.refresh()

    # ---------- helpers ----------
    def d(self, args, timeout=60):
        return self.app.ssh.run("docker " + args, timeout=timeout, sudo=self.app.ssh.docker_sudo)

    def say(self, text, tag="out"):
        self.logs.out.write(text, tag)

    def console(self, title, cmd, pty=False, wrap=None):
        return ConsoleWindow(self.app, title, cmd=cmd, wrap=wrap, sudo=self.app.ssh.docker_sudo, pty=pty)

    def refresh(self):
        if not self.app.ssh.connected():
            return
        key = self.order[self.nb.index(self.nb.select())]
        {"containers": self.load_containers, "images": self.load_images, "volumes": self.load_volumes,
         "networks": self.load_networks, "compose": self.load_compose, "stats": self.load_stats,
         "system": lambda: None}[key]()

    def act(self, args, label=None, after=None):
        def done(r):
            code, out, err = r
            self.say(f"$ docker {args}\n", "prompt")
            if out.strip():
                self.say(out if out.endswith("\n") else out + "\n", "out")
            if err.strip():
                self.say(err if err.endswith("\n") else err + "\n", "err" if code else "dim")
            ok = code == 0
            msg = ("✔ " if ok else "✘ ") + (label or args.split()[0])
            self.say(msg + "\n", "ok" if ok else "err")
            self.lbl_msg.config(text=msg, fg=Theme.GREEN if ok else Theme.RED)
            (after or self.refresh)()
        self.app.bg(lambda: self.d(args, timeout=180), done)

    def load_table(self, args, tree, rowfn, tagfn=None, keyfn=None, label=None, jsonlist=False):
        def done(r):
            code, out, err = r
            if code != 0:
                self.lbl_msg.config(text=(err or out).strip()[:90], fg=Theme.RED)
                return
            if jsonlist:
                try:
                    items = json.loads(out) if out.strip() else []
                except ValueError:
                    items = []
            else:
                items = jlines(out)
            rows = [rowfn(i) for i in items]
            fill_tree(tree, rows, keyfn or (lambda r: r[0]), tagfn)
            if label:
                label.config(text=f"{len(rows)} รายการ")
            self.lbl_msg.config(text="")
        self.app.bg(lambda: self.d(args), done)

    # ═══════════ Containers ═══════════
    def _tab_containers(self, f):
        toolbar(f, [("▶ Start", lambda: self.c_act("start")), ("⏹ Stop", lambda: self.c_act("stop", True)),
                    ("🔁 Restart", lambda: self.c_act("restart")), ("⏸ Pause", lambda: self.c_act("pause")),
                    ("⏯ Unpause", lambda: self.c_act("unpause")), ("☠ Kill", lambda: self.c_act("kill", True)),
                    ("🗑 Remove", lambda: self.c_act("rm", True)), ("✏ Rename", self.c_rename)])
        tb = toolbar(f, [("📋 Logs", self.c_logs), ("🗔 Logs (หน้าต่างแยก)", self.c_logs_win),
                         ("💻 Exec Console", self.c_exec), ("🔍 Inspect", self.c_inspect),
                         ("🧮 Top", self.c_top), ("📈 Port", self.c_port), None,
                         ("➕ Run Container", lambda: RunDialog(self.app))])
        self.v_cf = tk.StringVar()
        tk.Entry(tb, textvariable=self.v_cf, bg=Theme.INPUT, fg=Theme.FG, insertbackground=Theme.FG,
                 relief=tk.FLAT, width=20).pack(side=tk.RIGHT, ipady=3, padx=4)
        tk.Label(tb, text="ค้นหา:", fg=Theme.FG_DIM, bg=Theme.BG, font=Theme.FONT_SM).pack(side=tk.RIGHT)
        self.v_cf.trace_add("write", lambda *_: self._show_containers())
        pw = ttk.PanedWindow(f, orient=tk.VERTICAL)
        pw.pack(fill=tk.BOTH, expand=True, padx=4, pady=4)
        top = tk.Frame(pw, bg=Theme.BG)
        fr, self.tree_c = make_table(top, [
            ("name", "Name", 170, "w", False), ("image", "Image", 220, "w", False),
            ("status", "Status", 170, "w", False), ("ports", "Ports", 260, "w", True),
            ("id", "ID", 100, "w", False), ("created", "Created", 140, "w", False)],
            tags={"run": Theme.GREEN, "stop": Theme.RED, "pause": Theme.ORANGE, "other": Theme.FG_DIM})
        fr.pack(fill=tk.BOTH, expand=True)
        self.lbl_cc = tk.Label(top, text="—", fg=Theme.FG_DIM, bg=Theme.BG, font=Theme.FONT_SM, anchor="w")
        self.lbl_cc.pack(fill=tk.X)
        pw.add(top, weight=3)
        self.tree_c.bind("<Double-1>", lambda e: self.c_logs())
        self.tree_c.bind("<Button-3>", self._c_ctx)
        self.menu_c = popup_menu(self, [
            ("▶ Start", lambda: self.c_act("start")), ("⏹ Stop", lambda: self.c_act("stop", True)),
            ("🔁 Restart", lambda: self.c_act("restart")), ("⏸ Pause", lambda: self.c_act("pause")),
            ("⏯ Unpause", lambda: self.c_act("unpause")), ("☠ Kill", lambda: self.c_act("kill", True)),
            ("🗑 Remove", lambda: self.c_act("rm", True)), None,
            ("📋 Logs", self.c_logs), ("🗔 Logs (หน้าต่างแยก)", self.c_logs_win),
            ("💻 Exec Console", self.c_exec), ("🔍 Inspect", self.c_inspect), ("🧮 Top", self.c_top), None,
            ("✏ Rename", self.c_rename), ("📋 Copy Name", lambda: self._copy(0)), ("📋 Copy ID", lambda: self._copy(4))])

        bottom = tk.Frame(pw, bg=Theme.BG)
        pw.add(bottom, weight=2)
        lb = tk.Frame(bottom, bg=Theme.BG)
        lb.pack(fill=tk.X, pady=(2, 2))
        tk.Label(lb, text="📋 Live Logs / Output  Container:", fg=Theme.FG_DIM, bg=Theme.BG,
                 font=Theme.FONT_SM).pack(side=tk.LEFT)
        self.e_container = tk.Entry(lb, width=22, bg=Theme.INPUT, fg=Theme.FG, insertbackground=Theme.FG, relief=tk.FLAT)
        self.e_container.pack(side=tk.LEFT, padx=6, ipady=3)
        self.btn_stream = ModernButton(lb, text="▶ Stream", command=self.toggle_stream, bg=Theme.BLUE,
                                       fg="#16161e", hover="#89ddff", pady=3, font=Theme.FONT_SM)
        self.btn_stream.pack(side=tk.LEFT)
        tk.Label(lb, text="Tail", fg=Theme.FG_DIM, bg=Theme.BG, font=Theme.FONT_SM).pack(side=tk.LEFT, padx=(10, 2))
        self.v_tail = tk.StringVar(value="200")
        ttk.Combobox(lb, textvariable=self.v_tail, values=["50", "200", "500", "1000", "all"], width=6).pack(side=tk.LEFT)
        self.v_follow, self.v_ts = tk.BooleanVar(value=True), tk.BooleanVar(value=False)
        ttk.Checkbutton(lb, text="Follow", variable=self.v_follow).pack(side=tk.LEFT, padx=(8, 0))
        ttk.Checkbutton(lb, text="Timestamps", variable=self.v_ts).pack(side=tk.LEFT, padx=(8, 0))
        tk.Label(lb, text="Filter:", fg=Theme.FG_DIM, bg=Theme.BG, font=Theme.FONT_SM).pack(side=tk.LEFT, padx=(10, 2))
        self.v_lf = tk.StringVar()
        tk.Entry(lb, textvariable=self.v_lf, bg=Theme.INPUT, fg=Theme.FG, insertbackground=Theme.FG,
                 relief=tk.FLAT, width=18).pack(side=tk.LEFT, ipady=3)
        ModernButton(lb, text="🧹", command=lambda: self.logs.out.clear(), pady=2, padx=8,
                     font=Theme.FONT_SM).pack(side=tk.LEFT, padx=6)
        self.logs = StreamView(bottom, self.app, on_state=lambda r: self.btn_stream.config(
            text="⏹ Stop" if r else "▶ Stream"))
        self.logs.pack(fill=tk.BOTH, expand=True)
        self.v_lf.trace_add("write", lambda *_: self.logs.set_filter(self.v_lf.get()))

    def load_containers(self):
        fl = "-a " if self.v_all.get() else ""
        self.app.bg(lambda: self.d("ps " + fl + "--format '{{json .}}'"), self._got_containers)

    def _got_containers(self, r):
        code, out, err = r
        if code != 0:
            self.lbl_msg.config(text=(err or out).strip()[:90], fg=Theme.RED)
            return
        self.c_rows = [(i.get("Names", ""), i.get("Image", ""), i.get("Status", ""), i.get("Ports", ""),
                        i.get("ID", ""), (i.get("CreatedAt", "") or "")[:16]) for i in jlines(out)]
        self.lbl_msg.config(text="")
        self._show_containers()

    def _show_containers(self):
        f = self.v_cf.get().lower()
        rows = [r for r in self.c_rows if not f or f in " ".join(r).lower()]

        def tag(r):
            s = r[2]
            return ("pause" if "Paused" in s else "run" if s.startswith("Up") else
                    "stop" if s.startswith(("Exited", "Created", "Dead")) else "other")
        fill_tree(self.tree_c, rows, lambda r: r[4], tag)
        run = sum(1 for r in self.c_rows if r[2].startswith("Up"))
        self.lbl_cc.config(text=f"{len(self.c_rows)} containers • {run} running • {len(rows)} แสดงอยู่")

    def _c_ctx(self, e):
        iid = self.tree_c.identify_row(e.y)
        if iid:
            if iid not in self.tree_c.selection():
                self.tree_c.selection_set(iid)
            self.menu_c.tk_popup(e.x_root, e.y_root)

    def _copy(self, col):
        r = sel_rows(self.tree_c)
        if r:
            self.clipboard_clear()
            self.clipboard_append("\n".join(x[col] for x in r))

    def _names(self, need=True):
        n = [r[0] for r in sel_rows(self.tree_c)]
        if need and not n:
            messagebox.showwarning("Docker", "กรุณาเลือก Container ก่อน")
        return n

    def c_act(self, action, confirm=False):
        names = self._names()
        if not names:
            return
        if confirm and not messagebox.askyesno("ยืนยัน", f"{action} container:\n" + "\n".join(names)):
            return
        verb = "rm -f" if action == "rm" else action
        self.act(f"{verb} " + " ".join(q(n) for n in names), f"{action} {', '.join(names)}")

    def c_rename(self):
        n = self._names()
        if n:
            new = simpledialog.askstring("Rename", f"ชื่อใหม่ของ '{n[0]}':", initialvalue=n[0], parent=self)
            if new and new != n[0]:
                self.act(f"rename {q(n[0])} {q(new)}", f"rename → {new}")

    def toggle_stream(self):
        if self.logs.running:
            self.logs.stop()
            return
        n = self._names(need=False)
        name = n[0] if n else self.e_container.get().strip()
        if not name:
            messagebox.showwarning("Logs", "เลือก Container หรือพิมพ์ชื่อก่อน")
            return
        self.e_container.delete(0, tk.END)
        self.e_container.insert(0, name)
        self.logs.start(self._logs_cmd(name), sudo=self.app.ssh.docker_sudo, pty=True)

    def _logs_cmd(self, name):
        tail = self.v_tail.get().strip() or "200"
        return (f"docker logs --tail {tail if tail == 'all' or tail.isdigit() else '200'}"
                + (" -f" if self.v_follow.get() else "") + (" -t" if self.v_ts.get() else "") + f" {q(name)} 2>&1")

    def c_logs(self):
        n = self._names()
        if n:
            self.e_container.delete(0, tk.END)
            self.e_container.insert(0, n[0])
            self.logs.start(self._logs_cmd(n[0]), sudo=self.app.ssh.docker_sudo, pty=True)

    def c_logs_win(self):
        n = self._names()
        if n:
            self.console(f"Logs: {n[0]}", self._logs_cmd(n[0]), pty=True)

    def c_exec(self):
        n = self._names()
        if n:
            name = n[0]
            w = self.console(f"Exec: {name}", None, wrap=lambda c: f"docker exec {q(name)} sh -c {q(c)} 2>&1")
            w.view.out.write(f"เชื่อมต่อ container '{name}' — พิมพ์คำสั่งด้านล่าง (ทำงานแบบ non-interactive)\n", "info")

    def c_inspect(self):
        n = self._names()
        if n:
            self.console(f"Inspect: {n[0]}", f"docker inspect {q(n[0])}")

    def c_top(self):
        n = self._names()
        if n:
            self.console(f"Top: {n[0]}", f"docker top {q(n[0])}")

    def c_port(self):
        n = self._names()
        if n:
            self.console(f"Port: {n[0]}", f"docker port {q(n[0])}")

    # ═══════════ Images ═══════════
    def _tab_images(self, f):
        toolbar(f, [("⬇ Pull", self.i_pull), ("▶ Run", self.i_run), ("🏷 Tag", self.i_tag),
                    ("🔍 Inspect", lambda: self.i_show("inspect")), ("📜 History", lambda: self.i_show("history")),
                    None, ("🗑 Remove", lambda: self.i_rm(False)), ("🗑 Force", lambda: self.i_rm(True)), None,
                    ("🧹 Prune dangling", lambda: self.prune("image prune -f", "ลบ dangling images ทั้งหมด?")),
                    ("🧹 Prune unused (-a)", lambda: self.prune("image prune -a -f", "ลบ image ที่ไม่มี container ใช้ทั้งหมด?"))])
        fr, self.tree_i = make_table(f, [
            ("repo", "Repository", 280, "w", True), ("tag", "Tag", 120, "w", False), ("id", "Image ID", 120, "w", False),
            ("size", "Size", 100, "e", False), ("created", "Created", 150, "w", False)])
        fr.pack(fill=tk.BOTH, expand=True, padx=4, pady=4)
        self.lbl_ic = tk.Label(f, text="—", fg=Theme.FG_DIM, bg=Theme.BG, font=Theme.FONT_SM, anchor="w")
        self.lbl_ic.pack(fill=tk.X, padx=6)

    def load_images(self):
        self.load_table("images --format '{{json .}}'", self.tree_i,
                        lambda i: (i.get("Repository", ""), i.get("Tag", ""), i.get("ID", ""), i.get("Size", ""),
                                   i.get("CreatedSince", "")), keyfn=lambda r: r[2] + r[0] + r[1], label=self.lbl_ic)

    def _ref(self, r):
        return f"{r[0]}:{r[1]}" if r[0] != "<none>" and r[1] != "<none>" else r[2]

    def _refs(self):
        r = sel_rows(self.tree_i)
        if not r:
            messagebox.showwarning("Docker", "กรุณาเลือก Image ก่อน")
        return [self._ref(x) for x in r]

    def i_pull(self):
        img = simpledialog.askstring("Pull Image", "ชื่อ image (เช่น nginx:latest):", parent=self)
        if img and img.strip():
            self.console(f"Pull: {img}", f"docker pull {q(img.strip())}", pty=False)
            self._jobs.append(self.after(6000, self.refresh))

    def i_run(self):
        r = self._refs()
        if r:
            RunDialog(self.app, image=r[0])

    def i_tag(self):
        r = self._refs()
        if r:
            new = simpledialog.askstring("Tag", f"Tag ใหม่ของ '{r[0]}' (repo:tag):", parent=self)
            if new:
                self.act(f"tag {q(r[0])} {q(new.strip())}", f"tag {new}")

    def i_show(self, what):
        r = self._refs()
        if r:
            self.console(f"{what}: {r[0]}", f"docker image {what} {q(r[0])}")

    def i_rm(self, force):
        r = self._refs()
        if r and messagebox.askyesno("ยืนยัน", "ลบ image:\n" + "\n".join(r)):
            self.act("rmi " + ("-f " if force else "") + " ".join(q(x) for x in r), "rmi")

    def prune(self, args, msg):
        if messagebox.askyesno("ยืนยัน", msg):
            self.act(args, args.split(" -")[0])

    # ═══════════ Volumes ═══════════
    def _tab_volumes(self, f):
        toolbar(f, [("➕ Create", self.v_create), ("🔍 Inspect", self.v_inspect), ("🗑 Remove", self.v_rm), None,
                    ("🧹 Prune unused", lambda: self.prune("volume prune -f", "ลบ volume ที่ไม่มี container ใช้ทั้งหมด? (ข้อมูลหายถาวร)"))])
        fr, self.tree_v = make_table(f, [("name", "Volume Name", 420, "w", True), ("driver", "Driver", 120, "w", False),
                                         ("scope", "Scope", 100, "w", False)])
        fr.pack(fill=tk.BOTH, expand=True, padx=4, pady=4)
        self.lbl_vc = tk.Label(f, text="—", fg=Theme.FG_DIM, bg=Theme.BG, font=Theme.FONT_SM, anchor="w")
        self.lbl_vc.pack(fill=tk.X, padx=6)

    def load_volumes(self):
        self.load_table("volume ls --format '{{json .}}'", self.tree_v,
                        lambda i: (i.get("Name", ""), i.get("Driver", ""), i.get("Scope", "")), label=self.lbl_vc)

    def v_create(self):
        n = simpledialog.askstring("Create Volume", "ชื่อ volume:", parent=self)
        if n and n.strip():
            self.act(f"volume create {q(n.strip())}", f"create volume {n}")

    def v_inspect(self):
        r = sel_rows(self.tree_v)
        if r:
            self.console(f"Volume: {r[0][0]}", f"docker volume inspect {q(r[0][0])}")

    def v_rm(self):
        r = sel_rows(self.tree_v)
        if r and messagebox.askyesno("ยืนยัน", "ลบ volume:\n" + "\n".join(x[0] for x in r) + "\n\n(ข้อมูลหายถาวร)"):
            self.act("volume rm " + " ".join(q(x[0]) for x in r), "rm volume")

    # ═══════════ Networks ═══════════
    def _tab_networks(self, f):
        toolbar(f, [("➕ Create", self.n_create), ("🔍 Inspect", self.n_inspect), ("🗑 Remove", self.n_rm), None,
                    ("🔌 Connect", lambda: self.n_attach("connect")), ("⛓ Disconnect", lambda: self.n_attach("disconnect")), None,
                    ("🧹 Prune unused", lambda: self.prune("network prune -f", "ลบ network ที่ไม่ใช้ทั้งหมด?"))])
        fr, self.tree_n = make_table(f, [("name", "Network", 260, "w", True), ("id", "ID", 120, "w", False),
                                         ("driver", "Driver", 100, "w", False), ("scope", "Scope", 90, "w", False)])
        fr.pack(fill=tk.BOTH, expand=True, padx=4, pady=4)
        self.lbl_nc = tk.Label(f, text="—", fg=Theme.FG_DIM, bg=Theme.BG, font=Theme.FONT_SM, anchor="w")
        self.lbl_nc.pack(fill=tk.X, padx=6)

    def load_networks(self):
        self.load_table("network ls --format '{{json .}}'", self.tree_n,
                        lambda i: (i.get("Name", ""), i.get("ID", ""), i.get("Driver", ""), i.get("Scope", "")),
                        keyfn=lambda r: r[1], label=self.lbl_nc)

    def n_create(self):
        n = simpledialog.askstring("Create Network", "ชื่อ network:", parent=self)
        if n and n.strip():
            d = simpledialog.askstring("Driver", "Driver (bridge / overlay / macvlan):", initialvalue="bridge", parent=self)
            self.act(f"network create --driver {q((d or 'bridge').strip())} {q(n.strip())}", f"create network {n}")

    def n_inspect(self):
        r = sel_rows(self.tree_n)
        if r:
            self.console(f"Network: {r[0][0]}", f"docker network inspect {q(r[0][0])}")

    def n_rm(self):
        r = sel_rows(self.tree_n)
        if r and messagebox.askyesno("ยืนยัน", "ลบ network:\n" + "\n".join(x[0] for x in r)):
            self.act("network rm " + " ".join(q(x[0]) for x in r), "rm network")

    def n_attach(self, what):
        r = sel_rows(self.tree_n)
        if r:
            c = simpledialog.askstring(what.title(), f"ชื่อ container ที่จะ {what} กับ '{r[0][0]}':", parent=self)
            if c and c.strip():
                self.act(f"network {what} {q(r[0][0])} {q(c.strip())}", f"network {what}")

    # ═══════════ Compose ═══════════
    def _tab_compose(self, f):
        toolbar(f, [("▶ Up -d", lambda: self.comp("up -d")), ("⏹ Down", lambda: self.comp("down", True)),
                    ("⏹ Down -v", lambda: self.comp("down -v", True, "ลบ volumes ของโปรเจกต์ด้วย — ข้อมูลหายถาวร")),
                    ("🔁 Restart", lambda: self.comp("restart")), ("⏸ Stop", lambda: self.comp("stop")),
                    ("▶ Start", lambda: self.comp("start")), ("⬇ Pull", lambda: self.comp("pull")), None,
                    ("📋 PS", lambda: self.comp("ps -a")), ("📜 Logs", lambda: self.comp("logs -f --tail 200", pty=True)),
                    ("⚙ Config", lambda: self.comp("config")), None,
                    ("📂 Up จากไฟล์…", self.comp_up_file)])
        fr, self.tree_p = make_table(f, [("name", "Project", 200, "w", False), ("status", "Status", 180, "w", False),
                                         ("files", "Config Files", 520, "w", True)], selectmode="browse")
        fr.pack(fill=tk.BOTH, expand=True, padx=4, pady=4)
        self.lbl_pc = tk.Label(f, text="—", fg=Theme.FG_DIM, bg=Theme.BG, font=Theme.FONT_SM, anchor="w")
        self.lbl_pc.pack(fill=tk.X, padx=6)

    def load_compose(self):
        if self.app.compose_bin != "docker compose":
            self.lbl_pc.config(text="ต้องใช้ Docker Compose v2 (docker compose) เพื่อแสดงรายการโปรเจกต์")
            return
        self.load_table("compose ls -a --format json", self.tree_p,
                        lambda i: (i.get("Name", ""), i.get("Status", ""), i.get("ConfigFiles", "")),
                        label=self.lbl_pc, jsonlist=True)

    def comp(self, args, confirm=False, warn=""):
        r = sel_rows(self.tree_p)
        if not r:
            messagebox.showwarning("Compose", "กรุณาเลือกโปรเจกต์ก่อน")
            return
        name, _, files = r[0]
        if confirm and not messagebox.askyesno("ยืนยัน", f"docker compose {args} — โปรเจกต์ '{name}'\n{warn}"):
            return
        fl = " ".join(f"-f {q(p.strip())}" for p in files.split(",") if p.strip())
        pty = args.startswith("logs")
        self.console(f"Compose {args}: {name}", f"{self.app.compose_bin} -p {q(name)} {fl} {args}", pty=pty)
        if not pty:
            self._jobs.append(self.after(5000, self.refresh))

    def comp_up_file(self):
        p = simpledialog.askstring("Compose Up", "Path ของ docker-compose.yml บนเซิร์ฟเวอร์:",
                                   initialvalue=self.app.dep_dir.get().rstrip("/") + "/" + self.app.dep_fname.get(), parent=self)
        if p and p.strip():
            p = p.strip()
            self.console(f"Compose up: {p}", f"cd \"$(dirname {q(p)})\" 2>/dev/null; {self.app.compose_bin} -f {q(p)} up -d")
            self._jobs.append(self.after(6000, self.refresh))

    # ═══════════ Stats ═══════════
    def _tab_stats(self, f):
        tb = toolbar(f, [("🔄 Refresh", self.load_stats)])
        self.v_sauto = tk.BooleanVar(value=True)
        ttk.Checkbutton(tb, text="Live ทุก 4 วินาที", variable=self.v_sauto).pack(side=tk.LEFT, padx=12)
        fr, self.tree_st = make_table(f, [
            ("name", "Container", 180, "w", False), ("cpu", "CPU %", 80, "e", False), ("mem", "Memory", 180, "e", False),
            ("memp", "Mem %", 80, "e", False), ("net", "Net I/O", 160, "e", False), ("blk", "Block I/O", 160, "e", False),
            ("pids", "PIDs", 60, "e", False)])
        fr.pack(fill=tk.BOTH, expand=True, padx=4, pady=4)

    def load_stats(self):
        if self._stats_busy:
            return
        self._stats_busy = True

        def done(r):
            self._stats_busy = False
            if r[0] == 0:
                fill_tree(self.tree_st, [(i.get("Name", ""), i.get("CPUPerc", ""), i.get("MemUsage", ""),
                                          i.get("MemPerc", ""), i.get("NetIO", ""), i.get("BlockIO", ""), i.get("PIDs", ""))
                                         for i in jlines(r[1])], lambda r: r[0])
            else:
                self.lbl_msg.config(text=(r[2] or r[1]).strip()[:90], fg=Theme.RED)

        def fail(e):
            self._stats_busy = False
            self.app.show_error(e)
        self.app.bg(lambda: self.d("stats --no-stream --format '{{json .}}'", timeout=40), done, fail)

    def _stats_tick(self):
        try:
            if (self.v_sauto.get() and self.winfo_ismapped() and self.app.ssh.connected()
                    and self.order[self.nb.index(self.nb.select())] == "stats"):
                self.load_stats()
            self._jobs.append(self.after(4000, self._stats_tick))
        except tk.TclError:
            pass

    # ═══════════ System ═══════════
    def _tab_system(self, f):
        toolbar(f, [("ℹ Info", lambda: self.sys("info")), ("🏷 Version", lambda: self.sys("version")),
                    ("💽 Disk Usage", lambda: self.sys("system df -v")),
                    ("📡 Events (live)", lambda: self.console("Docker Events", "docker events", pty=True))])
        toolbar(f, [("🧹 Prune containers", lambda: self.sys_prune("container prune -f", "ลบ container ที่หยุดแล้วทั้งหมด?")),
                    ("🧹 Prune images", lambda: self.sys_prune("image prune -f", "ลบ dangling images?")),
                    ("🧹 Prune networks", lambda: self.sys_prune("network prune -f", "ลบ network ที่ไม่ใช้?")),
                    ("🧹 Build cache", lambda: self.sys_prune("builder prune -f", "ลบ build cache?")),
                    ("💣 System prune -a --volumes",
                     lambda: self.sys_prune("system prune -af --volumes", "⚠ ลบ container/image/network/volume ที่ไม่ใช้ทั้งหมด\nข้อมูลใน volume จะหายถาวร — ต้องการต่อหรือไม่?"))])
        toolbar(f, [("🩺 Daemon status", lambda: self.daemon("systemctl status docker --no-pager -l | head -30")),
                    ("🔁 Restart daemon", lambda: self.daemon("systemctl restart docker", True)),
                    ("▶ Start daemon", lambda: self.daemon("systemctl start docker")),
                    ("⏹ Stop daemon", lambda: self.daemon("systemctl stop docker", True))])
        self.sys_out = LogText(f)
        self.sys_out.pack(fill=tk.BOTH, expand=True, padx=4, pady=4)

    def _show_sys(self, title, r):
        self.sys_out.clear()
        self.sys_out.write(f"$ {title}\n", "prompt")
        self.sys_out.write((r[1] or "") + ("\n" if r[1] and not r[1].endswith("\n") else ""), "out")
        if r[2].strip():
            self.sys_out.write(r[2], "err" if r[0] else "dim")
        self.sys_out.write(f"\n[exit {r[0]}]\n", "ok" if r[0] == 0 else "err")

    def sys(self, args):
        self.sys_out.clear()
        self.sys_out.write("⏳ กำลังโหลด...\n", "dim")
        self.app.bg(lambda: self.d(args, timeout=90), lambda r: self._show_sys("docker " + args, r))

    def sys_prune(self, args, msg):
        if messagebox.askyesno("ยืนยัน", msg):
            self.app.bg(lambda: self.d(args, timeout=300), lambda r: (self._show_sys("docker " + args, r), self.refresh()))

    def daemon(self, cmd, confirm=False):
        if confirm and not messagebox.askyesno("ยืนยัน", f"รัน: sudo {cmd} ?\n(container ทั้งหมดอาจหยุดชั่วคราว)"):
            return
        self.app.bg(lambda: self.app.ssh.run(cmd, sudo=True, timeout=90), lambda r: self._show_sys("sudo " + cmd, r))


# ══════════════════════════ MAIN APP ══════════════════════════
class TermiusProApp:
    ORDER = ["terminal", "files", "docker", "monitor", "deploy"]
    CLASSES = {"terminal": TerminalPanel, "files": FilesPanel, "docker": DockerPanel,
               "monitor": MonitorPanel, "deploy": DeployPanel}
    WIN_SIZE = {"terminal": "1000x640", "files": "1000x680", "docker": "1200x760",
                "monitor": "1000x700", "deploy": "1150x780"}

    def __init__(self, root):
        self.root = root
        root.title("Termius Mini Pro — SSH · SFTP · Docker Commander")
        root.geometry("1400x860")
        root.minsize(1180, 700)
        root.configure(bg=Theme.BG)

        self.ssh = SSHManager()
        self._uiq = queue.Queue()
        self.profiles, self.panels = [], {}
        self.compose_bin, self.rtt, self._hb_busy, self.conn_label = "docker compose", None, False, ""

        # ---- Variables ----
        sv = tk.StringVar
        self.v_host, self.v_port, self.v_user = sv(value="192.168.1.x"), sv(value="22"), sv(value="geomanss")
        self.v_pass, self.v_sudo = sv(), sv()
        self.v_keypath, self.v_keypass = sv(), sv()
        self.auth_var = sv(value="Password")
        self.current_remote_path = sv(value="~")
        self.docker_sudo = tk.BooleanVar(value=False)
        self.dep_name, self.dep_hport, self.dep_cport = sv(value="chatroom"), sv(value="7890"), sv(value="5000")
        self.dep_image, self.dep_token = sv(value="python:3.10-slim"), sv(value="")
        self.dep_tunnel = tk.BooleanVar(value=True)
        self.dep_dir, self.dep_fname = sv(value="~/chatroom"), sv(value="docker-compose.yml")
        self.dep_env_text = self.dep_vol_text = ""

        self._read_state()
        self._setup_styles()
        self._build_menubar()
        self._build_layout()
        self._render_profiles()
        self._toggle_auth_fields()
        for v in (self.v_pass, self.v_sudo, self.docker_sudo):
            v.trace_add("write", self._sync_sudo)
        self._sync_sudo()
        self.dep_name.trace_add("write", self._sync_target_label)
        self.dep_hport.trace_add("write", self._sync_target_label)
        for v in (self.dep_dir, self.dep_fname, self.dep_name):
            v.trace_add("write", self._sync_target_path)

        self._pump()
        self._tick_clock()
        self.root.after(8000, self._heartbeat)
        self.panel("terminal").welcome()
        root.bind("<F5>", lambda e: self.refresh_current())
        root.bind("<Control-l>", lambda e: self.panel("terminal").clear())
        root.protocol("WM_DELETE_WINDOW", self._on_close)

    # ════════════════ THREAD-SAFE UI QUEUE ════════════════
    def ui(self, fn, *args):
        """เรียกจาก thread ใดก็ได้ → ไปรันบน main thread"""
        self._uiq.put((fn, args))

    def _pump(self):
        try:
            for _ in range(300):
                fn, args = self._uiq.get_nowait()
                try:
                    fn(*args)
                except tk.TclError:
                    pass
                except Exception as e:   # อย่าให้ callback เดียวพังทั้งแอป
                    print("UI callback error:", repr(e))
        except queue.Empty:
            pass
        self.root.after(20, self._pump)

    def bg(self, fn, done=None, err=None):
        """รัน fn ใน thread-pool แล้วส่งผลกลับ UI"""
        def w():
            try:
                r = fn()
            except Exception as e:
                self.ui(err or self.show_error, e)
                return
            if done:
                self.ui(done, r)
        self.ssh.pool.submit(w)

    def log(self, text, tag="out"):
        self.ui(self.panel("terminal").out.write, text, tag)

    def show_error(self, e):
        self.set_status("err", str(e)[:100])
        self.log(f"[Error] {e}\n", "err")

    # ════════════════ PANELS / WINDOWS ════════════════
    def panel(self, name):
        return self.panels[name]["panel"]

    def show(self, name):
        info = self.panels[name]
        if info["win"]:
            info["win"].deiconify()
            info["win"].lift()
        else:
            self.notebook.select(info["panel"])

    def run_in_terminal(self, cmd):
        self.show("terminal")
        self.panel("terminal").run(cmd)

    def toggle_window(self, name):
        self.dock(name) if self.panels[name]["win"] else self.popout(name)

    def popout(self, name):
        info = self.panels[name]
        if info["win"]:
            return info["win"].lift()
        snap = info["panel"].snapshot()
        self.notebook.forget(info["panel"])
        info["panel"].destroy()
        cls = self.CLASSES[name]
        win = tk.Toplevel(self.root)
        win.title(f"{cls.TITLE} — Termius Mini Pro")
        win.geometry(self.WIN_SIZE[name])
        win.configure(bg=Theme.BG)
        p = cls(win, self, windowed=True)
        p.pack(fill=tk.BOTH, expand=True)
        p.restore(snap)
        win.protocol("WM_DELETE_WINDOW", lambda n=name: self.dock(n))
        info.update(panel=p, win=win)
        if self.ssh.connected() and name != "terminal":
            p.on_connected()

    def dock(self, name):
        info = self.panels[name]
        if not info["win"]:
            return
        snap = info["panel"].snapshot()
        info["panel"].destroy()
        info["win"].destroy()
        cls = self.CLASSES[name]
        p = cls(self.notebook, self)
        idx = sum(1 for n in self.ORDER[:self.ORDER.index(name)] if self.panels[n]["win"] is None)
        if idx >= len(self.notebook.tabs()):
            self.notebook.add(p, text=f"  {cls.TITLE}  ")
        else:
            self.notebook.insert(idx, p, text=f"  {cls.TITLE}  ")
        p.restore(snap)
        info.update(panel=p, win=None)
        self.notebook.select(p)
        if self.ssh.connected() and name != "terminal":
            p.on_connected()

    def new_terminal_window(self):
        win = tk.Toplevel(self.root)
        win.title("Terminal — Termius Mini Pro")
        win.geometry("980x600")
        win.configure(bg=Theme.BG)
        p = TerminalPanel(win, self, popbtn=False)
        p.pack(fill=tk.BOTH, expand=True)
        p.cwd = self.panel("terminal").cwd
        p.lbl_cwd.config(text=p.cwd)
        p.welcome()

    def _tab_menu(self, e):
        try:
            idx = self.notebook.index(f"@{e.x},{e.y}")
        except tk.TclError:
            return
        docked = [n for n in self.ORDER if self.panels[n]["win"] is None]
        name = docked[idx]
        m = popup_menu(self.root, [(f"⧉ แยก '{self.CLASSES[name].TITLE}' เป็นหน้าต่างใหม่", lambda: self.popout(name))])
        m.tk_popup(e.x_root, e.y_root)

    def refresh_current(self):
        try:
            cur = self.notebook.select()
            for n in self.ORDER:
                if self.panels[n]["win"] is None and str(self.panels[n]["panel"]) == str(cur):
                    return self.panels[n]["panel"].refresh()
        except tk.TclError:
            pass

    # ════════════════ STYLES ════════════════
    def _setup_styles(self):
        style = ttk.Style()
        style.theme_use("clam")
        style.configure(".", background=Theme.BG, foreground=Theme.FG, font=Theme.FONT)
        style.configure("TNotebook", background=Theme.BG, borderwidth=0, tabmargins=(0, 0, 0, 0))
        style.configure("TNotebook.Tab", background=Theme.BG_DARK, foreground=Theme.FG_DIM,
                        padding=(16, 8), borderwidth=0, font=Theme.FONT)
        style.map("TNotebook.Tab", background=[("selected", Theme.PANEL)], foreground=[("selected", Theme.BLUE)])
        style.configure("Treeview", background=Theme.PANEL, foreground=Theme.FG,
                        fieldbackground=Theme.PANEL, rowheight=26, borderwidth=0)
        style.map("Treeview", background=[("selected", "#3b4a7d")], foreground=[("selected", "#ffffff")])
        style.configure("Treeview.Heading", background=Theme.PANEL_HI, foreground=Theme.FG,
                        relief="flat", font=Theme.FONT_BOLD, padding=6)
        style.map("Treeview.Heading", background=[("active", Theme.BORDER)])
        style.configure("TPanedwindow", background=Theme.BG, borderwidth=0)
        style.configure("Sash", sashthickness=5)
        style.configure("TEntry", fieldbackground=Theme.INPUT, foreground=Theme.FG, insertcolor=Theme.FG)
        style.configure("TCombobox", fieldbackground=Theme.INPUT, background=Theme.PANEL_HI,
                        foreground=Theme.FG, arrowcolor=Theme.FG, bordercolor=Theme.BORDER,
                        lightcolor=Theme.BORDER, darkcolor=Theme.BORDER)
        style.map("TCombobox", fieldbackground=[("readonly", Theme.INPUT)], foreground=[("readonly", Theme.FG)])
        style.configure("TCheckbutton", background=Theme.BG, foreground=Theme.FG)
        style.map("TCheckbutton", background=[("active", Theme.BG)])
        style.configure("TScrollbar", background=Theme.PANEL_HI, troughcolor=Theme.BG_DARK, borderwidth=0,
                        arrowcolor=Theme.FG_DIM)
        style.configure("transfer.Horizontal.TProgressbar", background=Theme.GREEN,
                        troughcolor=Theme.PANEL_HI, borderwidth=0, thickness=8)
        self.root.option_add("*TCombobox*Listbox.background", Theme.PANEL)
        self.root.option_add("*TCombobox*Listbox.foreground", Theme.FG)
        self.root.option_add("*TCombobox*Listbox.selectBackground", Theme.BLUE)
        self.root.option_add("*TCombobox*Listbox.selectForeground", "#16161e")

    # ════════════════ MENUBAR ════════════════
    def _mkmenu(self, parent):
        return tk.Menu(parent, tearoff=0, bg=Theme.PANEL, fg=Theme.FG, bd=0,
                       activebackground=Theme.PANEL_HI, activeforeground=Theme.BLUE)

    def _build_menubar(self):
        mb = tk.Menu(self.root, bg=Theme.BG_DARK, fg=Theme.FG, bd=0, relief=tk.FLAT,
                     activebackground=Theme.PANEL_HI, activeforeground=Theme.BLUE)
        m = self._mkmenu(mb)
        m.add_command(label="🔗 Connect Now", command=self.connect)
        m.add_command(label="⏻ Disconnect", command=self.disconnect)
        m.add_separator()
        m.add_command(label="💾 Save Connection Profile", command=self.profile_save)
        m.add_command(label="🗔 Terminal ใหม่ (หน้าต่างแยก)", command=self.new_terminal_window)
        m.add_separator()
        m.add_command(label="❌ Exit", command=self._on_close)
        mb.add_cascade(label="File", menu=m)

        m = self._mkmenu(mb)
        for n in self.ORDER:
            m.add_command(label=f"⧉ แยก / Dock กลับ: {self.CLASSES[n].TITLE}", command=lambda n=n: self.toggle_window(n))
        mb.add_cascade(label="🪟 View", menu=m)

        m = self._mkmenu(mb)
        m.add_command(label="📈 System Report", command=self.quick_system_info)
        for t, c in (("🌐 Open Ports", "sudo ss -tulpn | grep LISTEN | head -25"),
                     ("⚙️ Top CPU", "ps aux --sort=-%cpu | head -12"), ("💽 Disk Usage", "df -h")):
            m.add_command(label=t, command=lambda c=c: self.run_in_terminal(c))
        m.add_separator()
        m.add_command(label="🔄 Reboot Server", command=self.server_reboot)
        m.add_command(label="⏻ Power Off Server", command=self.server_shutdown)
        mb.add_cascade(label="🖥 Server", menu=m)

        m = self._mkmenu(mb)
        for t, k in (("📦 Containers", "containers"), ("🖼 Images", "images"), ("💽 Volumes", "volumes"),
                     ("🔗 Networks", "networks"), ("🧩 Compose", "compose"), ("📊 Stats", "stats"),
                     ("⚙️ System / Prune", "system")):
            m.add_command(label=t, command=lambda k=k: self.docker_goto(k))
        m.add_separator()
        m.add_command(label="➕ Run New Container", command=lambda: RunDialog(self))
        mb.add_cascade(label="🐳 Docker", menu=m)

        m = self._mkmenu(mb)
        m.add_command(label="⚙️ Open Deploy Center", command=lambda: self.show("deploy"))
        m.add_command(label="🚀 Deploy Now", command=lambda: self.panel("deploy").deploy_now())
        m.add_command(label="💾 Save Compose File Only", command=lambda: self.panel("deploy").save_only())
        mb.add_cascade(label="🚀 Deploy", menu=m)

        m = self._mkmenu(mb)
        m.add_command(label="ℹ About", command=self.show_about)
        mb.add_cascade(label="Help", menu=m)
        self.root.config(menu=mb)

    def docker_goto(self, key):
        self.show("docker")
        self.panel("docker").goto(key)

    # ════════════════ LAYOUT ════════════════
    def _build_layout(self):
        header = tk.Frame(self.root, bg=Theme.BG_DARK, height=54)
        header.pack(fill=tk.X)
        header.pack_propagate(False)
        tk.Frame(header, bg=Theme.BLUE, width=5).pack(side=tk.LEFT, fill=tk.Y)
        tk.Label(header, text="⚡ Termius Mini Pro", fg="white", bg=Theme.BG_DARK,
                 font=("Segoe UI", 14, "bold")).pack(side=tk.LEFT, padx=(14, 8))
        tk.Label(header, text="SSH · SFTP · Docker · Deploy Commander", fg=Theme.FG_DIM,
                 bg=Theme.BG_DARK, font=Theme.FONT_SM).pack(side=tk.LEFT, pady=(4, 0))
        tk.Label(header, text=f"v{APP_VER}", fg=Theme.BLUE, bg=Theme.PANEL_HI,
                 font=("Segoe UI", 9, "bold"), padx=8, pady=2).pack(side=tk.RIGHT, padx=14)
        self._build_statusbar()
        body = tk.Frame(self.root, bg=Theme.BG)
        body.pack(fill=tk.BOTH, expand=True)
        self.paned = ttk.PanedWindow(body, orient=tk.HORIZONTAL)
        self.paned.pack(fill=tk.BOTH, expand=True)
        self._build_sidebar()
        self.notebook = ttk.Notebook(self.paned)
        self.paned.add(self.notebook, weight=1)
        for n in self.ORDER:
            cls = self.CLASSES[n]
            p = cls(self.notebook, self)
            self.notebook.add(p, text=f"  {cls.TITLE}  ")
            self.panels[n] = {"panel": p, "win": None}
        self.notebook.bind("<Button-3>", self._tab_menu)

    def _section(self, parent, text):
        f = tk.Frame(parent, bg=Theme.BG_DARK)
        f.pack(fill=tk.X, pady=(12, 5))
        tk.Label(f, text=text, fg=Theme.FG_DIM, bg=Theme.BG_DARK, font=("Segoe UI", 9, "bold")).pack(anchor="w")
        tk.Frame(f, bg=Theme.BORDER, height=1).pack(fill=tk.X, pady=(4, 0))

    def _lab_entry(self, parent, label, var, show=None, width=None, pack=True):
        tk.Label(parent, text=label, fg=Theme.FG_DIM, bg=Theme.BG_DARK, font=Theme.FONT_SM).pack(anchor="w", pady=(5, 1))
        e = tk.Entry(parent, textvariable=var, bg=Theme.INPUT, fg=Theme.FG, insertbackground=Theme.FG,
                     relief=tk.FLAT, show=show or "", **({"width": width} if width else {}))
        e.pack(fill=tk.X, ipady=4)
        return e

    def _build_sidebar(self):
        sb = tk.Frame(self.paned, bg=Theme.BG_DARK, width=300)
        self.paned.add(sb, weight=0)
        inner = tk.Frame(sb, bg=Theme.BG_DARK)
        inner.pack(fill=tk.BOTH, expand=True, padx=12, pady=8)

        self._section(inner, "CONNECTION MANAGER")
        self.tree_profiles = ttk.Treeview(inner, height=3, show="tree", selectmode="browse")
        self.tree_profiles.pack(fill=tk.X, pady=(0, 4))
        self.tree_profiles.bind("<Double-1>", self.profile_load)
        pr = tk.Frame(inner, bg=Theme.BG_DARK)
        pr.pack(fill=tk.X)
        ModernButton(pr, text="💾 Save", command=self.profile_save, pady=3, font=Theme.FONT_SM).pack(
            side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 3))
        ModernButton(pr, text="🗑 Del", command=self.profile_delete, pady=3, font=Theme.FONT_SM).pack(
            side=tk.LEFT, fill=tk.X, expand=True)
        tk.Label(inner, text="ดับเบิลคลิกเพื่อโหลด Profile", fg=Theme.FG_DIM, bg=Theme.BG_DARK,
                 font=Theme.FONT_SM).pack(anchor="w", pady=(2, 2))

        row = tk.Frame(inner, bg=Theme.BG_DARK)
        row.pack(fill=tk.X)
        f1 = tk.Frame(row, bg=Theme.BG_DARK)
        f1.pack(side=tk.LEFT, fill=tk.X, expand=True)
        self._lab_entry(f1, "Host / IP", self.v_host)
        f2 = tk.Frame(row, bg=Theme.BG_DARK)
        f2.pack(side=tk.LEFT, padx=(8, 0))
        self._lab_entry(f2, "Port", self.v_port, width=6)
        self._lab_entry(inner, "Username", self.v_user)
        tk.Label(inner, text="Auth Method", fg=Theme.FG_DIM, bg=Theme.BG_DARK, font=Theme.FONT_SM).pack(anchor="w", pady=(5, 1))
        cb = ttk.Combobox(inner, textvariable=self.auth_var, values=["Password", "Private Key"], state="readonly")
        cb.pack(fill=tk.X, ipady=2)
        cb.bind("<<ComboboxSelected>>", self._toggle_auth_fields)
        self.frame_pass = tk.Frame(inner, bg=Theme.BG_DARK)
        self._lab_entry(self.frame_pass, "Password", self.v_pass, show="•")
        self.frame_key = tk.Frame(inner, bg=Theme.BG_DARK)
        tk.Label(self.frame_key, text="Private Key Path", fg=Theme.FG_DIM, bg=Theme.BG_DARK,
                 font=Theme.FONT_SM).pack(anchor="w", pady=(5, 1))
        kr = tk.Frame(self.frame_key, bg=Theme.BG_DARK)
        kr.pack(fill=tk.X)
        tk.Entry(kr, textvariable=self.v_keypath, bg=Theme.INPUT, fg=Theme.FG, insertbackground=Theme.FG,
                 relief=tk.FLAT).pack(side=tk.LEFT, fill=tk.X, expand=True, ipady=4)
        ModernButton(kr, text="…", command=self.browse_key, pady=1, padx=9).pack(side=tk.LEFT, padx=(4, 0))
        self._lab_entry(self.frame_key, "Key Passphrase (ถ้ามี)", self.v_keypass, show="•")
        self.frame_sudo = tk.Frame(inner, bg=Theme.BG_DARK)
        self.frame_sudo.pack(fill=tk.X)
        self._lab_entry(self.frame_sudo, "Sudo Password (ว่าง = ใช้รหัสผ่านล็อกอิน)", self.v_sudo, show="•")

        cr = tk.Frame(inner, bg=Theme.BG_DARK)
        cr.pack(fill=tk.X, pady=(10, 0))
        ModernButton(cr, text="🔗  Connect", command=self.connect, bg=Theme.GREEN, fg="#10131a",
                     hover="#b9f27a", font=Theme.FONT_BOLD).pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 4))
        ModernButton(cr, text="⏻", command=self.disconnect, hover=Theme.RED, font=Theme.FONT_BOLD,
                     padx=14).pack(side=tk.LEFT)

        self._section(inner, "QUICK TOOLS")
        grid = tk.Frame(inner, bg=Theme.BG_DARK)
        grid.pack(fill=tk.X)
        tools = [("🖥 Info", self.quick_system_info),
                 ("🌐 Ports", lambda: self.run_in_terminal("sudo ss -tulpn | grep LISTEN | head -25")),
                 ("⚙️ Procs", lambda: self.run_in_terminal("top -bn1 | head -22")),
                 ("💾 Disk", lambda: self.run_in_terminal("df -h")),
                 ("🧠 RAM", lambda: self.run_in_terminal("free -h")),
                 ("🐳 Docker", lambda: self.docker_goto("containers"))]
        for i, (t, c) in enumerate(tools):
            ModernButton(grid, text=t, command=c, pady=4, font=Theme.FONT_SM).grid(
                row=i // 2, column=i % 2, sticky="ew", padx=2, pady=2)
        grid.columnconfigure(0, weight=1)
        grid.columnconfigure(1, weight=1)

        self._section(inner, "DEPLOY TARGET")
        self.lbl_target = tk.Label(inner, text="", fg=Theme.CYAN, bg=Theme.BG_DARK, font=Theme.FONT_SM, anchor="w")
        self.lbl_target.pack(fill=tk.X)
        self.lbl_target_path = tk.Label(inner, text="", fg=Theme.FG_DIM, bg=Theme.BG_DARK, font=Theme.FONT_SM, anchor="w")
        self.lbl_target_path.pack(fill=tk.X)
        self._sync_target_label()
        self._sync_target_path()
        br = tk.Frame(inner, bg=Theme.BG_DARK)
        br.pack(fill=tk.X, pady=4)
        ModernButton(br, text="🚀 Deploy Now", command=lambda: self.panel("deploy").deploy_now(),
                     bg=Theme.GREEN, fg="#10131a", hover="#b9f27a", font=Theme.FONT_BOLD).pack(
            side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 3))
        ModernButton(br, text="⚙️", command=lambda: self.show("deploy"), pady=4, padx=11).pack(side=tk.LEFT)

    def _toggle_auth_fields(self, *_):
        if self.auth_var.get() == "Password":
            self.frame_key.pack_forget()
            self.frame_pass.pack(fill=tk.X, before=self.frame_sudo)
        else:
            self.frame_pass.pack_forget()
            self.frame_key.pack(fill=tk.X, before=self.frame_sudo)

    def browse_key(self):
        p = filedialog.askopenfilename(title="เลือกไฟล์ Private Key")
        if p:
            self.v_keypath.set(p)

    def _sync_target_label(self, *_):
        self.lbl_target.config(text=f"🎯 {self.dep_name.get() or 'app'} (:{self.dep_hport.get() or '80'})")

    def _sync_target_path(self, *_):
        self.lbl_target_path.config(text=f"📄 {self.remote_compose_path()}")

    def _sync_sudo(self, *_):
        self.ssh.sudo_pw = self.v_sudo.get() or self.v_pass.get()
        self.ssh.docker_sudo = bool(self.docker_sudo.get())

    # ════════════════ STATUSBAR ════════════════
    def _build_statusbar(self):
        sb = tk.Frame(self.root, bg=Theme.BG_DARK, height=32)
        sb.pack(fill=tk.X, side=tk.BOTTOM)
        sb.pack_propagate(False)
        self.status_dot = tk.Label(sb, text="●", fg=Theme.FG_DIM, bg=Theme.BG_DARK, font=("Segoe UI", 11, "bold"))
        self.status_dot.pack(side=tk.LEFT, padx=(12, 4))
        self.status_text = tk.Label(sb, text="ไม่ได้เชื่อมต่อ — กรอกข้อมูลแล้วกด Connect",
                                    fg=Theme.FG_DIM, bg=Theme.BG_DARK, font=Theme.FONT_SM)
        self.status_text.pack(side=tk.LEFT)
        self.pbar = ttk.Progressbar(sb, style="transfer.Horizontal.TProgressbar", length=220,
                                    mode="determinate", maximum=100)
        self.op_label = tk.Label(sb, text="Ready", fg=Theme.FG_DIM, bg=Theme.BG_DARK, font=Theme.FONT_SM)
        self.op_label.pack(side=tk.RIGHT, padx=12)
        self.lbl_clock = tk.Label(sb, text="", fg=Theme.FG_DIM, bg=Theme.BG_DARK, font=Theme.FONT_SM)
        self.lbl_clock.pack(side=tk.RIGHT)

    def _tick_clock(self):
        self.lbl_clock.config(text=datetime.datetime.now().strftime("🕑 %H:%M:%S"))
        self.root.after(1000, self._tick_clock)

    def set_status(self, kind, text):
        colors = {"ok": Theme.GREEN, "wait": Theme.ORANGE, "err": Theme.RED, "off": Theme.FG_DIM}
        self.status_dot.config(fg=colors.get(kind, Theme.FG_DIM))
        self.status_text.config(text=text)

    def progress(self, pct, text):
        if not self.pbar.winfo_ismapped():
            self.pbar.pack(side=tk.RIGHT, padx=8)
        self.pbar["value"] = pct
        self.op_label.config(text=text)

    def progress_done(self):
        self.pbar.pack_forget()
        self.op_label.config(text="Ready")

    def _status_connected(self):
        ms = f" • {self.rtt} ms" if self.rtt is not None else ""
        self.set_status("ok", f"เชื่อมต่อสำเร็จ: {self.conn_label}{ms}")

    # ════════════════ CONNECTION ════════════════
    def _load_pkey(self, path, password=None):
        classes = [paramiko.RSAKey, paramiko.ECDSAKey]
        if hasattr(paramiko, "Ed25519Key"):
            classes.insert(0, paramiko.Ed25519Key)
        errs = []
        for cls in classes:
            try:
                return cls.from_private_key_file(path, password=password or None)
            except paramiko.PasswordRequiredException:
                raise ValueError("Private Key ต้องการ Passphrase — กรุณากรอกช่อง Key Passphrase")
            except Exception as e:
                errs.append(str(e))
        raise ValueError("อ่าน Private Key ไม่สำเร็จ: " + "; ".join(errs[:2]))

    def conn_params(self):
        host, user = self.v_host.get().strip(), self.v_user.get().strip()
        try:
            port = int(self.v_port.get().strip() or "22")
        except ValueError:
            raise ValueError("Port ต้องเป็นตัวเลข")
        if not host or not user:
            raise ValueError("กรุณากรอก Host และ Username")
        params = dict(hostname=host, port=port, username=user, timeout=10, banner_timeout=15,
                      auth_timeout=15, allow_agent=False, look_for_keys=False)
        if self.auth_var.get() == "Private Key":
            kp = self.v_keypath.get().strip()
            if not kp or not os.path.exists(kp):
                raise ValueError("ไม่พบไฟล์ Private Key")
            params["pkey"] = self._load_pkey(kp, self.v_keypass.get())
        else:
            params["password"] = self.v_pass.get()
        return params

    def connect(self):
        try:
            params = self.conn_params()
        except Exception as e:
            self.set_status("err", str(e))
            messagebox.showerror("Connection", str(e))
            return
        self.ssh.params = params
        self._sync_sudo()
        self.conn_label = f"{params['username']}@{params['hostname']}:{params['port']}"
        self.set_status("wait", f"กำลังเชื่อมต่อ {params['hostname']} ...")

        def work():
            self.ssh.connect()
            uname = self.ssh.run("uname -a", timeout=15)[1].strip() or params["hostname"]
            bin_ = "docker compose"
            if self.ssh.run("docker compose version", timeout=15)[0] != 0:
                if self.ssh.run("docker-compose version", timeout=15)[0] == 0:
                    bin_ = "docker-compose"
            return uname, bin_

        def fail(e):
            msg = str(e)
            self.ssh.params = None
            self.set_status("err", f"เชื่อมต่อไม่สำเร็จ — {msg[:70]}")
            self.log(f"\n[FAIL] ❌ {msg}\n", "err")
            messagebox.showerror("Connection Failed", msg)
        self.bg(work, self._on_connected, fail)

    def _on_connected(self, res):
        uname, self.compose_bin = res
        self.rtt = None
        self._status_connected()
        self.log(f"\n[OK] 🔗 Connected → {self.conn_label}\n      {uname}\n      compose: {self.compose_bin}\n", "ok")
        self._persist()
        for n in self.ORDER:
            if n != "terminal":
                self.panels[n]["panel"].on_connected()

    def disconnect(self):
        self.ssh.close()
        self.rtt = None
        self.set_status("off", "ไม่ได้เชื่อมต่อ")
        self.log("\n[INFO] ⏻ ปิดการเชื่อมต่อแล้ว\n", "info")

    def _heartbeat(self):
        """ตรวจสุขภาพการเชื่อมต่อ + วัด latency + ต่อใหม่อัตโนมัติถ้าหลุด"""
        if self.ssh.params and not self._hb_busy:
            self._hb_busy = True

            def work():
                t0 = time.time()
                try:
                    self.ssh.run("true", timeout=12)
                    return int((time.time() - t0) * 1000)
                except Exception as e:
                    return e

            def done(r):
                self._hb_busy = False
                if isinstance(r, Exception):
                    self.set_status("err", f"การเชื่อมต่อขาด — ลองต่อใหม่อัตโนมัติ ({str(r)[:50]})")
                else:
                    self.rtt = r
                    self._status_connected()
            self.bg(work, done)
        self.root.after(8000, self._heartbeat)

    # ════════════════ PROFILES / STATE ════════════════
    def _render_profiles(self):
        self.tree_profiles.delete(*self.tree_profiles.get_children())
        for p in self.profiles:
            self.tree_profiles.insert("", tk.END, iid=p["name"],
                                      text=f"  🖧  {p['name']} — {p.get('user', '')}@{p.get('host', '')}")

    def profile_save(self):
        host = self.v_host.get().strip()
        if not host:
            messagebox.showwarning("Save Profile", "กรุณากรอก Host ก่อน")
            return
        name = simpledialog.askstring("Save Profile", "ตั้งชื่อ Connection Profile:", initialvalue=host, parent=self.root)
        if not name:
            return
        prof = {"name": name, "host": host, "port": self.v_port.get().strip() or "22",
                "user": self.v_user.get().strip(), "auth": self.auth_var.get(),
                "password": self.v_pass.get(), "key_path": self.v_keypath.get().strip(),
                "key_pass": self.v_keypass.get()}
        self.profiles = [p for p in self.profiles if p["name"] != name] + [prof]
        self._persist()
        self._render_profiles()

    def profile_delete(self):
        sel = self.tree_profiles.selection()
        if sel and messagebox.askyesno("ลบ Profile", f"ลบ profile '{sel[0]}' ?"):
            self.profiles = [p for p in self.profiles if p["name"] != sel[0]]
            self._persist()
            self._render_profiles()

    def profile_load(self, event=None):
        sel = self.tree_profiles.selection()
        p = next((x for x in self.profiles if sel and x["name"] == sel[0]), None)
        if not p:
            return
        self.v_host.set(p.get("host", ""))
        self.v_port.set(p.get("port", "22"))
        self.v_user.set(p.get("user", ""))
        self.auth_var.set(p.get("auth", "Password"))
        self._toggle_auth_fields()
        self.v_pass.set(p.get("password", ""))
        self.v_keypath.set(p.get("key_path", ""))
        self.v_keypass.set(p.get("key_pass", ""))
        self.set_status("off", f"โหลด Profile '{p['name']}' แล้ว — กด Connect เพื่อเชื่อมต่อ")

    def _read_state(self):
        data = {}
        if os.path.exists(PROFILES_FILE):
            try:
                with open(PROFILES_FILE, "r", encoding="utf-8") as f:
                    data = json.load(f)
            except Exception:
                data = {}
        self.profiles = data.get("profiles", [])
        last = data.get("last")
        if last:
            self.v_host.set(str(last.get("host", "")))
            self.v_port.set(str(last.get("port", "22")))
            self.v_user.set(str(last.get("user", "")))
            self.auth_var.set(last.get("auth", "Password"))
            self.v_pass.set(last.get("password", "") or "")
            self.v_keypath.set(last.get("key_path", "") or "")
            self.v_keypass.set(last.get("key_pass", "") or "")
            self.current_remote_path.set(last.get("remote_path", "~"))
            self.docker_sudo.set(bool(last.get("docker_sudo", False)))
        proj = data.get("project", {})
        if proj:
            self.dep_name.set(proj.get("name", "chatroom"))
            self.dep_hport.set(proj.get("hport", "7890"))
            self.dep_cport.set(proj.get("cport", "5000"))
            self.dep_image.set(proj.get("image", "python:3.10-slim"))
            self.dep_tunnel.set(proj.get("tunnel", True))
            self.dep_token.set(proj.get("token", ""))
            self.dep_dir.set(proj.get("dir", f"~/{self.dep_name.get()}"))
            self.dep_fname.set(proj.get("fname", "docker-compose.yml"))
            self.dep_env_text = proj.get("env", "")
            self.dep_vol_text = proj.get("volumes", "")

    def _persist(self):
        data = {"profiles": self.profiles,
                "last": {"host": self.v_host.get().strip(), "port": self.v_port.get().strip(),
                         "user": self.v_user.get().strip(), "auth": self.auth_var.get(),
                         "password": self.v_pass.get(), "key_path": self.v_keypath.get().strip(),
                         "key_pass": self.v_keypass.get(), "remote_path": self.current_remote_path.get(),
                         "docker_sudo": bool(self.docker_sudo.get())},
                "project": {"name": self.dep_name.get(), "hport": self.dep_hport.get(),
                            "cport": self.dep_cport.get(), "image": self.dep_image.get(),
                            "tunnel": self.dep_tunnel.get(), "token": self.dep_token.get(),
                            "dir": self.dep_dir.get(), "fname": self.dep_fname.get(),
                            "env": self.dep_env_text, "volumes": self.dep_vol_text}}
        try:
            with open(PROFILES_FILE, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception:
            pass

    # ════════════════ COMPOSE BUILDER ════════════════
    def remote_compose_path(self):
        d = self.dep_dir.get().strip().rstrip("/") or "~"
        f = self.dep_fname.get().strip() or "docker-compose.yml"
        if not f.endswith((".yml", ".yaml")):
            f += ".yml"
        return f"{d}/{f}"

    def build_compose(self):
        name = self.dep_name.get().strip() or "app"
        hp, cp = self.dep_hport.get().strip() or "8080", self.dep_cport.get().strip() or "5000"
        img = self.dep_image.get().strip() or "python:3.10-slim"
        envs = [l.strip() for l in self.dep_env_text.splitlines() if l.strip()]
        vols = [l.strip() for l in self.dep_vol_text.splitlines() if l.strip()]
        lines = ["services:", f"  {name}-app:", f"    image: {img}", f"    container_name: {name}-app",
                 "    restart: unless-stopped", "    ports:", f'      - "{hp}:{cp}"']
        if envs:
            lines += ["    environment:"] + [f"      - {e}" for e in envs]
        if vols:
            lines += ["    volumes:"] + [f"      - {v}" for v in vols]
        if self.dep_tunnel.get():
            lines += [f"  {name}-tunnel:", "    image: cloudflare/cloudflared:latest",
                      f"    container_name: {name}-tunnel", "    restart: unless-stopped"]
            token = self.dep_token.get().strip()
            if token:
                lines.append(f"    command: tunnel --no-autoupdate run --token {token}")
        return "\n".join(lines) + "\n"

    # ════════════════ MISC ════════════════
    def quick_system_info(self):
        self.show("monitor")
        self.panel("monitor").nb.select(0)
        self.panel("monitor").refresh()

    def server_reboot(self):
        if messagebox.askyesno("ยืนยัน", "รีบูตเซิร์ฟเวอร์เดี๋ยวนี้?"):
            self.run_in_terminal("sudo reboot")

    def server_shutdown(self):
        if messagebox.askyesno("ยืนยัน", "ปิดเครื่องเซิร์ฟเวอร์?"):
            self.run_in_terminal("sudo shutdown -h now")

    def show_about(self):
        messagebox.showinfo(f"About — Termius Mini Pro v{APP_VER}",
                            "⚡ Termius Mini Pro — SSH · SFTP · Docker · Deploy Commander\n\n"
                            "• แยกทุกแท็บเป็นหน้าต่างอิสระ (คลิกขวาที่แท็บ / ปุ่ม ⧉ / เมนู View)\n"
                            "• Terminal หลายหน้าต่าง, จำ working directory, Stop ฆ่าคำสั่งจริง\n"
                            "• SSH Engine: 1 connection หลาย channel, auto-reconnect, heartbeat\n"
                            "• SFTP Explorer + Editor + chmod + Progress\n"
                            "• Docker: Containers/Images/Volumes/Networks/Compose/Stats/System\n"
                            "• Monitor: Live CPU/RAM/Disk/Net, Processes, Services, System Logs\n"
                            "• Deploy Center — docker compose -f\n\n"
                            "⚠ รหัสผ่านถูกบันทึกเป็น plaintext ใน\n"
                            f"{PROFILES_FILE}\nโปรดใช้งานด้วยความระมัดระวัง")

    def _on_close(self):
        self._persist()
        self.ssh.close()
        self.ssh.pool.shutdown(wait=False, cancel_futures=True)
        self.root.destroy()


if __name__ == "__main__":
    root = tk.Tk()
    app = TermiusProApp(root)
    root.mainloop()