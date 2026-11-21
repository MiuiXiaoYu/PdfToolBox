# -*- coding: utf-8 -*-
"""PDF 工具箱

两个页签：
  1) 批量打印 —— 把多个 PDF 拖进来，调用 SumatraPDF 静默打印，不需要任何会员。
  2) 图片转 PDF —— 多张图片按顺序合并成一个 PDF，页面可选 A4 或原图尺寸。

打包：见同目录 build.bat，产出单文件 exe。
"""

import os
import re
import io
import sys
import time
import queue
import shutil
import tempfile
import threading
import subprocess
import ctypes
from ctypes import wintypes

import tkinter as tk
from tkinter import ttk, filedialog, messagebox

try:
    from tkinterdnd2 import TkinterDnD, DND_FILES
    HAS_DND = True
except Exception:
    HAS_DND = False

try:
    from PIL import Image
    HAS_PIL = True
except Exception:
    HAS_PIL = False

try:
    import img2pdf
    HAS_IMG2PDF = True
except Exception:
    img2pdf = None
    HAS_IMG2PDF = False

# 打包成窗口程序后没有标准输出，个别库会往里写日志
if sys.stdout is None:
    sys.stdout = io.StringIO()
if sys.stderr is None:
    sys.stderr = io.StringIO()

APP_TITLE = "PDF 工具箱"
APP_VERSION = "1.3"
CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)

IMG_EXTS = (".jpg", ".jpeg", ".png", ".bmp", ".tif", ".tiff", ".webp", ".gif")

PAGE_MODES = ["A4（自动横竖，等比留白）", "原图尺寸（按 300 DPI 计）"]
QUALITIES = {"高（95，体积大）": 95, "标准（88）": 88, "小体积（75）": 75}


# --------------------------------------------------------------------- 路径
def app_dir():
    if getattr(sys, "frozen", False):
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.abspath(__file__))


def resource_dir():
    return getattr(sys, "_MEIPASS", app_dir())


def load_config():
    """读取 config.ini 的 key=value 配置，返回 dict（键统一小写）。

    找不到 config.ini 时返回空 dict，不会报错。
    """
    cfg = {}
    for base in (resource_dir(), app_dir()):
        path = os.path.join(base, "config.ini")
        if os.path.isfile(path):
            try:
                with open(path, "r", encoding="utf-8-sig") as f:
                    for line in f:
                        line = line.strip()
                        if not line or line.startswith(("#", ";")):
                            continue
                        if "=" in line:
                            key, val = line.split("=", 1)
                            key = key.strip().lower()
                            val = val.strip().strip('"').strip("'")
                            if key and key not in cfg:
                                cfg[key] = val
            except OSError:
                pass
    return cfg


def find_sumatra():
    env = os.environ.get("SUMATRA_EXE")
    if env and os.path.isfile(env):
        return env

    conf = load_config().get("sumatra_path")
    if conf and os.path.isfile(conf):
        return conf

    bases = [resource_dir(), app_dir()]
    # 向后兼容旧版单行配置 SumatraPath.txt
    for base in bases:
        cfg = os.path.join(base, "SumatraPath.txt")
        if os.path.isfile(cfg):
            try:
                with open(cfg, "r", encoding="utf-8-sig") as f:
                    for line in f:
                        line = line.strip().strip('"')
                        if line and os.path.isfile(line):
                            return line
            except OSError:
                pass

    for base in bases:
        try:
            names = sorted(os.listdir(base))
        except OSError:
            continue
        for name in names:
            low = name.lower()
            if low.startswith("sumatrapdf") and low.endswith(".exe"):
                return os.path.join(base, name)

    local = os.environ.get("LOCALAPPDATA", "")
    known = [
        os.path.join(local, "SumatraPDF", "SumatraPDF.exe"),
        r"C:\Program Files\SumatraPDF\SumatraPDF.exe",
        r"C:\Program Files (x86)\SumatraPDF\SumatraPDF.exe",
    ]
    for path in known:
        if path and os.path.isfile(path):
            return path

    hit = shutil.which("SumatraPDF")
    return hit or None


# ------------------------------------------------------------------ 打印机
class PRINTER_INFO_4(ctypes.Structure):
    _fields_ = [
        ("pPrinterName", wintypes.LPWSTR),
        ("pServerName", wintypes.LPWSTR),
        ("Attributes", wintypes.DWORD),
    ]


def _list_printers_ps():
    try:
        out = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "Get-CimInstance Win32_Printer | ForEach-Object { $_.Name }"],
            capture_output=True, text=True, timeout=20,
            creationflags=CREATE_NO_WINDOW,
        )
        return [line.strip() for line in out.stdout.splitlines() if line.strip()]
    except Exception:
        return []


def list_printers():
    names = []
    try:
        winspool = ctypes.WinDLL("winspool.drv", use_last_error=True)
        flags = 0x02 | 0x04
        needed = wintypes.DWORD(0)
        returned = wintypes.DWORD(0)
        winspool.EnumPrintersW(flags, None, 4, None, 0,
                               ctypes.byref(needed), ctypes.byref(returned))
        if needed.value:
            buf = ctypes.create_string_buffer(needed.value)
            ok = winspool.EnumPrintersW(flags, None, 4, buf, needed.value,
                                        ctypes.byref(needed), ctypes.byref(returned))
            if ok and returned.value:
                arr = ctypes.cast(buf, ctypes.POINTER(PRINTER_INFO_4))
                for i in range(returned.value):
                    if arr[i].pPrinterName:
                        names.append(arr[i].pPrinterName)
    except Exception:
        names = []

    if not names:
        names = _list_printers_ps()
    return sorted(set(names), key=str.lower)


def default_printer():
    try:
        winspool = ctypes.WinDLL("winspool.drv", use_last_error=True)
        size = wintypes.DWORD(0)
        winspool.GetDefaultPrinterW(None, ctypes.byref(size))
        if size.value:
            buf = ctypes.create_unicode_buffer(size.value)
            if winspool.GetDefaultPrinterW(buf, ctypes.byref(size)):
                return buf.value
    except Exception:
        pass
    return ""


# ---------------------------------------------------------------- 打印引擎
def build_settings(page_range, copies, duplex):
    items = []
    rng = (page_range or "").strip()
    if rng:
        items.append(rng)
    if copies and copies > 0:
        items.append("%dx" % copies)
    if duplex:
        items.append("duplex")
    return ",".join(items)


FAIL_REASONS = {
    1: "打印失败（文件、打印机、驱动或权限）",
    2: "文件无法打开",
    3: "文档禁止打印",
    4: "打印机不存在",
    5: "打印机驱动异常",
    6: "被策略禁止打印",
}


def print_one(sumatra, printer, settings, path, timeout=180):
    args = [sumatra, "-print-to", printer, "-silent"]
    if settings:
        args += ["-print-settings", settings]
    args.append(path)
    try:
        proc = subprocess.run(args, timeout=timeout, creationflags=CREATE_NO_WINDOW)
    except subprocess.TimeoutExpired:
        return False, "超时（%d 秒）" % timeout
    except OSError as exc:
        return False, "调用失败：%s" % exc
    if proc.returncode == 0:
        return True, "已发送到打印机"
    return False, FAIL_REASONS.get(proc.returncode, "退出码 %d" % proc.returncode)


# ------------------------------------------------------------ 图片转 PDF 引擎
def natural_key(text):
    return [int(part) if part.isdigit() else part.lower()
            for part in re.split(r"(\d+)", text)]


def _flatten(im):
    """把带透明通道的图铺到白底上，PDF 只接受 RGB。"""
    if im.mode in ("RGBA", "LA") or (im.mode == "P" and "transparency" in im.info):
        im = im.convert("RGBA")
        bg = Image.new("RGB", im.size, (255, 255, 255))
        bg.paste(im, mask=im.split()[-1])
        return bg
    return im.convert("RGB")


def prepare_page(src, page_mode, quality, out_dpi, tmpdir, idx):
    """把一张图片处理成一页，落盘为 JPEG，返回文件路径。"""
    im = Image.open(src)
    im.load()
    im = _flatten(im)
    w, h = im.size

    if page_mode.startswith("A4"):
        landscape = w > h
        pw = int(round((297 if landscape else 210) / 25.4 * out_dpi))
        ph = int(round((210 if landscape else 297) / 25.4 * out_dpi))
        scale = min(pw / float(w), ph / float(h))
        nw = max(1, int(round(w * scale)))
        nh = max(1, int(round(h * scale)))
        scaled = im.resize((nw, nh), Image.LANCZOS)
        page = Image.new("RGB", (pw, ph), (255, 255, 255))
        page.paste(scaled, ((pw - nw) // 2, (ph - nh) // 2))
        scaled.close()
        im.close()
        im = page
    else:
        limit = 6000
        if max(w, h) > limit:
            factor = limit / float(max(w, h))
            resized = im.resize((max(1, int(w * factor)), max(1, int(h * factor))),
                                Image.LANCZOS)
            im.close()
            im = resized

    out = os.path.join(tmpdir, "p%05d.jpg" % idx)
    im.save(out, "JPEG", quality=quality, optimize=True, dpi=(out_dpi, out_dpi))
    im.close()
    return out


def images_to_pdf(files, out_pdf, page_mode, quality, out_dpi=300,
                  on_step=None, on_log=None, stop=None):
    """多张图片合并成一个 PDF。返回 (成功, 说明或路径)。"""
    if not HAS_PIL:
        return False, "缺少 Pillow，无法转换（pip install pillow）"

    tmpdir = tempfile.mkdtemp(prefix="img2pdf_")
    pages = []
    try:
        total = len(files)
        for i, path in enumerate(files, 1):
            if stop is not None and stop.is_set():
                return False, "已取消"
            try:
                pages.append(prepare_page(path, page_mode, quality, out_dpi, tmpdir, i))
            except Exception as exc:
                if on_log:
                    on_log("跳过 %s —— %s" % (os.path.basename(path), exc))
                continue
            if on_step:
                on_step(i, total)
            if on_log:
                on_log("[%d/%d] %s" % (i, total, os.path.basename(path)))

        if not pages:
            return False, "没有可用的图片"

        imgs = [Image.open(p) for p in pages]
        try:
            imgs[0].save(out_pdf, "PDF", resolution=out_dpi,
                         save_all=True, append_images=imgs[1:])
        finally:
            for im in imgs:
                im.close()
        return True, out_pdf
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


# --------------------------------------------------- 图片转 PDF（无损通道）
LOSSLESS_PASSTHROUGH = (".jpg", ".jpeg", ".png")


def _lossless_input(path, tmpdir, idx):
    """无损模式下，JPEG/PNG 原样交给 img2pdf；其它格式先存成 PNG（无损）。"""
    ext = os.path.splitext(path)[1].lower()
    if ext in LOSSLESS_PASSTHROUGH:
        with Image.open(path) as probe:
            probe.size  # 读文件头，顺便确认能打开
        return path

    im = Image.open(path)
    im.load()
    if im.mode not in ("RGB", "RGBA", "L", "LA"):
        keep_alpha = im.mode == "P" and "transparency" in im.info
        im = im.convert("RGBA" if keep_alpha else "RGB")
    out = os.path.join(tmpdir, "p%05d.png" % idx)
    im.save(out, "PNG")
    im.close()
    return out


def images_to_pdf_lossless(files, out_pdf, page_mode, out_dpi=300,
                           on_step=None, on_log=None, stop=None):
    """无损合并：JPEG 原样嵌入，PNG 用 Flate 压缩，都不重新编码。"""
    if not HAS_IMG2PDF:
        return False, "缺少 img2pdf，无法无损转换（pip install img2pdf）"

    tmpdir = tempfile.mkdtemp(prefix="img2pdf_l_")
    try:
        prepared = []
        total = len(files)
        for i, path in enumerate(files, 1):
            if stop is not None and stop.is_set():
                return False, "已取消"
            try:
                prepared.append(_lossless_input(path, tmpdir, i))
            except Exception as exc:
                if on_log:
                    on_log("跳过 %s —— %s" % (os.path.basename(path), exc))
                continue
            if on_step:
                on_step(i, total)
            if on_log:
                on_log("[%d/%d] %s（无损）" % (i, total, os.path.basename(path)))

        if not prepared:
            return False, "没有可用的图片"

        if page_mode.startswith("A4"):
            layout = img2pdf.get_layout_fun(
                pagesize=img2pdf.parse_pagesize_rectarg("A4"),
                fit=img2pdf.FitMode.into, auto_orient=True)
        else:
            layout = img2pdf.get_fixed_dpi_layout_fun((out_dpi, out_dpi))

        data = img2pdf.convert(prepared, layout_fun=layout)
        with open(out_pdf, "wb") as f:
            f.write(data)
        return True, out_pdf
    except Exception as exc:
        return False, "无损合成失败：%s" % exc
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def images_to_pdfs(files, out_dir, page_mode, quality, out_dpi=300, lossless=False,
                   on_step=None, on_log=None, stop=None):
    """每张图导出一个独立 PDF，放在 out_dir 里。返回 (成功数, 失败列表, 总数)。"""
    if not HAS_PIL:
        return 0, [(p, "缺少 Pillow") for p in files], len(files)

    total = len(files)
    ok = 0
    failed = []
    used = {}
    for i, path in enumerate(files, 1):
        if stop is not None and stop.is_set():
            break

        stem = os.path.splitext(os.path.basename(path))[0]
        name = stem + ".pdf"
        key = name.lower()
        if key in used:                      # a.jpg / a.png 撞名时加后缀
            used[key] += 1
            name = "%s_%d.pdf" % (stem, used[key])
        else:
            used[key] = 1
        target = os.path.join(out_dir, name)

        if lossless:
            good, msg = images_to_pdf_lossless([path], target, page_mode, out_dpi)
        else:
            good, msg = images_to_pdf([path], target, page_mode, quality, out_dpi=out_dpi)

        if good:
            ok += 1
            if on_log:
                on_log("[%d/%d] %s → %s" % (i, total, os.path.basename(path), name))
        else:
            failed.append((path, msg))
            if on_log:
                on_log("[%d/%d] %s → 失败：%s" % (i, total, os.path.basename(path), msg))
        if on_step:
            on_step(i, total)
    return ok, failed, total


# -------------------------------------------------------------------- 界面
class TabBase(ttk.Frame):
    """两个页签共用的日志 / 进度 / 后台线程骨架。"""

    def __init__(self, master):
        super().__init__(master, padding=(12, 10))
        self.msg_queue = queue.Queue()
        self.worker = None
        self.stop_flag = threading.Event()
        self._pending_logs = []
        self._build_body()
        self._build_footer()
        for text in self._pending_logs:
            self.log(text)
        self._pending_logs = []
        self.after(100, self._drain)

    def _build_body(self):
        raise NotImplementedError

    def _build_footer(self):
        self.progress = ttk.Progressbar(self, mode="determinate", maximum=100)
        self.progress.pack(fill="x", pady=(8, 4))
        self.status_var = tk.StringVar(value="就绪")
        ttk.Label(self, textvariable=self.status_var,
                  style="Hint.TLabel").pack(anchor="w")
        wrap = ttk.Frame(self)
        wrap.pack(fill="both", expand=True, pady=(4, 0))
        self.log_text = tk.Text(wrap, height=6, wrap="word", state="disabled",
                                font=("Consolas", 9), bg="#fbfbfc",
                                relief="solid", bd=1)
        bar = ttk.Scrollbar(wrap, orient="vertical", command=self.log_text.yview)
        self.log_text.configure(yscrollcommand=bar.set)
        self.log_text.pack(side="left", fill="both", expand=True)
        bar.pack(side="right", fill="y")

    # --- 通用工具 ---
    def log(self, text):
        if not hasattr(self, "log_text"):
            self._pending_logs.append(text)
            return
        self.log_text.configure(state="normal")
        self.log_text.insert("end", "[%s] %s\n" % (time.strftime("%H:%M:%S"), text))
        self.log_text.see("end")
        self.log_text.configure(state="disabled")

    def post(self, kind, payload=None):
        self.msg_queue.put((kind, payload))

    def _drain(self):
        try:
            while True:
                kind, payload = self.msg_queue.get_nowait()
                if kind == "log":
                    self.log(payload)
                elif kind == "status":
                    self.status_var.set(payload)
                elif kind == "progress":
                    self.progress["value"] = payload
                elif kind == "done":
                    self.on_done(payload)
        except queue.Empty:
            pass
        self.after(100, self._drain)

    def on_done(self, payload):
        pass

    def set_busy(self, busy):
        self.stop_btn.configure(state="normal" if busy else "disabled")
        self.start_btn.configure(state="disabled" if busy else "normal")

    def _make_drop(self, parent, text, on_drop):
        label = tk.Label(parent, text=text, bg="#f4f6f9", fg="#5a6472",
                         relief="solid", bd=1, height=3,
                         font=("Microsoft YaHei UI", 10), cursor="hand2")
        label.pack(fill="x", pady=(0, 8))
        if HAS_DND:
            label.drop_target_register(DND_FILES)
            label.dnd_bind("<<Drop>>", on_drop)
        return label


class PrintTab(TabBase):
    def _build_body(self):
        self.files = []
        self.sumatra = find_sumatra()

        ttk.Label(self, text="把 PDF 拖进下面的区域，选好打印机后点击开始打印",
                  font=("Microsoft YaHei UI", 10, "bold")).pack(anchor="w", pady=(0, 8))
        self.drop_area = self._make_drop(
            self, "拖放 PDF 到这里（也可以拖入文件夹）", self._on_drop)
        self.drop_area.bind("<Button-1>", lambda e: self.add_files())

        bar = ttk.Frame(self)
        bar.pack(fill="x", pady=(0, 4))
        ttk.Label(bar, text="待打印文件").pack(side="left")
        self.count_label = ttk.Label(bar, text="0 个", style="Hint.TLabel")
        self.count_label.pack(side="left", padx=(8, 0))
        ttk.Button(bar, text="清空", width=6, command=self.clear_files).pack(side="right")
        ttk.Button(bar, text="移除选中", width=9,
                   command=self.remove_selected).pack(side="right", padx=4)
        ttk.Button(bar, text="添加文件夹", command=self.add_folder).pack(side="right", padx=4)
        ttk.Button(bar, text="添加文件", command=self.add_files).pack(side="right")

        box = ttk.Frame(self)
        box.pack(fill="both", expand=True)
        self.listbox = tk.Listbox(box, height=6, activestyle="none",
                                  font=("Microsoft YaHei UI", 9), selectmode="extended")
        scroll = ttk.Scrollbar(box, orient="vertical", command=self.listbox.yview)
        self.listbox.configure(yscrollcommand=scroll.set)
        self.listbox.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")

        opt = ttk.LabelFrame(self, text="打印设置")
        opt.pack(fill="x", pady=8)

        row1 = ttk.Frame(opt)
        row1.pack(fill="x", padx=10, pady=(8, 4))
        ttk.Label(row1, text="打印机").pack(side="left")
        self.printer_var = tk.StringVar()
        self.printer_box = ttk.Combobox(row1, textvariable=self.printer_var, width=40)
        self.printer_box.pack(side="left", padx=(8, 6))
        ttk.Button(row1, text="刷新", width=6,
                   command=self.refresh_printers).pack(side="left")

        row2 = ttk.Frame(opt)
        row2.pack(fill="x", padx=10, pady=(0, 10))
        ttk.Label(row2, text="份数").pack(side="left")
        self.copies_var = tk.StringVar(value="1")
        ttk.Spinbox(row2, from_=1, to=99, width=5,
                    textvariable=self.copies_var).pack(side="left", padx=(6, 18))
        ttk.Label(row2, text="页范围").pack(side="left")
        self.range_var = tk.StringVar(value="")
        ttk.Entry(row2, width=14, textvariable=self.range_var).pack(side="left", padx=(6, 0))
        ttk.Label(row2, text="如 1-3,5（留空=全部）",
                  style="Hint.TLabel").pack(side="left", padx=(6, 18))
        self.duplex_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(row2, text="双面", variable=self.duplex_var).pack(side="left")

        act = ttk.Frame(self)
        act.pack(fill="x", pady=(4, 0))
        self.start_btn = ttk.Button(act, text="开始打印", style="Accent.TButton",
                                    command=self.start_print)
        self.start_btn.pack(side="left")
        self.stop_btn = ttk.Button(act, text="停止", state="disabled",
                                   command=lambda: self.stop_flag.set())
        self.stop_btn.pack(side="left", padx=8)

        self.refresh_printers()
        if self.sumatra:
            self.log("打印引擎：%s" % self.sumatra)
        else:
            self.log("未找到 SumatraPDF，打印功能不可用。请把 SumatraPDF.exe 放到程序目录，"
                     "或在程序目录新建 config.ini 写入 sumatra_path=<完整路径>（参考 config.example）。")

    # --- 文件 ---
    def _on_drop(self, event):
        try:
            paths = self.tk.splitlist(event.data)
        except Exception:
            paths = [event.data]
        self.add_paths(paths)

    def add_paths(self, paths):
        added = 0
        for path in paths:
            if os.path.isdir(path):
                for name in sorted(os.listdir(path)):
                    full = os.path.join(path, name)
                    if os.path.isfile(full) and name.lower().endswith(".pdf"):
                        added += 1 if self._append(full) else 0
            elif os.path.isfile(path) and path.lower().endswith(".pdf"):
                added += 1 if self._append(path) else 0
        self.refresh_list()
        if added:
            self.log("已添加 %d 个文件，共 %d 个待打印。" % (added, len(self.files)))

    def _append(self, path):
        full = os.path.abspath(path)
        if full in self.files:
            return False
        self.files.append(full)
        return True

    def add_files(self):
        paths = filedialog.askopenfilenames(
            title="选择 PDF 文件",
            filetypes=[("PDF 文件", "*.pdf"), ("所有文件", "*.*")])
        if paths:
            self.add_paths(list(paths))

    def add_folder(self):
        folder = filedialog.askdirectory(title="选择一个文件夹")
        if folder:
            self.add_paths([folder])

    def remove_selected(self):
        for idx in sorted(self.listbox.curselection(), reverse=True):
            del self.files[idx]
        self.refresh_list()

    def clear_files(self):
        if self.files and not messagebox.askyesno(
                "确认", "清空全部 %d 个文件？" % len(self.files)):
            return
        self.files = []
        self.refresh_list()

    def refresh_list(self):
        self.listbox.delete(0, "end")
        for i, path in enumerate(self.files, 1):
            self.listbox.insert("end", "%d. %s" % (i, os.path.basename(path)))
        self.count_label.configure(text="%d 个" % len(self.files))

    # --- 打印机 ---
    def refresh_printers(self):
        names = list_printers()
        self.printer_box["values"] = names
        current = self.printer_var.get().strip()
        if current and current in names:
            return
        if names:
            dflt = default_printer()
            self.printer_var.set(dflt if dflt in names else names[0])
            if any(k in (dflt or "").upper() for k in ("PDF", "ONENOTE", "XPS", "FAX")):
                self.log("提示：默认打印机是虚拟打印机，每个文件都会弹另存为对话框。")
        else:
            self.log("没有枚举到打印机，可以手动在输入框里填写打印机名称。")

    # --- 打印 ---
    def start_print(self):
        if self.worker and self.worker.is_alive():
            return
        if not self.sumatra:
            messagebox.showerror("缺少打印引擎",
                                 "未找到 SumatraPDF.exe。\n\n把它放到程序目录，"
                                 "或在程序目录新建 config.ini 写入 sumatra_path=<完整路径>（参考 config.example）。")
            return
        if not self.files:
            messagebox.showinfo("提示", "请先添加要打印的 PDF 文件。")
            return
        printer = self.printer_var.get().strip()
        if not printer:
            messagebox.showinfo("提示", "请选择打印机。")
            return
        try:
            copies = int(self.copies_var.get().strip() or "1")
            if copies < 1:
                raise ValueError
        except ValueError:
            messagebox.showinfo("提示", "份数必须是大于 0 的整数。")
            return

        settings = build_settings(self.range_var.get(), copies, self.duplex_var.get())
        files = list(self.files)

        self.stop_flag.clear()
        self.set_busy(True)
        self.progress["value"] = 0
        self.log("开始打印 %d 个文件 → %s" % (len(files), printer))
        if settings:
            self.log("打印参数：%s" % settings)

        self.worker = threading.Thread(
            target=self._worker, args=(files, printer, settings), daemon=True)
        self.worker.start()

    def _worker(self, files, printer, settings):
        ok = 0
        failed = []
        total = len(files)
        for i, path in enumerate(files, 1):
            if self.stop_flag.is_set():
                self.post("log", "已停止，剩余 %d 个未打印。" % (total - i + 1))
                break
            self.post("status", "正在打印 %d/%d：%s" % (i, total, os.path.basename(path)))
            success, msg = print_one(self.sumatra, printer, settings, path)
            if success:
                ok += 1
            else:
                failed.append((path, msg))
            self.post("log", "[%d/%d] %s → %s" % (i, total, os.path.basename(path), msg))
            self.post("progress", int(i * 100 / total))
        self.post("done", {"ok": ok, "failed": failed, "total": total})

    def on_done(self, result):
        self.set_busy(False)
        self.status_var.set("完成：成功 %d / 共 %d" % (result["ok"], result["total"]))
        if result["failed"]:
            self.log("失败 %d 个：" % len(result["failed"]))
            for path, msg in result["failed"]:
                self.log("    %s —— %s" % (os.path.basename(path), msg))
            messagebox.showwarning("部分失败", "成功 %d 个，失败 %d 个。详情见日志。"
                                   % (result["ok"], len(result["failed"])))
        else:
            self.log("全部完成。")


class ImageTab(TabBase):
    def _build_body(self):
        self.files = []
        self.last_output = ""

        ttk.Label(self, text="把图片拖进下面的区域，按顺序合并成一个 PDF",
                  font=("Microsoft YaHei UI", 10, "bold")).pack(anchor="w", pady=(0, 8))
        self.drop_area = self._make_drop(
            self, "拖放图片到这里（jpg / png / bmp / tif / webp）", self._on_drop)
        self.drop_area.bind("<Button-1>", lambda e: self.add_files())

        bar = ttk.Frame(self)
        bar.pack(fill="x", pady=(0, 4))
        ttk.Label(bar, text="图片顺序").pack(side="left")
        self.count_label = ttk.Label(bar, text="0 张", style="Hint.TLabel")
        self.count_label.pack(side="left", padx=(8, 0))
        ttk.Button(bar, text="清空", width=6, command=self.clear_files).pack(side="right")
        ttk.Button(bar, text="移除选中", width=9,
                   command=self.remove_selected).pack(side="right", padx=4)
        ttk.Button(bar, text="添加图片", command=self.add_files).pack(side="right", padx=4)
        ttk.Button(bar, text="按名称排序", command=self.sort_by_name).pack(side="right")

        box = ttk.Frame(self)
        box.pack(fill="both", expand=True)
        self.listbox = tk.Listbox(box, height=6, activestyle="none",
                                  font=("Microsoft YaHei UI", 9), selectmode="extended")
        scroll = ttk.Scrollbar(box, orient="vertical", command=self.listbox.yview)
        self.listbox.configure(yscrollcommand=scroll.set)
        self.listbox.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")

        move = ttk.Frame(self)
        move.pack(fill="x", pady=4)
        ttk.Button(move, text="↑ 上移", width=8,
                   command=lambda: self.move(-1)).pack(side="left")
        ttk.Button(move, text="↓ 下移", width=8,
                   command=lambda: self.move(1)).pack(side="left", padx=4)
        ttk.Label(move, text="列表顺序即 PDF 的页序", style="Hint.TLabel").pack(
            side="left", padx=8)

        opt = ttk.LabelFrame(self, text="输出设置")
        opt.pack(fill="x", pady=6)

        row1 = ttk.Frame(opt)
        row1.pack(fill="x", padx=10, pady=(8, 4))
        ttk.Label(row1, text="页面").pack(side="left")
        self.page_var = tk.StringVar(value=PAGE_MODES[0])
        ttk.Combobox(row1, textvariable=self.page_var, values=PAGE_MODES,
                     state="readonly", width=24).pack(side="left", padx=(8, 18))
        ttk.Label(row1, text="质量").pack(side="left")
        self.quality_var = tk.StringVar(value="标准（88）")
        self.quality_box = ttk.Combobox(row1, textvariable=self.quality_var,
                                        values=list(QUALITIES.keys()),
                                        state="readonly", width=14)
        self.quality_box.pack(side="left", padx=(8, 16))
        self.lossless_var = tk.BooleanVar(value=False)
        self.lossless_box = ttk.Checkbutton(
            row1, text="无损（原图不重新编码）", variable=self.lossless_var,
            command=self._toggle_lossless)
        self.lossless_box.pack(side="left")

        row2 = ttk.Frame(opt)
        row2.pack(fill="x", padx=10, pady=(0, 4))
        self.out_label = ttk.Label(row2, text="输出到")
        self.out_label.pack(side="left")
        self.out_var = tk.StringVar(value="")
        ttk.Entry(row2, textvariable=self.out_var).pack(
            side="left", fill="x", expand=True, padx=(8, 6))
        ttk.Button(row2, text="浏览", width=6,
                   command=self.pick_output).pack(side="left")
        self.open_btn = ttk.Button(row2, text="打开目录", width=9,
                                   state="disabled", command=self.open_folder)
        self.open_btn.pack(side="left", padx=(6, 0))

        row3 = ttk.Frame(opt)
        row3.pack(fill="x", padx=10, pady=(0, 8))
        self.per_file_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(row3, text="逐个导出（每张图一个 PDF）",
                        variable=self.per_file_var,
                        command=self._toggle_per_file).pack(side="left")
        ttk.Label(row3, text="勾选后「输出到」变成目录，文件名沿用原图名",
                  style="Hint.TLabel").pack(side="left", padx=(10, 0))

        act = ttk.Frame(self)
        act.pack(fill="x", pady=(4, 0))
        self.start_btn = ttk.Button(act, text="开始合并", style="Accent.TButton",
                                    command=self.start_convert)
        self.start_btn.pack(side="left")
        self.stop_btn = ttk.Button(act, text="取消", state="disabled",
                                   command=lambda: self.stop_flag.set())
        self.stop_btn.pack(side="left", padx=8)

        if not HAS_PIL:
            self.log("缺少 Pillow，图片转 PDF 不可用。")
        else:
            self.log("图片转 PDF 就绪（Pillow %s）。" % getattr(Image, "__version__", "?"))

    # --- 文件 ---
    def _on_drop(self, event):
        try:
            paths = self.tk.splitlist(event.data)
        except Exception:
            paths = [event.data]
        self.add_paths(paths)

    def add_paths(self, paths):
        added = 0
        for path in paths:
            if os.path.isdir(path):
                names = sorted(os.listdir(path), key=natural_key)
                for name in names:
                    full = os.path.join(path, name)
                    if os.path.isfile(full) and name.lower().endswith(IMG_EXTS):
                        added += 1 if self._append(full) else 0
            elif os.path.isfile(path) and path.lower().endswith(IMG_EXTS):
                added += 1 if self._append(path) else 0
        self.refresh_list()
        if added:
            self.log("已添加 %d 张图片，共 %d 张。" % (added, len(self.files)))
            if not self.out_var.get().strip():
                if self.per_file_var.get():
                    self.out_var.set(os.path.dirname(self.files[0]))
                else:
                    self.out_var.set(self._default_output())

    def _append(self, path):
        full = os.path.abspath(path)
        if full in self.files:
            return False
        self.files.append(full)
        return True

    def _default_output(self):
        base = os.path.dirname(self.files[0]) if self.files else os.getcwd()
        return os.path.join(base, "合并_%s.pdf" % time.strftime("%Y%m%d_%H%M%S"))

    def add_files(self):
        paths = filedialog.askopenfilenames(
            title="选择图片",
            filetypes=[("图片", "*.jpg *.jpeg *.png *.bmp *.tif *.tiff *.webp *.gif"),
                       ("所有文件", "*.*")])
        if paths:
            self.add_paths(list(paths))

    def remove_selected(self):
        for idx in sorted(self.listbox.curselection(), reverse=True):
            del self.files[idx]
        self.refresh_list()

    def clear_files(self):
        if self.files and not messagebox.askyesno(
                "确认", "清空全部 %d 张图片？" % len(self.files)):
            return
        self.files = []
        self.refresh_list()
        self.out_var.set("")

    def sort_by_name(self):
        if not self.files:
            return
        self.files.sort(key=lambda p: natural_key(os.path.basename(p)))
        self.refresh_list()
        self.log("已按文件名排序。")

    def move(self, delta):
        sel = list(self.listbox.curselection())
        if not sel:
            return
        if delta < 0 and sel[0] == 0:
            return
        if delta > 0 and sel[-1] == len(self.files) - 1:
            return
        order = sel if delta < 0 else list(reversed(sel))
        for idx in order:
            target = idx + delta
            self.files[idx], self.files[target] = self.files[target], self.files[idx]
        self.refresh_list()
        for idx in sel:
            self.listbox.selection_set(idx + delta)

    def refresh_list(self):
        self.listbox.delete(0, "end")
        for i, path in enumerate(self.files, 1):
            self.listbox.insert("end", "%d. %s" % (i, os.path.basename(path)))
        self.count_label.configure(text="%d 张" % len(self.files))

    def _toggle_lossless(self):
        if self.lossless_var.get():
            self.quality_box.configure(state="disabled")
            if not HAS_IMG2PDF:
                self.lossless_var.set(False)
                self.quality_box.configure(state="readonly")
                self.log("缺少 img2pdf，无损模式不可用。安装：pip install img2pdf")
                messagebox.showwarning("无损模式不可用",
                                       "当前环境缺少 img2pdf 组件。\n\n"
                                       "安装后即可使用：pip install img2pdf")
            else:
                self.log("已切换无损模式：JPEG 原样嵌入、PNG 无损压缩，"
                         "体积约等于原图总和，画质零损失。")
        else:
            self.quality_box.configure(state="readonly")
            self.log("已切换标准模式：图片会重新编码为 JPEG，体积更小。")

    def _toggle_per_file(self):
        if self.per_file_var.get():
            self.out_label.configure(text="输出目录")
            if self.files:
                self.out_var.set(os.path.dirname(self.files[0]))
            else:
                self.out_var.set("")
            self.log("已切换逐个导出：每张图生成一个同名 PDF。")
        else:
            self.out_label.configure(text="输出到")
            if self.files:
                self.out_var.set(self._default_output())
            self.log("已切换合并导出：所有图片合成一个 PDF。")

    def pick_output(self):
        if self.per_file_var.get():
            init = self.out_var.get().strip() or (
                os.path.dirname(self.files[0]) if self.files else None)
            folder = filedialog.askdirectory(title="选择输出目录", initialdir=init)
            if folder:
                self.out_var.set(folder)
            return

        init = self.out_var.get().strip() or (self._default_output() if self.files else "")
        path = filedialog.asksaveasfilename(
            title="保存为", defaultextension=".pdf",
            initialfile=os.path.basename(init) if init else "合并.pdf",
            initialdir=os.path.dirname(init) if init else None,
            filetypes=[("PDF 文件", "*.pdf")])
        if path:
            self.out_var.set(path)

    def open_folder(self):
        target = self.last_output
        if not target:
            return
        if os.path.isdir(target):
            subprocess.Popen(["explorer", target])
        elif os.path.isfile(target):
            subprocess.Popen(["explorer", "/select,", target])
        elif os.path.isdir(os.path.dirname(target)):
            subprocess.Popen(["explorer", os.path.dirname(target)])

    # --- 转换 ---
    def start_convert(self):
        if self.worker and self.worker.is_alive():
            return
        if not HAS_PIL:
            messagebox.showerror("缺少依赖", "需要 Pillow 才能转换：pip install pillow")
            return
        if not self.files:
            messagebox.showinfo("提示", "请先添加图片。")
            return

        files = list(self.files)
        page_mode = self.page_var.get()
        quality = QUALITIES.get(self.quality_var.get(), 88)
        lossless = bool(self.lossless_var.get()) and HAS_IMG2PDF

        if self.per_file_var.get():
            self._start_per_file(files, page_mode, quality, lossless)
            return

        out = self.out_var.get().strip() or self._default_output()
        if not out.lower().endswith(".pdf"):
            out += ".pdf"
        if os.path.exists(out):
            if not messagebox.askyesno("文件已存在", "目标文件已存在，要覆盖吗？\n\n%s" % out):
                return
        folder = os.path.dirname(out)
        if folder and not os.path.isdir(folder):
            messagebox.showerror("路径无效", "输出目录不存在：\n%s" % folder)
            return

        self.out_var.set(out)

        self.stop_flag.clear()
        self.set_busy(True)
        self.progress["value"] = 0
        self.log("开始合并 %d 张图片 → %s（%s）"
                 % (len(files), out, "无损" if lossless else "标准"))

        self.worker = threading.Thread(
            target=self._worker, args=(files, out, page_mode, quality, lossless),
            daemon=True)
        self.worker.start()

    def _start_per_file(self, files, page_mode, quality, lossless):
        out_dir = self.out_var.get().strip()
        if not out_dir:
            out_dir = os.path.dirname(files[0])
        if out_dir.lower().endswith(".pdf"):
            out_dir = os.path.dirname(out_dir)
        if not os.path.isdir(out_dir):
            if not messagebox.askyesno("目录不存在",
                                       "输出目录不存在，要创建吗？\n\n%s" % out_dir):
                return
            try:
                os.makedirs(out_dir)
            except OSError as exc:
                messagebox.showerror("无法创建目录", str(exc))
                return

        existing = 0
        for path in files:
            stem = os.path.splitext(os.path.basename(path))[0]
            if os.path.exists(os.path.join(out_dir, stem + ".pdf")):
                existing += 1
        if existing and not messagebox.askyesno(
                "文件已存在", "目标目录里已有 %d 个同名 PDF，要覆盖吗？" % existing):
            return

        self.out_var.set(out_dir)
        self.stop_flag.clear()
        self.set_busy(True)
        self.progress["value"] = 0
        self.log("开始逐个导出 %d 张图片 → %s（%s）"
                 % (len(files), out_dir, "无损" if lossless else "标准"))

        self.worker = threading.Thread(
            target=self._worker_per_file,
            args=(files, out_dir, page_mode, quality, lossless), daemon=True)
        self.worker.start()

    def _worker_per_file(self, files, out_dir, page_mode, quality, lossless):
        def on_step(i, total):
            self.post("progress", int(i * 100 / total))
            self.post("status", "正在导出 %d/%d" % (i, total))

        ok, failed, total = images_to_pdfs(
            files, out_dir, page_mode, quality, lossless=lossless,
            on_step=on_step, on_log=lambda t: self.post("log", t),
            stop=self.stop_flag)
        self.post("done", {"kind": "multi", "ok": ok, "failed": failed,
                           "total": total, "dir": out_dir})

    def _worker(self, files, out, page_mode, quality, lossless):
        def on_step(i, total):
            self.post("progress", int(i * 100 / total))
            self.post("status", "正在处理 %d/%d" % (i, total))

        if lossless:
            ok, msg = images_to_pdf_lossless(
                files, out, page_mode,
                on_step=on_step, on_log=lambda t: self.post("log", t),
                stop=self.stop_flag)
        else:
            ok, msg = images_to_pdf(
                files, out, page_mode, quality,
                on_step=on_step, on_log=lambda t: self.post("log", t),
                stop=self.stop_flag)
        self.post("done", {"kind": "single", "ok": ok, "msg": msg})

    def on_done(self, result):
        self.set_busy(False)

        if result.get("kind") == "multi":
            ok = result["ok"]
            failed = result["failed"]
            total = result["total"]
            self.last_output = result["dir"]
            if ok:
                self.open_btn.configure(state="normal")
            if failed:
                self.status_var.set("完成：成功 %d / 共 %d，失败 %d"
                                    % (ok, total, len(failed)))
                self.log("失败 %d 个：" % len(failed))
                for path, msg in failed:
                    self.log("    %s —— %s" % (os.path.basename(path), msg))
                messagebox.showwarning("部分失败", "成功 %d 个，失败 %d 个。详情见日志。"
                                       % (ok, len(failed)))
            elif ok:
                self.status_var.set("完成：已导出 %d 个 PDF" % ok)
                self.log("全部完成，共导出 %d 个 PDF 到 %s" % (ok, result["dir"]))
            else:
                self.status_var.set("未导出任何文件")
                self.log("没有生成任何 PDF。")
            return

        if result["ok"]:
            self.last_output = result["msg"]
            size = os.path.getsize(self.last_output) / 1024.0 / 1024.0
            self.status_var.set("完成：%s（%.1f MB）" % (os.path.basename(self.last_output), size))
            self.log("已生成：%s（%.2f MB）" % (self.last_output, size))
            self.open_btn.configure(state="normal")
        else:
            self.status_var.set("失败")
            self.log("转换失败：%s" % result["msg"])


class App:
    def __init__(self, root):
        self.root = root
        root.title("%s  v%s" % (APP_TITLE, APP_VERSION))
        root.geometry("900x700")
        root.minsize(780, 620)

        style = ttk.Style()
        for theme in ("vista", "winnative", "clam"):
            if theme in style.theme_names():
                style.theme_use(theme)
                break
        style.configure("TButton", padding=(10, 4))
        style.configure("Accent.TButton", padding=(16, 6))
        style.configure("TLabel", font=("Microsoft YaHei UI", 9))
        style.configure("Hint.TLabel", foreground="#6b6b6b",
                        font=("Microsoft YaHei UI", 9))
        style.configure("TNotebook.Tab", padding=(18, 6))

        notebook = ttk.Notebook(root)
        notebook.pack(fill="both", expand=True, padx=8, pady=8)

        self.print_tab = PrintTab(notebook)
        self.image_tab = ImageTab(notebook)
        notebook.add(self.print_tab, text="批量打印")
        notebook.add(self.image_tab, text="图片转 PDF")

        if not HAS_DND:
            self.print_tab.log("当前环境缺少 tkinterdnd2，拖放不可用，请用「添加文件」按钮。")


# ---------------------------------------------------------------- 自检模式
def selftest(out_path):
    lines = ["App version : %s" % APP_VERSION,
             "Frozen      : %s" % getattr(sys, "frozen", False),
             "app_dir     : %s" % app_dir(),
             "resource_dir: %s" % resource_dir()]

    sumatra = find_sumatra()
    lines.append("sumatra     : %s" % (sumatra or "NOT FOUND"))
    lines.append("dnd         : %s" % HAS_DND)
    lines.append("pillow      : %s" % (getattr(Image, "__version__", "?") if HAS_PIL else "MISSING"))
    lines.append("img2pdf     : %s" % (getattr(img2pdf, "__version__", "?") if HAS_IMG2PDF else "MISSING"))

    names = list_printers()
    lines.append("printers    : %d" % len(names))
    for name in names:
        lines.append("    - %s" % name)
    lines.append("default     : %s" % (default_printer() or "NONE"))
    lines.append("settings ex : %s" % build_settings("1-3,5", 2, True))

    code = 0
    if not sumatra:
        code = 1
        lines.append("RESULT      : FAIL (no sumatra)")
    else:
        try:
            proc = subprocess.run([sumatra, "-print-to", "__NoSuchPrinter__", "-silent",
                                   os.path.abspath(__file__)],
                                  timeout=60, creationflags=CREATE_NO_WINDOW)
            lines.append("print smoke : exit=%d (1 = 参数被解析，打印机不存在)" % proc.returncode)
        except Exception as exc:
            code = 2
            lines.append("print smoke : EXCEPTION %s" % exc)

    if HAS_PIL:
        tmp = tempfile.mkdtemp(prefix="selftest_")
        try:
            paths = []
            for i, color in enumerate(((220, 80, 80), (80, 160, 220), (120, 200, 120)), 1):
                img = Image.new("RGB", (1600 - i * 200, 1200), color)
                path = os.path.join(tmp, "img%d.png" % i)
                img.save(path)
                img.close()
                paths.append(path)
            out_pdf = os.path.join(tmp, "merged.pdf")
            ok, msg = images_to_pdf(paths, out_pdf, PAGE_MODES[0], 88)
            if ok and os.path.getsize(out_pdf) > 1000:
                lines.append("pdf smoke   : OK %d bytes" % os.path.getsize(out_pdf))
            else:
                code = 3
                lines.append("pdf smoke   : FAIL %s" % msg)

            if HAS_IMG2PDF:
                out_l = os.path.join(tmp, "merged_lossless.pdf")
                ok2, msg2 = images_to_pdf_lossless(paths, out_l, PAGE_MODES[0])
                if ok2 and os.path.getsize(out_l) > 1000:
                    lines.append("lossless    : OK %d bytes" % os.path.getsize(out_l))
                else:
                    code = 4
                    lines.append("lossless    : FAIL %s" % msg2)

                # 核心保证：JPEG 必须原样出现在 PDF 里
                jpg = os.path.join(tmp, "verbatim.jpg")
                imj = Image.new("RGB", (900, 1200), (180, 90, 60))
                imj.save(jpg, "JPEG", quality=90)
                imj.close()
                out_v = os.path.join(tmp, "verbatim.pdf")
                okv, msgv = images_to_pdf_lossless([jpg], out_v, PAGE_MODES[1])
                if okv:
                    raw = open(jpg, "rb").read()
                    data = open(out_v, "rb").read()
                    same = raw[300:3000] in data
                    lines.append("jpeg verbatim: %s" % same)
                    if not same:
                        code = 5
                else:
                    code = 5
                    lines.append("jpeg verbatim: FAIL %s" % msgv)

                # 逐个导出：3 张图应该得到 3 个 PDF
                per_dir = os.path.join(tmp, "per_file")
                os.makedirs(per_dir, exist_ok=True)
                okp, failedp, totalp = images_to_pdfs(
                    paths, per_dir, PAGE_MODES[0], 88)
                produced = [n for n in os.listdir(per_dir) if n.lower().endswith(".pdf")]
                if okp == totalp and len(produced) == totalp:
                    lines.append("per-file    : OK %d pdf" % len(produced))
                else:
                    code = 6
                    lines.append("per-file    : FAIL ok=%d total=%d pdf=%d"
                                 % (okp, totalp, len(produced)))
            else:
                code = 4
                lines.append("lossless    : SKIP (no img2pdf)")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
    else:
        code = 3
        lines.append("pdf smoke   : SKIP (no pillow)")

    if code == 0:
        lines.append("RESULT      : OK")

    text = "\n".join(lines)
    try:
        with open(out_path, "w", encoding="utf-8") as f:
            f.write(text)
    except OSError:
        pass
    try:
        if sys.stdout:
            print(text)
    except Exception:
        pass
    return code


def main():
    if "--selftest" in sys.argv:
        idx = sys.argv.index("--selftest")
        out = sys.argv[idx + 1] if len(sys.argv) > idx + 1 else os.path.join(
            os.environ.get("TEMP", "."), "pdf_toolbox_selftest.txt")
        sys.exit(selftest(out))

    root = TkinterDnD.Tk() if HAS_DND else tk.Tk()
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
