#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""osu! 谱面搜索下载器（数据源：Sayobot 镜像站）。

预填当前播放的歌曲名 -> 搜索谱面 -> 一键下载 osz -> 改名 zip 解压到指定目录 -> 删除 zip。
纯标准库（tkinter 界面），无第三方依赖。

    python osu_map_finder.py            启动界面
    python osu_map_finder.py --selftest 内置自检（不联网、不开窗）
    python osu_map_finder.py --smoke    建一次窗口就退出
"""

import json
import os
import queue
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import urllib.request
import zipfile
from concurrent.futures import ThreadPoolExecutor
from tkinter import filedialog, messagebox, ttk

import tkinter as tk

# 打包成 exe 后 __file__ 指向 PyInstaller 的临时解包目录，配置和下载目录得跟着 exe 走
APP_DIR = (os.path.dirname(os.path.abspath(sys.executable)) if getattr(sys, "frozen", False)
           else os.path.dirname(os.path.abspath(__file__)))
VERSION = "1.0.8"
CONFIG_PATH = os.path.join(APP_DIR, "config.json")
DEFAULT_SAVE_DIR = os.path.join(APP_DIR, "beatmaps")
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"

SAYOBOT_POST = "https://api.sayobot.cn/?post"
SAYOBOT_INFO = "https://api.sayobot.cn/v2/beatmapinfo?0="
SAYOBOT_DL = "https://dl.sayobot.cn/beatmaps/download/full/%d"

# ---- Sayobot 搜索参数：位掩码抄自 osu.sayobot.cn 前端 core/config/options.ts ----
MODES = [("STD", 1), ("Taiko", 2), ("CTD", 4), ("Mania", 8)]
CLASSES = [("Ranked & Approved", 1), ("Qualified", 2), ("Loved", 4),
           ("Pending & WIP", 8), ("Graveyard", 16)]
SUBTYPES = [("标题", 1), ("艺术家", 2), ("作图者", 4), ("难度", 8), ("标签", 16), ("提供方", 32)]
GENRES = [("any", 1), ("尚未指定", 2), ("电子游戏", 4), ("动漫", 8), ("摇滚", 16),
          ("流行乐", 32), ("其他", 64), ("新奇", 128), ("嘻哈", 256), ("电子", 1024)]
LANGUAGES = [("any", 1), ("其他", 2), ("English", 4), ("Japanese", 8), ("中文", 16),
             ("器乐", 32), ("韩语", 64), ("法语", 128), ("德语", 256), ("瑞典语", 512),
             ("西班牙语", 1024), ("意大利语", 2048)]
RANGES = [("Stars", "stars", 0, 10), ("AR", "ar", 0, 10), ("OD", "od", 0, 10),
          ("CS", "cs", 0, 10), ("HP", "hp", 0, 10), ("BPM", "bpm", 0, 1000),
          ("Length", "length", 0, 1000)]

MODE_NAME = {1: "STD", 2: "Taiko", 4: "CTD", 8: "Mania"}

# 列表列：(行数据键, 表头, 字符宽, 排序取值函数)。表头点一下按该列排，再点一下反向。
COLUMNS = [
    ("title", "曲名", 34, lambda r: (r["artist"] + " " + r["title"]).lower()),
    ("creator", "谱师", 12, lambda r: r["creator"].lower()),
    ("modes", "模式", 11, lambda r: "/".join(MODE_NAME[b] for b in r["mode_bits"])),
    ("stars", "Stars", 7, lambda r: r.get("stars")),
    ("bpm", "BPM", 7, lambda r: r.get("bpm")),
    ("length", "时长", 7, lambda r: r.get("length")),
    ("plays", "播放量", 9, lambda r: r.get("plays")),
    ("likes", "点赞", 7, lambda r: r.get("likes")),
    ("status", "状态", 16, lambda r: r["status"]),
]
COLUMN_GET = {key: get for key, _text, _width, get in COLUMNS}


def sort_rows(rows, key, desc=False):
    """按某列排序，缺值（None，详情还没回来）永远排最后。"""
    get = COLUMN_GET[key]
    missing = [r for r in rows if get(r) is None]
    return sorted((r for r in rows if get(r) is not None), key=get, reverse=desc) + missing


def _count(value):
    return "{:,}".format(int(value)) if value else "—"


def _say(text):
    """打包成窗口程序后 sys.stdout 可能是 None，print 会炸。"""
    if sys.stdout:
        print(text)


def _resource(name):
    """打包后随包资源在 PyInstaller 的解包目录里。"""
    return os.path.join(getattr(sys, "_MEIPASS", APP_DIR), name)


def _json(url, data=None, headers=None, timeout=30):
    head = {"User-Agent": USER_AGENT}
    head.update(headers or {})
    with urllib.request.urlopen(urllib.request.Request(url, data=data, headers=head),
                                timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8-sig"))


def sayobot_body(keyword, sel, limit=25, offset=0):
    """界面勾选状态 -> Sayobot beatmaplist 请求体。某组一个都没勾=该组不过滤。

    关键词留空就走 type=hot 的热门榜（服务端自己排的，会忽略这边的筛选参数）。
    """
    body = {"cmd": "beatmaplist", "limit": limit, "offset": offset}
    if keyword:
        body["type"], body["keyword"] = "search", keyword
    else:
        body["type"] = "hot"
    for key in ("mode", "class", "subtype", "genre", "language"):
        bits = sel.get(key) or []
        if bits:
            body[key] = sum(bits)
    for key, span in (sel.get("range") or {}).items():
        body[key] = [span[0], span[1]]
    return body


def sayobot_row(item):
    """列表里的一条 -> 表格一行。modes 是模式位掩码，approved 是状态码。"""
    bitmask = item.get("modes") or 0
    return {"sid": item["sid"], "title": item.get("title") or "",
            "artist": item.get("artist") or "", "creator": item.get("creator") or "",
            "mode_bits": [bit for _name, bit in MODES if bitmask & bit],
            "status": {1: "ranked", 2: "qualified", 3: "qualified", 4: "loved",
                       -2: "graveyard", 0: "pending"}.get(item.get("approved"), "pending"),
            "stars": None, "bpm": None, "length": None,
            "plays": item.get("play_count"), "likes": item.get("favourite_count")}


def sayobot_search(keyword, sel, limit=25, offset=0):
    data = _json(SAYOBOT_POST, json.dumps(sayobot_body(keyword, sel, limit, offset)).encode(),
                 {"Content-Type": "application/json"}, timeout=40)
    return [sayobot_row(item) for item in data.get("data") or []]


def sayobot_detail(sid):
    """列表接口不给 BPM/时长，逐条补详情（失败返回空 dict，界面显示 —）。"""
    try:
        data = _json(SAYOBOT_INFO + str(sid), timeout=30).get("data") or {}
        bids = data.get("bid_data") or []
        stars = [b["star"] for b in bids if b.get("star")]
        lengths = [b["length"] for b in bids if b.get("length")]
        return {"stars": max(stars) if stars else None, "bpm": data.get("bpm"),
                "length": max(lengths) if lengths else None}
    except Exception:
        return {}


def fetch_file(url, path, progress=None):
    with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": USER_AGENT}),
                                timeout=120) as resp, open(path, "wb") as fh:
        total = int(resp.headers.get("Content-Length") or 0)
        done = 0
        while True:
            chunk = resp.read(1 << 16)
            if not chunk:
                break
            fh.write(chunk)
            done += len(chunk)
            if progress and total:
                progress(done * 100 // total)
    return path


# ---------------------------------------------------------------- 下载 / 解压
def fix_legacy_zip_names(zf):
    """资源管理器压出来的中文包名（无 UTF-8 标记）会解成乱码，按 GBK 还原。

    # ponytail: 只处理 cp437->gbk；真遇到别的代码页再加。
    """
    changed = False
    for info in zf.infolist():
        if info.flag_bits & 0x800 or info.filename.isascii():
            continue
        try:
            info.filename = info.filename.encode("cp437").decode("gbk")
            changed = True
        except (UnicodeEncodeError, UnicodeDecodeError):
            continue
    if changed:
        zf.NameToInfo = {info.filename: info for info in zf.infolist()}


def safe_name(name):
    return re.sub(r'[\\/:*?"<>|\r\n\t]', "_", name).strip(" .")[:120] or "beatmap"


def download_map(row, save_dir, progress=None):
    """下载 osz -> 改名 zip -> 解压到 save_dir\\<谱面集编号 艺术家 - 曲名> -> 删掉 zip。

    文件夹名跟 osu! 客户端 Songs 目录一个格式（编号在最前），别的工具才认得出来。
    """
    folder = os.path.join(save_dir, safe_name("%d %s - %s" % (row["sid"], row["artist"],
                                                              row["title"])))
    if os.path.exists(folder):
        return None, "已存在同名文件夹，跳过：" + os.path.basename(folder)
    tmp = tempfile.mkdtemp(prefix="osu_map_")
    try:
        osz = os.path.join(tmp, "%d.osz" % row["sid"])
        fetch_file(SAYOBOT_DL % row["sid"], osz, progress)
        zip_path = os.path.join(tmp, "%d.zip" % row["sid"])
        os.replace(osz, zip_path)
        os.makedirs(folder)
        with zipfile.ZipFile(zip_path) as zf:
            fix_legacy_zip_names(zf)
            zf.extractall(folder)
        return folder, None
    except Exception as exc:
        shutil.rmtree(folder, ignore_errors=True)
        return None, "%s: %s" % (type(exc).__name__, exc)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)   # 顺带删掉那个 zip


# ---------------------------------------------------------------- 当前播放（Windows SMTC）
# 与 desktop-pet-ai/running_apps.py 同一套做法：调用系统自带 PowerShell 查媒体会话。
_SMTC_PS1 = r'''
$ErrorActionPreference = "Stop"
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
Add-Type -AssemblyName System.Runtime.WindowsRuntime
$asTaskGeneric = ([System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object { $_.Name -eq 'AsTask' -and $_.GetParameters().Count -eq 1 -and $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1' })[0]
function Await($WinRtTask, $ResultType) {
  $asTask = $asTaskGeneric.MakeGenericMethod($ResultType)
  $netTask = $asTask.Invoke($null, @($WinRtTask))
  $netTask.Wait(-1) | Out-Null
  $netTask.Result
}
[Windows.Media.Control.GlobalSystemMediaTransportControlsSessionManager, Windows.Media.Control, ContentType=WindowsRuntime] | Out-Null
$mgr = Await ([Windows.Media.Control.GlobalSystemMediaTransportControlsSessionManager]::RequestAsync()) ([Windows.Media.Control.GlobalSystemMediaTransportControlsSessionManager])
$out = @()
foreach ($s in @($mgr.GetSessions())) {
  $pi = $s.GetPlaybackInfo()
  if ([int]$pi.PlaybackStatus -ne 4) { continue }
  try {
    $props = Await ($s.TryGetMediaPropertiesAsync()) ([Windows.Media.Control.GlobalSystemMediaTransportControlsSessionMediaProperties])
  } catch { continue }
  $app = [string]$s.SourceAppUserModelId
  if ($app -match '[\\/]') { $app = Split-Path -Leaf $app }
  $out += [pscustomobject]@{ App = $app; Title = [string]$props.Title; Artist = [string]$props.Artist }
}
if ($out.Count -gt 0) { $out | ConvertTo-Json -Compress } else { Write-Output "[]" }
'''


def now_playing():
    """当前播放的 {"title","artist","app"}；没在播放或查询失败返回 None。"""
    try:
        import base64
        enc = base64.b64encode(_SMTC_PS1.encode("utf-16-le")).decode("ascii")
        proc = subprocess.run(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
             "-EncodedCommand", enc],
            capture_output=True, timeout=20, creationflags=subprocess.CREATE_NO_WINDOW)
        for line in proc.stdout.decode("utf-8", "replace").splitlines():
            line = line.strip()
            if line.startswith(("{", "[")):
                data = json.loads(line)
                data = data[0] if isinstance(data, list) else data
                if data.get("Title"):
                    return {"title": data.get("Title", ""), "artist": data.get("Artist", ""),
                            "app": data.get("App", "")}
    except Exception:
        pass
    return None


# ---------------------------------------------------------------- 配置
def load_config():
    cfg = {"save_dir": DEFAULT_SAVE_DIR}
    try:
        with open(CONFIG_PATH, encoding="utf-8") as fh:
            cfg.update(json.load(fh))
    except Exception:
        pass
    return cfg


def save_config(cfg):
    try:
        with open(CONFIG_PATH, "w", encoding="utf-8") as fh:
            json.dump(cfg, fh, ensure_ascii=False, indent=2)
    except Exception:
        pass


def fmt_length(seconds):
    if not seconds:
        return "—"
    return "%d:%02d" % (int(seconds) // 60, int(seconds) % 60)


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("osu! 谱面搜索下载器 v" + VERSION)
        self.geometry("1200x800")
        self.minsize(960, 620)
        icon = _resource("icon.ico")
        if os.path.exists(icon):
            self.iconbitmap(icon)
        self.cfg = load_config()
        self.events = queue.Queue()
        self.row_widgets = {}
        self.empty_label = None
        self.rows = []
        self.sort_key = None
        self.sort_desc = False
        self.busy = False
        self.offset = 0
        self._build()
        self.after(80, self._drain)
        self.after(300, self._prefill)

    # -------------------------------------------------- 界面
    def _build(self):
        top = ttk.Frame(self, padding=(10, 8, 10, 4))
        top.pack(fill="x")
        ttk.Label(top, text="歌曲名/关键词").pack(side="left")
        self.keyword = tk.StringVar()
        entry = ttk.Entry(top, textvariable=self.keyword, width=44)
        entry.pack(side="left", padx=6)
        entry.bind("<Return>", lambda _event: self.search())
        ttk.Button(top, text="获取当前播放", command=self._prefill).pack(side="left")
        ttk.Button(top, text="搜索", command=self.search).pack(side="left", padx=(6, 0))

        self.filters = ttk.LabelFrame(self, text="高级选项（全部勾选 = 不筛选）", padding=(10, 4))
        self.filters.pack(fill="x", padx=10)
        self.mode_vars = self._check_group(self.filters, "模式", MODES, 0)
        self.class_vars = self._check_group(self.filters, "状态", CLASSES, 1)
        self.subtype_vars = self._check_group(self.filters, "范围", SUBTYPES, 2)
        self.genre_vars = self._check_group(self.filters, "分类", GENRES, 3)
        self.language_vars = self._check_group(self.filters, "语言", LANGUAGES, 4)
        ranges = ttk.Frame(self.filters)
        ranges.grid(row=5, column=0, columnspan=3, sticky="w", pady=(4, 2))
        self.range_widgets = {}
        for index, (label, key, low, high) in enumerate(RANGES):
            box = ttk.Frame(ranges)
            box.grid(row=index // 4, column=index % 4, sticky="w", padx=(0, 14))
            enabled = tk.BooleanVar(value=False)
            ttk.Checkbutton(box, text=label, variable=enabled, width=7).pack(side="left")
            low_var = tk.StringVar(value=str(low))
            high_var = tk.StringVar(value=str(high))
            ttk.Entry(box, textvariable=low_var, width=6).pack(side="left")
            ttk.Label(box, text="~").pack(side="left")
            ttk.Entry(box, textvariable=high_var, width=6).pack(side="left")
            self.range_widgets[key] = (enabled, low_var, high_var, low, high)
        ttk.Label(self.filters, foreground="#888",
                  text="筛选由 Sayobot 服务端执行；某个区间勾上但填错会退回默认值").grid(
            row=6, column=0, columnspan=3, sticky="w")

        head = ttk.Frame(self, padding=(10, 8, 10, 0))
        head.pack(fill="x")
        self.head_labels = {}
        for key, text, width, _get in COLUMNS:
            label = ttk.Label(head, text=text, width=width, anchor="w", cursor="hand2")
            label.pack(side="left")
            label.bind("<Button-1>", lambda _event, k=key: self.sort_by(k))
            self.head_labels[key] = label

        body = ttk.Frame(self)
        body.pack(fill="both", expand=True, padx=10, pady=(0, 4))
        self.canvas = tk.Canvas(body, highlightthickness=0)
        bar = ttk.Scrollbar(body, orient="vertical", command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=bar.set)
        bar.pack(side="right", fill="y")
        self.canvas.pack(side="left", fill="both", expand=True)
        self.results = ttk.Frame(self.canvas)
        self.canvas.create_window((0, 0), window=self.results, anchor="nw")
        self.results.bind("<Configure>",
                          lambda _event: self.canvas.configure(scrollregion=self.canvas.bbox("all")))
        self.canvas.bind_all("<MouseWheel>",
                             lambda event: self.canvas.yview_scroll(-event.delta // 120, "units"))

        bottom = ttk.Frame(self, padding=(10, 4, 10, 8))
        bottom.pack(fill="x")
        ttk.Label(bottom, text="保存路径").pack(side="left")
        self.save_dir = tk.StringVar(value=self.cfg.get("save_dir") or DEFAULT_SAVE_DIR)
        ttk.Entry(bottom, textvariable=self.save_dir, width=52).pack(side="left", padx=6)
        ttk.Button(bottom, text="浏览…", command=self._pick_dir).pack(side="left")
        ttk.Button(bottom, text="打开目录", command=self._open_dir).pack(side="left", padx=4)
        self.status = tk.StringVar(value="就绪")
        ttk.Label(bottom, textvariable=self.status).pack(side="left", padx=12)
        ttk.Button(bottom, text="加载更多", command=self.load_more).pack(side="right")

    def _check_group(self, parent, title, options, row):
        frame = ttk.Frame(parent)
        frame.grid(row=row, column=0, columnspan=3, sticky="w", pady=1)
        ttk.Label(frame, text=title, width=5).pack(side="left")
        variables = {}
        for name, bit in options:
            var = tk.BooleanVar(value=True)
            ttk.Checkbutton(frame, text=name, variable=var).pack(side="left")
            variables[bit] = var
        return variables

    # -------------------------------------------------- 小工具
    def post(self, func):
        self.events.put(func)

    def _drain(self):
        while True:
            try:
                self.events.get_nowait()()
            except queue.Empty:
                break
            except Exception as exc:
                self.status.set("界面错误：%s" % exc)
        self.after(80, self._drain)

    def _set_status(self, text):
        self.status.set(text)

    def _pick_dir(self):
        chosen = filedialog.askdirectory(initialdir=self.save_dir.get() or DEFAULT_SAVE_DIR)
        if chosen:
            self.save_dir.set(chosen)
            self.cfg["save_dir"] = chosen
            save_config(self.cfg)

    def _open_dir(self):
        path = self.save_dir.get()
        if os.path.isdir(path):
            os.startfile(path)
        else:
            messagebox.showinfo("提示", "目录还不存在：%s" % path)

    def filters_state(self):
        sel = {key: [bit for bit, var in variables.items() if var.get()]
               for key, variables in (("mode", self.mode_vars), ("class", self.class_vars),
                                      ("subtype", self.subtype_vars), ("genre", self.genre_vars),
                                      ("language", self.language_vars))}
        sel["range"] = {}
        for key, (enabled, low_var, high_var, low_def, high_def) in self.range_widgets.items():
            if enabled.get():
                try:
                    sel["range"][key] = (float(low_var.get()), float(high_var.get()))
                except ValueError:
                    sel["range"][key] = (float(low_def), float(high_def))
        return sel

    # -------------------------------------------------- 当前播放预填
    def _prefill(self):
        self._set_status("正在读取当前播放…")

        def work():
            track = now_playing()

            def apply():
                if track:
                    self.keyword.set(track["title"])
                    self._set_status("已填入：%s - %s（%s）"
                                     % (track["artist"], track["title"], track["app"]))
                else:
                    self._set_status("没检测到正在播放的媒体，手动输入即可")
            self.post(apply)

        threading.Thread(target=work, daemon=True).start()

    # -------------------------------------------------- 搜索
    def search(self):
        keyword = self.keyword.get().strip()
        if self.busy:
            return
        self.busy = True
        self.offset = 0
        self.clear_results()
        self._set_status("正在取热门谱面…" if not keyword else "搜索中…")
        self.cfg["save_dir"] = self.save_dir.get()
        save_config(self.cfg)
        thread = threading.Thread(target=self._search_worker,
                                  args=(keyword, self.filters_state()), daemon=True)
        thread.start()

    def load_more(self):
        if self.busy:
            return
        keyword = self.keyword.get().strip()
        self.busy = True
        self._set_status("加载更多…")
        thread = threading.Thread(target=self._search_worker,
                                  args=(keyword, self.filters_state(), self.offset), daemon=True)
        thread.start()

    def _search_worker(self, keyword, sel, offset=0):
        try:
            rows = sayobot_search(keyword, sel, 25, offset)
        except Exception as exc:
            self.busy = False
            self.post(lambda: self._set_status("搜索失败：%s" % exc))
            self.post(lambda: self._show_empty("搜索失败：%s" % exc))
            return
        if not rows:
            self.busy = False
            self.post(lambda: self._set_status("没有匹配的谱面"))
            self.post(lambda: self._show_empty("没有匹配的谱面，换个关键词或放宽筛选试试"))
            return
        self.offset = offset + len(rows)
        self.post(lambda: self._show(rows))
        self.post(lambda: self._set_status("正在补全 Stars / BPM / 时长…"))
        with ThreadPoolExecutor(max_workers=8) as pool:
            details = pool.map(lambda row: sayobot_detail(row["sid"]), rows)
            for row, detail in zip(rows, details):
                row.update(detail or {})
                self.post(lambda row=row: self.fill(row))
        self.busy = False
        self.post(lambda: self._set_status("完成"))

    # -------------------------------------------------- 结果行
    def clear_results(self):
        for child in self.results.winfo_children():
            child.destroy()
        self.row_widgets.clear()
        self.rows = []
        self.sort_key, self.sort_desc = None, False
        self._refresh_head()
        self._hide_empty()
        self.canvas.yview_moveto(0)

    def _hide_empty(self):
        if self.empty_label is not None:
            self.empty_label.place_forget()

    def _show_empty(self, text):
        """没有结果时在列表区域正中提示，别让用户对着空白发呆。"""
        if self.row_widgets:
            return
        if self.empty_label is None:
            self.empty_label = ttk.Label(self.canvas, foreground="#888")
        self.empty_label.config(text=text)
        self.empty_label.place(relx=0.5, rely=0.5, anchor="center")

    def _show(self, rows):
        self._hide_empty()
        self.rows.extend(rows)
        self._render()

    def sort_by(self, key):
        if key == self.sort_key:
            self.sort_desc = not self.sort_desc
        else:
            self.sort_key, self.sort_desc = key, False
        self.rows = sort_rows(self.rows, key, self.sort_desc)
        self._refresh_head()
        self._render()

    def _refresh_head(self):
        for key, text, _width, _get in COLUMNS:
            mark = ""
            if key == self.sort_key:
                mark = " ▼" if self.sort_desc else " ▲"
            self.head_labels[key].config(text=text + mark)

    def _render(self):
        """按 self.rows 重建整个列表（新结果、排序都走这里）。"""
        for child in self.results.winfo_children():
            child.destroy()
        self.row_widgets.clear()
        for row in self.rows:
            line = ttk.Frame(self.results)
            line.pack(fill="x", pady=1)
            widgets = {}
            for key, _text, width, _get in COLUMNS:
                widgets[key] = ttk.Label(line, text="—", width=width, anchor="w")
                widgets[key].pack(side="left")
            widgets["title"].config(text=("%s - %s" % (row["artist"], row["title"]))[:44])
            widgets["button"] = ttk.Button(line, text="下载",
                                           command=lambda r=row: self.download(r))
            widgets["button"].pack(side="right")
            self.row_widgets[row["sid"]] = widgets
            self.fill(row)

    def fill(self, row):
        widgets = self.row_widgets.get(row["sid"])
        if not widgets:
            return
        widgets["creator"].config(text=row["creator"][:18] or "—")
        widgets["modes"].config(text="/".join(MODE_NAME[b] for b in row["mode_bits"]) or "—")
        widgets["stars"].config(text="%.2f" % row["stars"] if row.get("stars") else "—")
        widgets["bpm"].config(text="%g" % row["bpm"] if row.get("bpm") else "—")
        widgets["length"].config(text=fmt_length(row.get("length")))
        widgets["plays"].config(text=_count(row.get("plays")))
        widgets["likes"].config(text=_count(row.get("likes")))

    # -------------------------------------------------- 下载
    def download(self, row):
        save_dir = self.save_dir.get()
        try:
            os.makedirs(save_dir, exist_ok=True)
        except Exception as exc:
            messagebox.showerror("保存路径不可用", str(exc))
            return
        self.cfg["save_dir"] = save_dir
        save_config(self.cfg)
        widgets = self.row_widgets.get(row["sid"])
        if widgets:
            widgets["button"].config(state="disabled")
            widgets["status"].config(text="准备中…")

        def progress(percent):
            self.post(lambda: widgets and widgets["status"].config(text="下载中 %d%%" % percent))

        def work():
            folder, error = download_map(row, save_dir, progress)

            def done():
                if widgets:
                    widgets["button"].config(state="normal")
                    widgets["status"].config(text="完成" if not error else error[:34])
                self._set_status(error or ("已解压到 %s" % folder))
            self.post(done)

        threading.Thread(target=work, daemon=True).start()


def selftest():
    assert safe_name('a/b:c*?"<>|') == "a_b_c" + "_" * 6
    assert safe_name("  ..  ") == "beatmap"
    assert fmt_length(389) == "6:29" and fmt_length(None) == "—"

    sel = {"mode": [1, 8], "class": [1, 2], "subtype": [], "genre": [], "language": [8],
           "range": {"stars": (6.0, 7.0)}}
    body = sayobot_body("xi", sel, limit=5, offset=10)
    assert body == {"cmd": "beatmaplist", "limit": 5, "offset": 10, "type": "search",
                    "keyword": "xi", "mode": 9, "class": 3, "language": 8,
                    "stars": [6.0, 7.0]}, body
    assert sayobot_body("", {}, limit=25) == {"cmd": "beatmaplist", "limit": 25,
                                             "offset": 0, "type": "hot"}

    row = sayobot_row({"sid": 42, "modes": 5, "approved": 4, "title": "t", "artist": "a",
                       "creator": "c"})
    assert row["mode_bits"] == [1, 4], row["mode_bits"]
    assert row["status"] == "loved" and row["sid"] == 42
    assert sayobot_row({"sid": 1, "modes": 0, "approved": 99})["mode_bits"] == []

    rows = [{"sid": 1, "stars": 3.0}, {"sid": 2, "stars": None}, {"sid": 3, "stars": 1.5}]
    assert [r["sid"] for r in sort_rows(rows, "stars")] == [3, 1, 2]
    assert [r["sid"] for r in sort_rows(rows, "stars", True)] == [1, 3, 2]
    assert _count(191495) == "191,495" and _count(None) == "—"

    archive = tempfile.mkdtemp(prefix="osu_map_selftest_")
    try:
        class GbkEntry(zipfile.ZipInfo):
            def _encodeFilenameFlags(self):
                return self.filename.encode("gbk"), 0

        pack = os.path.join(archive, "1.zip")
        with zipfile.ZipFile(pack, "w") as zf:
            zf.writestr(GbkEntry("中文 - 测试.txt"), "ok")
        out = os.path.join(archive, "out")
        with zipfile.ZipFile(pack) as zf:
            fix_legacy_zip_names(zf)
            zf.extractall(out)
        assert os.path.isfile(os.path.join(out, "中文 - 测试.txt"))
    finally:
        shutil.rmtree(archive, ignore_errors=True)
    _say("selftest OK")


def _enable_dpi_awareness():
    """不声明 DPI 感知时 Windows 会按缩放比例拉伸窗口：125% 下 1200x800 实测 1500x1000。"""
    if sys.platform != "win32":
        return
    try:
        import ctypes
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except Exception:
            ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass


def main(argv):
    _enable_dpi_awareness()
    if "--selftest" in argv:
        selftest()
        return 0
    app = App()
    if "--smoke" in argv:
        app.update_idletasks()
        app.update()
        app.destroy()
        _say("smoke OK")
        return 0
    app.mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
