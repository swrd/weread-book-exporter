#!/usr/bin/env python3
"""WeRead Book Exporter - 图形界面版 (gui.py)

在独立窗口中完成微信读书书籍的抓取与多格式导出：
1. 粘贴书籍链接 / ID，一键执行「抓取 ➔ PDF/EPUB/Markdown 导出」全流程；
2. 实时滚动显示逐页抓取进度与章节落盘日志，支持随时安全终止；
3. 三种运行模式：完整流程 / 仅抓取 / 仅导出，可勾选「强制重新抓取」；
4. 内置「已抓取书籍」历史列表，双击自动填入 ID，直接断点续跑；
5. 以子进程方式调用原有脚本，与命令行行为完全一致，互不干扰。
"""
import json
import os
import queue
import re
import subprocess
import sys
import threading
import tkinter as tk
from tkinter import ttk, messagebox

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

BASE = os.path.dirname(os.path.abspath(__file__))
OUTPUT_DIR = os.path.join(BASE, "output")
SCRIPTS = {
    "full": os.path.join(BASE, "main.py"),
    "crawl": os.path.join(BASE, "crawl_weread.py"),
    "export": os.path.join(BASE, "export_book.py"),
}
MODE_LABELS = {
    "full": "完整流程（抓取 + 导出）",
    "crawl": "仅抓取（不导出）",
    "export": "仅导出（复用已抓取数据）",
}

CREATE_NO_WINDOW = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0

# 与 crawl_weread.extract_book_id 保持一致的轻量校验（避免为一个小函数导入 playwright）
ID_URL_RE = re.compile(r"(?:reader|bookDetail)/([a-zA-Z0-9_-]+)")
PLAIN_ID_RE = re.compile(r"^[a-zA-Z0-9_-]{6,36}$")

TAG_COLORS = {
    "err": "#c62828",
    "ok": "#1b7a3e",
    "warn": "#a3570b",
    "info": "#155fa0",
}

FONT_MAIN = ("Microsoft YaHei UI", 10)
FONT_ENTRY = ("Microsoft YaHei UI", 12)
FONT_LOG = ("Microsoft YaHei UI", 10)


def looks_like_book_input(text):
    """粗略校验输入是否像有效的书籍链接 / ID（严格校验由子进程脚本完成）"""
    text = (text or "").strip()
    return bool(text) and ("weread.qq.com" in text or ID_URL_RE.search(text) or PLAIN_ID_RE.match(text))


class WeReadGUI:
    POLL_MS = 90           # 日志队列轮询间隔
    MAX_LOG_LINES = 8000   # 日志窗口最大行数，超出后丢弃最旧内容

    def __init__(self, root):
        self.root = root
        self.proc = None
        self.running = False
        self.cancel_requested = False
        self.saw_error = False
        self.log_queue = queue.Queue()
        self._chapters = 0
        self._words = 0

        self._build_ui()
        self._load_history()
        self._poll_log()

        self._append_log("📚 微信读书导出工具 · 图形界面已就绪")
        self._append_log("👉 粘贴书籍链接或输入 ID，点击「开始导出」；首次运行会弹出浏览器扫码登录。")

    # ------------------------------------------------------------------ UI
    def _build_ui(self):
        self.root.title("📚 微信读书导出工具 | WeRead Book Exporter")
        self.root.geometry("1000x660")
        self.root.minsize(840, 560)
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)

        # —— 状态栏（先 pack 到底部，保证中间面板可展开）——
        bar = ttk.Frame(self.root, padding=(10, 5))
        bar.pack(fill="x", side="bottom")
        ttk.Separator(self.root, orient="horizontal").pack(fill="x", side="bottom")

        self.status_var = tk.StringVar(value="空闲 · 等待输入书籍链接或 ID")
        self.status_label = ttk.Label(bar, textvariable=self.status_var, font=FONT_MAIN)
        self.status_label.pack(side="left")

        self.prog_var = tk.StringVar(value="")
        self.prog_label = ttk.Label(bar, textvariable=self.prog_var, font=FONT_MAIN, foreground="#555")
        self.prog_label.pack(side="right", padx=(8, 0))
        self.progress = ttk.Progressbar(bar, mode="indeterminate", length=110)
        # 空闲时隐藏，仅在任务运行期间显示（避免静止绿块造成误解）

        # —— 顶部输入区 ——
        top = ttk.Frame(self.root, padding=(12, 10, 12, 2))
        top.pack(fill="x")
        ttk.Label(top, text="书籍链接 / ID:", font=FONT_MAIN).pack(side="left")
        self.entry_var = tk.StringVar()
        entry = ttk.Entry(top, textvariable=self.entry_var, font=FONT_ENTRY)
        entry.pack(side="left", fill="x", expand=True, padx=8)
        entry.bind("<Return>", lambda _e: self.start())
        entry.bind("<Control-Return>", lambda _e: self.start())
        ttk.Button(top, text="📋 粘贴", command=self.paste).pack(side="left", padx=(0, 4))
        ttk.Button(top, text="🗑 清空", command=lambda: self.entry_var.set("")).pack(side="left")

        # —— 控制区 ——
        row = ttk.Frame(self.root, padding=(12, 2, 12, 6))
        row.pack(fill="x")
        ttk.Label(row, text="模式:", font=FONT_MAIN).pack(side="left")
        self.mode_box = ttk.Combobox(
            row, state="readonly", width=24, font=FONT_MAIN,
            values=list(MODE_LABELS.values()),
        )
        self.mode_box.current(0)
        self.mode_box.pack(side="left", padx=(6, 12))

        self.force_var = tk.BooleanVar(value=False)
        self.force_chk = ttk.Checkbutton(row, text="强制重新抓取 (--force)", variable=self.force_var)
        self.force_chk.pack(side="left", padx=(0, 14))

        self.start_btn = tk.Button(
            row, text="🚀 开始导出", font=("Microsoft YaHei UI", 11, "bold"),
            bg="#2e7d32", fg="#ffffff", activebackground="#256428", activeforeground="#ffffff",
            relief="flat", padx=18, pady=3, cursor="hand2", command=self.start,
        )
        self.start_btn.pack(side="left", padx=(0, 8))

        self.cancel_btn = tk.Button(
            row, text="⛔ 终止任务", font=("Microsoft YaHei UI", 10),
            bg="#d8b0b0", fg="#5a1414", activebackground="#c62828", activeforeground="#ffffff",
            relief="flat", padx=12, pady=3, cursor="hand2", command=self.cancel,
        )
        self.cancel_btn.pack(side="left", padx=(0, 8))
        self.cancel_btn.config(state="disabled")

        ttk.Button(row, text="📂 打开输出目录", command=self.open_output).pack(side="right")

        # —— 中部：日志 + 历史 ——
        pane = ttk.PanedWindow(self.root, orient="horizontal")
        pane.pack(fill="both", expand=True, padx=12, pady=(2, 4))

        log_frame = ttk.LabelFrame(pane, text=" 运行日志 ")
        pane.add(log_frame, weight=1)

        self.log = tk.Text(
            log_frame, wrap="word", font=FONT_LOG, state="disabled",
            bg="#fbfaf8", fg="#2c3e50", relief="flat",
            selectbackground="#bcd6ef", padx=8, pady=6,
        )
        log_scroll = ttk.Scrollbar(log_frame, orient="vertical", command=self.log.yview)
        self.log.configure(yscrollcommand=log_scroll.set)
        log_scroll.pack(side="right", fill="y")
        self.log.pack(fill="both", expand=True)
        for tag, color in TAG_COLORS.items():
            self.log.tag_configure(tag, foreground=color)

        hist_frame = ttk.LabelFrame(pane, text=" 已抓取书籍 ")
        pane.add(hist_frame, weight=0)

        cols = ("title", "status", "chaps", "words", "bid")
        self.tree = ttk.Treeview(hist_frame, columns=cols, show="headings", selectmode="browse")
        for cid, text, width, anchor in (
            ("title", "书名", 168, "w"),
            ("status", "状态", 74, "center"),
            ("chaps", "章节", 46, "center"),
            ("words", "字数", 72, "e"),
            ("bid", "书籍 ID", 132, "w"),
        ):
            self.tree.heading(cid, text=text)
            self.tree.column(cid, width=width, anchor=anchor, stretch=(cid == "title"))
        tree_scroll = ttk.Scrollbar(hist_frame, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=tree_scroll.set)
        tree_scroll.pack(side="right", fill="y")
        self.tree.pack(fill="both", expand=True, padx=(4, 0), pady=(4, 0))
        self.tree.bind("<Double-1>", self._on_tree_double)

        hist_bar = ttk.Frame(hist_frame, padding=(4, 3))
        hist_bar.pack(fill="x")
        ttk.Label(hist_bar, text="双击条目填入 ID（可断点续跑）", font=("Microsoft YaHei UI", 9), foreground="#777").pack(side="left")
        ttk.Button(hist_bar, text="🔄 刷新", width=8, command=self._load_history).pack(side="right")

    # ------------------------------------------------------------- 日志与状态
    def _tag_of(self, line):
        if "❌" in line or "失败" in line:
            return "err"
        if "✅" in line or "🎉" in line or "生成完成" in line or "已保存" in line:
            return "ok"
        if "⚠" in line or "⏸" in line or "扫码" in line or "提示:" in line or "终止" in line:
            return "warn"
        if "🚀" in line or "📑" in line or "🔄" in line or "🌐" in line or "【步骤" in line or "👉" in line:
            return "info"
        return None

    def _append_log(self, line):
        at_bottom = self.log.yview()[1] >= 0.98
        self.log.config(state="normal")
        self.log.insert("end", line + "\n", self._tag_of(line) or ())
        end_line = int(self.log.index("end-1c").split(".")[0])
        if end_line > self.MAX_LOG_LINES:
            self.log.delete("1.0", "%d.0" % (end_line - self.MAX_LOG_LINES))
        self.log.config(state="disabled")
        if at_bottom:
            self.log.see("end")

        # —— 状态栏与进度联动 ——
        if "❌" in line:
            self.saw_error = True
        if "检测到未登录" in line:  # 仅匹配爬虫真正的扫码提示，避免欢迎语误触发
            self._set_status("🔌 请在弹出的浏览器窗口中【微信扫码】登录（限时 300 秒）", "warn")
        elif "【步骤 1/2】" in line:
            self._set_status("阶段 1/2：抓取全书章节、插图与封面...", "info")
        elif "【步骤 2/2】" in line:
            self._set_status("阶段 2/2：生成 PDF / EPUB / Markdown...", "info")
        elif "🎉 抓取成功" in line:
            self._set_status("抓取完成，正在进入导出流程...", "ok")
        elif "全部完成" in line or "生成完毕" in line:
            self._set_status("🎉 全部完成！文件已保存至 output/ 目录", "ok")

        m = re.search(r"💾.*\[(\d+)\]", line)  # 取落盘行最后一个 [序号]（跳过 [章节实时落盘] 等中文括号段）
        if m:
            self._chapters = int(m.group(1))
        m = re.search(r"累计\s*(\d+)\s*字", line)
        if m:
            self._words = int(m.group(1))
        if self._chapters or self._words:
            self.prog_var.set("已落盘 %d 章 · 累计 %s 字" % (self._chapters, format(self._words, ",")))

    def _set_status(self, text, kind="info"):
        self.status_var.set(text)
        self.status_label.config(foreground=TAG_COLORS.get(kind, "#333333"))

    # ------------------------------------------------------------ 任务启动/终止
    def start(self):
        if self.running:
            messagebox.showinfo("任务进行中", "当前已有任务在运行，请等待完成或先终止。")
            return

        book = self.entry_var.get().strip()
        if not book:
            messagebox.showwarning("缺少输入", "请先粘贴微信读书书籍链接或输入书籍 ID。")
            return
        if not looks_like_book_input(book):
            if not messagebox.askyesno(
                "输入格式可疑",
                "「%s」\n看起来不是有效的微信读书链接或书籍 ID，仍然尝试执行吗？" % book[:60],
            ):
                return

        label_to_mode = {v: k for k, v in MODE_LABELS.items()}
        mode = label_to_mode.get(self.mode_box.get(), "full")
        force = self.force_var.get() and mode != "export"
        script = SCRIPTS[mode]
        if not os.path.isfile(script):
            messagebox.showerror("脚本缺失", "未找到 %s，请确认项目文件完整。" % script)
            return

        cmd = [sys.executable, "-u", script, book]
        if force:
            cmd.append("--force")

        env = dict(os.environ)
        env["PYTHONIOENCODING"] = "utf-8"
        env["PYTHONUTF8"] = "1"

        try:
            self.proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                cwd=BASE,
                env=env,
                creationflags=CREATE_NO_WINDOW,
            )
        except Exception as e:
            messagebox.showerror("启动失败", "无法启动导出进程：\n%s" % e)
            return

        self.running = True
        self.cancel_requested = False
        self.saw_error = False
        self._chapters = 0
        self._words = 0
        self.prog_var.set("")
        self._set_running_ui(True)
        self._set_status("⏳ 任务已启动，等待日志输出...", "info")

        self._append_log("=" * 52)
        self._append_log("🚀 启动任务: %s %s%s" % (
            os.path.basename(script), book[:70], "（强制重抓）" if force else ""))

        threading.Thread(target=self._reader, args=(self.proc,), daemon=True).start()

    def cancel(self):
        if not self.running or not self.proc:
            return
        if not messagebox.askyesno(
            "确认终止",
            "确定终止当前导出任务吗？\n（已抓取落盘的章节会保留，下次运行可自动断点续传）",
        ):
            return
        self.cancel_requested = True
        self._append_log("⛔ 正在终止任务（关闭子进程与其浏览器）...")
        self._set_status("正在终止...", "warn")
        self._kill_tree()

    def _kill_tree(self):
        if not self.proc:
            return
        try:
            if os.name == "nt":
                subprocess.run(
                    ["taskkill", "/F", "/T", "/PID", str(self.proc.pid)],
                    capture_output=True, creationflags=CREATE_NO_WINDOW,
                )
            else:
                self.proc.terminate()
        except Exception:
            pass

    def _reader(self, proc):
        """后台线程：逐行读取子进程输出，推入队列由主线程刷新界面"""
        try:
            for raw in iter(proc.stdout.readline, b""):
                self.log_queue.put(raw.decode("utf-8", errors="replace").rstrip("\r\n"))
        except Exception:
            pass
        finally:
            try:
                rc = proc.wait()
            except Exception:
                rc = -1
            self.log_queue.put(("__EXIT__", rc))

    def _poll_log(self):
        exit_rc = None
        try:
            while True:
                item = self.log_queue.get_nowait()
                if isinstance(item, tuple) and item[0] == "__EXIT__":
                    exit_rc = item[1]
                else:
                    self._append_log(item)
        except queue.Empty:
            pass
        if exit_rc is not None:
            self._on_exit(exit_rc)
        self.root.after(self.POLL_MS, self._poll_log)

    def _on_exit(self, rc):
        self.running = False
        self.proc = None
        self._set_running_ui(False)
        self._load_history()
        if self.cancel_requested:
            self._append_log("⛔ 任务已被终止。已抓取内容保留，可重新运行断点续传。")
            self._set_status("已终止", "warn")
        elif rc == 0 and not self.saw_error:
            self._append_log("✔ 子进程正常退出。")
            self._set_status("任务完成 ✅", "ok")
        else:
            self._set_status("任务结束（退出码 %s），请检查上方日志" % rc, "err")
        self.cancel_requested = False
        self.saw_error = False

    def _set_running_ui(self, running):
        self.start_btn.config(
            state="disabled" if running else "normal",
            bg="#9dbb9d" if running else "#2e7d32",
        )
        self.cancel_btn.config(
            state="normal" if running else "disabled",
            bg="#c62828" if running else "#d8b0b0",
        )
        self.mode_box.config(state="disabled" if running else "readonly")
        self.force_chk.config(state="disabled" if running else "normal")
        if running:
            self.progress.pack(side="right")  # label 已在最右，progress 会排到其左侧
            self.progress.start(12)
        else:
            self.progress.stop()
            self.progress.pack_forget()

    # ------------------------------------------------------------- 历史与工具
    def _load_history(self):
        for iid in self.tree.get_children():
            self.tree.delete(iid)
        if not os.path.isdir(OUTPUT_DIR):
            return
        for name in sorted(os.listdir(OUTPUT_DIR)):
            book_dir = os.path.join(OUTPUT_DIR, name)
            if not name.endswith("_v2") or not os.path.isdir(book_dir):
                continue
            book_id = name[:-3]
            title, status, chaps, words = book_id, "—", "", ""
            try:
                with open(os.path.join(book_dir, "meta.json"), encoding="utf-8") as f:
                    title = json.load(f).get("title") or book_id
            except Exception:
                pass
            try:
                with open(os.path.join(book_dir, "_state.json"), encoding="utf-8") as f:
                    st = json.load(f)
                chaps = st.get("saved_chapter_count", "")
                words = format(st.get("total_words", 0) or 0, ",")
                if st.get("completed"):
                    status = "✅ 完成"
                elif chaps:
                    status = "⏸ 中断"
            except Exception:
                pass
            self.tree.insert("", "end", values=(title, status, chaps, words, book_id))

    def _on_tree_double(self, _event):
        sel = self.tree.selection()
        if not sel:
            return
        vals = self.tree.item(sel[0], "values")
        book_id = vals[-1]
        self.entry_var.set(book_id)
        self._set_status("已填入《%s》的 ID，点击「开始导出」继续" % vals[0], "info")
        self._append_log("👉 已填入历史书籍 ID: %s（来自《%s》），点击「开始导出」继续" % (book_id, vals[0]))

    def paste(self):
        try:
            text = self.root.clipboard_get().strip()
        except tk.TclError:
            self._set_status("剪贴板为空或不可读取", "warn")
            return
        if text:
            self.entry_var.set(text)
            self._set_status("已粘贴，点击「开始导出」", "info")

    def open_output(self):
        os.makedirs(OUTPUT_DIR, exist_ok=True)
        try:
            if os.name == "nt":
                os.startfile(OUTPUT_DIR)
            elif sys.platform == "darwin":
                subprocess.Popen(["open", OUTPUT_DIR])
            else:
                subprocess.Popen(["xdg-open", OUTPUT_DIR])
        except Exception as e:
            messagebox.showerror("打开失败", str(e))

    def on_close(self):
        if self.running:
            if not messagebox.askyesno("退出确认", "任务仍在运行，退出窗口将终止任务。\n确定退出吗？"):
                return
            self._kill_tree()
        self.root.destroy()


def main():
    if os.name == "nt":
        try:
            from ctypes import windll
            windll.shcore.SetProcessDpiAwareness(1)  # 高分屏下界面清晰
        except Exception:
            pass
    try:
        root = tk.Tk()
        WeReadGUI(root)
        root.mainloop()
    except Exception:
        # pythonw 启动时没有控制台，异常必须落盘 + 弹窗，否则无从排查
        import traceback
        err = traceback.format_exc()
        try:
            with open(os.path.join(BASE, "_gui_error.log"), "w", encoding="utf-8") as f:
                f.write(err)
        except Exception:
            pass
        try:
            messagebox.showerror("启动失败", err)
        except Exception:
            pass
        raise


if __name__ == "__main__":
    main()
