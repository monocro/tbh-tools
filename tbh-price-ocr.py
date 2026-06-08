#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
tbh-price-ocr.py — ゲーム内アイテムの相場をホットキーで表示する常駐ヘルパー

★チート検出されない設計（毎回維持すること）:
  - ゲームプロセスに一切触れない。メモリ読み書き/DLL注入/速度・時計操作を行わない。
  - やるのは「自分の画面の小領域スクショ」+「OCR」+「ホットキー待ち」だけ＝別プロセスで完結。
  - TBHのACTk検出器(ObscuredCheating/SpeedHack/TimeCheat)はどれもゲーム内部の事象しか見ない。
    本ツールはその検出面に一切触れないため、原理的に検出対象外。
  - 軽量・ホットキー押下時のみ稼働＝ゲームをフレーム飢餓させずスピードハック誤検出も誘発しない。

見た目: コンソール無し(pythonw)・タスクトレイ常駐・カード型ポップ。
操作  : アイテムにカーソル→ マウスの「戻る」サイドボタン で価格ポップ / 終了はトレイから
        ※TaskBarHero.exe が前面の時だけ反応。ブラウザ等での「戻る」は普通に効く。
"""
import os, sys, json, threading, queue, traceback, time, webbrowser, urllib.parse, urllib.request, re
import tkinter as tk
from tkinter import font as tkfont
from tkinter import ttk

# ---- 設定 ----------------------------------------------------------------
SIDE_BUTTON   = "x"                # マウスの「戻る」(XBUTTON1)。効かなければ "x2" に変更
GAME_EXE      = "taskbarhero.exe"  # この実行ファイルが前面の時だけ反応
APPID         = "3678970"          # TBH の Steam appid（マーケットURL用）
# 名前枠は位置が毎回変わる→上部ウィンドウ全体を撮り、OCRを行単位＋隣接行ペアで照合して
# どこにあっても名前＋等級を拾う。(左, 上, 右, 下) のゲームウィンドウ比率。
NAME_REGIONS = [
    (0.0, 0.0, 1.0, 0.62),
]
OCR_LANGS     = ["ja", "en"]
POPUP_SECONDS = 6
CALIBRATE     = False              # Trueで撮影画像を保存（調整用）
DEBUG_UI      = False              # Trueで押下毎に「撮影＋枠＋読取＋結果」を1枚のウィンドウ表示（クリックで閉じる窓）
# 配色
C_CARD, C_ACCENT = "#1a1d24", "#2dd4bf"
_KEYCLR = "#ff00fe"   # 角丸の外側を透過させる魔法色（どの配色とも被らない）
C_NAME, C_JA, C_PRICE, C_META, C_ERR = "#ffffff", "#8ab4f8", "#34d399", "#8b909a", "#f87171"
RARITY_COLORS = {"Common": "#c8c8c8", "Uncommon": "#5ce65c", "Rare": "#5b9bff",
                 "Legendary": "#f5a623", "Immortal": "#ff5252", "Arcana": "#c061ff",
                 "Beyond": "#ff5fb0", "Celestial": "#34d6e6", "Divine": "#ffe14d", "Cosmic": "#ff8a5c"}
def rarity_color(r):
    return RARITY_COLORS.get(r, C_ACCENT)
_ui_lang = "ja"                    # 直近に判定したゲーム言語（ja/en）
LBL = {
    "ja": dict(low="最安", med="中央値", lst="出品", sold="売買", quote="相場",
               mkt="クリックでSteamマーケットを開く", noprice="市場価格なし（非取引）",
               nomatch="該当なし", reading="🔍 読み取り中…", read="読取"),
    "en": dict(low="Low", med="Median", lst="List", sold="Sold", quote="Updated",
               mkt="Click to open Steam Market", noprice="Not on market",
               nomatch="No match", reading="🔍 Reading…", read="OCR"),
}
# -------------------------------------------------------------------------

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
LOG = os.path.join(HERE, "error.log")


def log_fatal(msg):
    try:
        with open(LOG, "a", encoding="utf-8") as f:
            f.write(msg + "\n")
    except Exception:
        pass


# ---- 依存 ----------------------------------------------------------------
try:
    import mss
    from PIL import Image, ImageDraw, ImageFilter
    import numpy as np
    import cv2
    import winocr
    import mouse
    import keyboard
    import pystray
    from tbh_price_match import Matcher, RARITIES, norm as _norm
except Exception as e:
    log_fatal("import error:\n" + traceback.format_exc())
    try:
        import tkinter.messagebox as mb
        r = tk.Tk(); r.withdraw()
        mb.showerror("TBH相場OCR", f"必要なライブラリが不足:\n{e}\n\npip install mss pillow winocr mouse keyboard pystray")
    except Exception:
        pass
    sys.exit(1)

import ctypes as _ctypes
try:                                  # DPI対応: GetCursorPos/GetWindowRect を mss と同じ物理座標に揃える
    _ctypes.windll.shcore.SetProcessDpiAwareness(2)
except Exception:
    try: _ctypes.windll.user32.SetProcessDPIAware()
    except Exception: pass

matcher = Matcher(os.path.join(HERE, "tbh-price-lookup.json"))
try:
    _TPL = cv2.imread(os.path.join(HERE, "frame_tpl.png"))   # 名前枠の左角テンプレート（定数ピクセル）
except Exception:
    _TPL = None
PQ = queue.Queue()          # ポップ要求キュー（別スレッド→メインスレッド）


JPY_RATE = 155.0     # USD→JPY。起動時に最新レートへ更新（失敗時はこの値）

def fetch_rate():
    global JPY_RATE
    try:
        import urllib.request
        with urllib.request.urlopen("https://open.er-api.com/v6/latest/USD", timeout=5) as r:
            JPY_RATE = float(json.load(r)["rates"]["JPY"])
    except Exception:
        pass

def price(c):                      # 英語モードは$、日本語モードは¥
    if c is None: return "—"
    if _ui_lang == "en": return f"${c/100:.2f}"
    return f"¥{round(c / 100 * JPY_RATE):,}"


_price_cache = {}                  # hash -> (取得time, low_cents, med_cents, volume)
def live_price(hash_name, force=False):
    """Steamマーケットの現在価格を取得（表示の瞬間に最新を取る）。失敗時None。5分キャッシュ。force=Trueで無視。"""
    if not hash_name: return None
    now = time.time()
    c = _price_cache.get(hash_name)
    if c and not force and now - c[0] < 300:
        return c[1], c[2], c[3]
    try:
        url = (f"https://steamcommunity.com/market/priceoverview/?appid={APPID}"
               f"&currency=1&market_hash_name=" + urllib.parse.quote(hash_name))
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        d = json.load(urllib.request.urlopen(req, timeout=6))
        def cents(s):
            if not s: return None
            m = re.search(r"[\d,.]+", s)
            return round(float(m.group().replace(",", "")) * 100) if m else None
        low, med, vol = cents(d.get("lowest_price")), cents(d.get("median_price")), d.get("volume")
        if low is None and med is None: return None
        _price_cache[hash_name] = (now, low, med, vol)
        return low, med, vol
    except Exception:
        return None


# ---- 前面ウィンドウ判定（ゲームが前面の時だけ反応） ----------------------
def foreground_exe():
    import ctypes
    from ctypes import wintypes
    try:
        hwnd = ctypes.windll.user32.GetForegroundWindow()
        pid = wintypes.DWORD()
        ctypes.windll.user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        h = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)  # QUERY_LIMITED_INFORMATION
        if not h:
            return ""
        buf = ctypes.create_unicode_buffer(1024)
        size = wintypes.DWORD(1024)
        ctypes.windll.kernel32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size))
        ctypes.windll.kernel32.CloseHandle(h)
        return os.path.basename(buf.value).lower()
    except Exception:
        return ""


# ---- 撮影 & OCR ----------------------------------------------------------
def cursor_pos():
    import ctypes
    from ctypes import wintypes
    pt = wintypes.POINT()
    ctypes.windll.user32.GetCursorPos(ctypes.byref(pt))
    return pt.x, pt.y


def grab(frac):
    """ゲームウィンドウ基準で frac=(左,上,右,下)比率の領域を撮る。戻り: (画像, (画面左, 画面上))。"""
    import ctypes
    from ctypes import wintypes
    hwnd = ctypes.windll.user32.GetForegroundWindow()
    r = wintypes.RECT()
    ctypes.windll.user32.GetWindowRect(hwnd, ctypes.byref(r))
    W, H = r.right - r.left, r.bottom - r.top
    x0, y0, x1, y1 = frac
    left, top = r.left + int(W * x0), r.top + int(H * y0)
    region = {"left": left, "top": top,
              "width": max(1, int(W * (x1 - x0))), "height": max(1, int(H * (y1 - y0)))}
    with mss.mss() as sct:
        raw = sct.grab(region)
    return Image.frombytes("RGB", raw.size, raw.bgra, "raw", "BGRX"), (left, top)


def _adapt(c):
    """局所適応二値化（色付き/暗い名前も白黒高コントラスト化）。"""
    v = c.convert("HSV").split()[2]
    mean = v.filter(ImageFilter.BoxBlur(14))
    a = np.asarray(v, dtype=np.int16); m = np.asarray(mean, dtype=np.int16)
    return Image.fromarray(((a > m + 8) * 255).astype("uint8"), "L").convert("RGB")


def _ocr(c):
    proc = _adapt(c)
    out = []
    for lang in ("ja", "en"):          # 日本語・英語どちらの表示でも読めるよう両方
        try:
            r = winocr.recognize_pil_sync(proc, lang)
            out.append(" ".join(l.get("text", "") for l in (r.get("lines") if isinstance(r, dict) else []) or []))
        except Exception:
            pass
    return "\n".join(out)   # ja/en読みは改行区切り＝行ごとに照合（二重化での薄まりを防ぐ）


def detect_boxes(img):
    """名前枠テンプレートで枠を位置特定し、各枠の (名前＋等級テキスト, 枠中心x, 中心y) を返す。
    枠は毎回同じピクセル＝位置が左右・上下に動いてもテンプレートマッチで見つかる。"""
    if _TPL is None:
        return []
    arr = cv2.cvtColor(np.array(img), cv2.COLOR_RGB2BGR)
    res = cv2.matchTemplate(arr, _TPL, cv2.TM_CCOEFF_NORMED)
    # 閾値は低め＝枠を取りこぼさない。誤検出はマッチャの確信0.85で除外される。
    ys, xs = np.where(res >= 0.62)
    peaks = sorted(zip(xs.tolist(), ys.tolist(), res[ys, xs].tolist()), key=lambda p: -p[2])
    picked = []
    for x, y, s in peaks:                       # 同じ枠の重複ピークをまとめる（高スコア順なので最良が残る）
        if all(abs(x - px) > 420 or abs(y - py) > 36 for px, py, _ in picked):
            picked.append((x, y, s))
        if len(picked) >= 10:
            break
    out = []
    for x, y, s in picked:
        name = _ocr(img.crop((max(0, x - 90), y + 6, x + 560, y + 56)))   # 枠内＝名前（左に広め＝短名対策）
        rank = _ocr(img.crop((max(0, x - 90), y + 56, x + 560, y + 122))) # 枠直下＝等級
        out.append((name, rank, x, y, s))   # 枠の左上座標とテンプレ一致度も返す
    return out


def _annotate(img, boxes, cands, chosen, xy, off):
    """デバッグ用: 撮影画像に 検出枠・読取・マッチ結果・カーソル を描いて縮小して返す。"""
    from PIL import ImageDraw, ImageFont
    ox, oy = off
    im = img.convert("RGB").copy()
    d = ImageDraw.Draw(im)
    try:
        fnt = ImageFont.truetype("YuGothM.ttc", 22); fbig = ImageFont.truetype("YuGothB.ttc", 30)
    except Exception:
        fnt = ImageFont.load_default(); fbig = fnt
    for name, rank, bx, by, sc_t in boxes:
        d.rectangle([bx - 90, by + 6, bx + 560, by + 56], outline=(0, 255, 255), width=3)
        d.rectangle([bx - 90, by + 56, bx + 560, by + 122], outline=(0, 160, 255), width=2)
        d.text((bx - 88, by - 26), f"枠 t={sc_t:.2f} 名[{name}] 級[{rank}]", fill=(255, 255, 0), font=fnt)
    for c in cands:
        sc, d2, sx, sy, r, bx, by, name, rank = c
        e = r[0]
        col = (0, 255, 0) if sc >= 0.85 else (255, 120, 120)
        d.text((bx - 88, by + 124), f"= {e.get('ja','')}({e.get('rarity_ja','')}) s={sc} d={int(d2**0.5)}", fill=col, font=fnt)
    if chosen:
        bx, by = chosen[5], chosen[6]
        d.rectangle([bx - 94, by + 2, bx + 564, by + 126], outline=(0, 255, 0), width=6)
    cx, cy = xy[0] - ox, xy[1] - oy
    d.line([cx - 24, cy, cx + 24, cy], fill=(255, 0, 0), width=3)
    d.line([cx, cy - 24, cx, cy + 24], fill=(255, 0, 0), width=3)
    d.ellipse([cx - 16, cy - 16, cx + 16, cy + 16], outline=(255, 0, 0), width=3)
    res = chosen[4][0] if chosen else None
    if res and res.get("sell") is not None:
        head = f"-> {res.get('ja','')}({res.get('rarity_ja','')}) Y{round((res.get('sell') or 0)/100*JPY_RATE):,}"
    elif res:
        head = f"-> {res.get('ja','')}({res.get('rarity_ja','')}) 市場価格なし"
    else:
        head = "-> 該当なし"
    d.rectangle([0, 0, im.width, 48], fill=(0, 0, 0))
    d.text((10, 8), f"枠数={len(boxes)}  {head}", fill=(255, 255, 255), font=fbig)
    W = 1100
    return im.resize((W, max(1, int(im.height * W / im.width))), Image.LANCZOS)


_dbg_win = [None]
def show_debug(pim, root):
    from PIL import ImageTk
    if _dbg_win[0] is not None:
        try: _dbg_win[0].destroy()
        except Exception: pass
    win = tk.Toplevel(root); win.title("TBH OCR デバッグ")
    win.attributes("-topmost", True)
    ph = ImageTk.PhotoImage(pim)
    lb = tk.Label(win, image=ph, bg="#000"); lb.image = ph; lb.pack()
    win.bind("<Button-1>", lambda e: win.destroy())
    win.geometry("+20+20")
    _dbg_win[0] = win


WORKQ = queue.Queue()    # 戻るボタン押下シグナル（常駐ワーカーが処理）

def ocr_worker():
    """常駐1本のワーカー: OCRエンジンを一度だけ初期化(COM/winrtのスレッド親和性対策)し、
    押下シグナルごとに 撮影→OCR→照合 を直列実行する。"""
    try:
        winocr.recognize_pil_sync(Image.new("RGB", (48, 48)), "ja")   # ウォームアップ
    except Exception:
        pass
    while True:
        WORKQ.get()
        try:
            while True: WORKQ.get_nowait()      # 連打はまとめて1回に
        except queue.Empty:
            pass
        try:
            if foreground_exe() != GAME_EXE:
                continue                        # 他アプリでは何もしない＝「戻る」は普通に効く
            xy = cursor_pos()
            PQ.put(("__close__", None, None))       # ① 自分の古いポップ・デバッグ窓を消す（撮らないため）
            time.sleep(0.13)
            img, (ox, oy) = grab(NAME_REGIONS[0])   # ② 撮影
            PQ.put(("__processing__", xy, None))    # ③ 読み取り中
            if CALIBRATE:
                try: img.save(os.path.join(HERE, "cap0.png"))
                except Exception: pass
            boxes = detect_boxes(img)            # 枠テンプレートで名前枠を位置特定→各枠OCR
            cands = []
            for name, rank, bx, by, sc_t in boxes:
                best_r = matcher.match_item(name, rank)   # 名前で特定＋等級行から正しい等級を補う
                cx, cy = bx + 250, by + 30
                if best_r:
                    sx, sy = ox + cx, oy + cy
                    d2 = (sx - xy[0]) ** 2 + (sy - xy[1]) ** 2
                    cands.append((best_r[0]["score"], d2, sx, sy, best_r, bx, by, name, rank))
            # ゲーム言語判定（全枠OCRのASCII/日本語比率）
            global _ui_lang
            alltext = " ".join((n or "") + (r or "") for n, r, *_ in boxes)
            asc = sum(1 for c in alltext if c.isascii() and c.isalpha())
            jpn = sum(1 for c in alltext if ord(c) > 0x3040)
            _ui_lang = "en" if asc > jpn else "ja"
            found, chosen = [], None
            if cands:
                ax, ay = min(cands, key=lambda c: c[1])[2:4]   # カーソル最近の枠＝指してる位置
                same = [c for c in cands if (c[2] - ax) ** 2 + (c[3] - ay) ** 2 < 80 ** 2]
                best = max(same, key=lambda c: c[0])
                if best[0] >= 0.85:
                    found, chosen = best[4], best
            if found:                             # 表示の瞬間にSteamの現在価格を取得（常に最新）
                lp = live_price(found[0].get("hash"))
                if lp:
                    low, med, vol = lp
                    if low is not None: found[0]["sell"] = low
                    if med is not None: found[0]["median"] = med
                    if vol is not None: found[0]["volume"] = vol
                    found[0]["_live"] = True
                _record_history(found[0])         # 履歴に記録
            if CALIBRATE:                         # 失敗時の画像とログを残す（私が原因を見る用）
                try:
                    with open(os.path.join(HERE, "ocr-text.txt"), "w", encoding="utf-8") as f:
                        f.write(f"lang={_ui_lang} cursor={xy} off=({ox},{oy}) 枠数={len(boxes)} 結果={'OK' if found else 'なし'}\n")
                        for n, r, bx, by, st in boxes:
                            f.write(f" 枠@({bx},{by}) t={st:.2f} 名[{(n or '')[:26]}] 級[{(r or '')[:16]}]\n")
                        for c in sorted(cands, key=lambda c: c[1]):
                            f.write(f" 候補 {c[4][0].get('en')} / {c[4][0].get('ja')} s={c[0]} d={int(c[1]**.5)}\n")
                    if not found:
                        img.save(os.path.join(HERE, "fail.png"))
                        import shutil; shutil.copy(os.path.join(HERE, "ocr-text.txt"), os.path.join(HERE, "fail.txt"))
                except Exception:
                    pass
            if DEBUG_UI:
                try:
                    PQ.put(("__debug__", _annotate(img, boxes, cands, chosen, xy, (ox, oy)), None))
                except Exception:
                    log_fatal("annotate:\n" + traceback.format_exc())
            hint = ""                              # カーソル最近枠の読取生テキスト（候補選び直し用）
            if boxes:
                bb = min(boxes, key=lambda b: (ox + b[2] + 250 - xy[0]) ** 2 + (oy + b[3] + 30 - xy[1]) ** 2)
                hint = ((bb[0] or "") + " " + (bb[1] or "")).strip()
            PQ.put((found, xy, hint))
        except Exception:
            log_fatal("worker error:\n" + traceback.format_exc())


# ---- ポップ表示（メインスレッドで） --------------------------------------
_open = []
_hist = []                 # 価格履歴（新しい順）
_hist_win = [None]         # 履歴ウィンドウ
_hist_inner = [None]       # (canvas, inner) の参照
_hist_visible = [False]    # トレイのオン/オフ状態
_hist_limit = [50]         # 履歴の上限（0=無制限）。お気に入りは上限の対象外
_hist_status = [None]      # ヘッダの「更新中/更新時刻」ラベル
HIST_FILE = os.path.join(HERE, "tbh-price-history.json")   # 履歴の保存先（再起動で消えないように）
SET_FILE = os.path.join(HERE, "tbh-price-settings.json")   # 設定の保存先

# ---- 発動トリガー（マウスボタン/キーボード、ユーザーが自由に割り当て） ----
_trigger = {"kind": "mouse", "value": SIDE_BUTTON}   # 既定：マウス戻る(サイド)
_trig_hook = [None]                                  # (kind, handler) 解除用
_set_win = [None]                                    # 設定ウィンドウ

_MOUSE_LBL = {"x": "マウス サイド(戻る)", "x2": "マウス サイド(進む)", "middle": "マウス 中ボタン",
              "left": "マウス 左", "right": "マウス 右"}

def _trigger_label(kind=None, value=None):
    kind = kind or _trigger["kind"]; value = value if value is not None else _trigger["value"]
    if kind == "mouse":
        return _MOUSE_LBL.get(value, "マウス " + str(value))
    return " + ".join(p.capitalize() for p in str(value).split("+"))   # ctrl+shift+p → Ctrl + Shift + P

def _save_settings():
    try:
        with open(SET_FILE, "w", encoding="utf-8") as f:
            json.dump({"trigger": _trigger}, f, ensure_ascii=False)
    except Exception: pass

def _load_settings():
    try:
        d = json.load(open(SET_FILE, encoding="utf-8"))
        t = d.get("trigger") or {}
        if t.get("kind") in ("mouse", "key") and t.get("value"):
            _trigger.update(kind=t["kind"], value=t["value"])
    except Exception: pass

def _bind_trigger():
    """現在の_triggerでWORKQ発火をフック。既存フックは外す。"""
    if _trig_hook[0]:
        kind, h = _trig_hook[0]
        try:
            if kind == "mouse": mouse.unhook(h)
            else: keyboard.remove_hotkey(h)
        except Exception: pass
        _trig_hook[0] = None
    kind, val = _trigger["kind"], _trigger["value"]
    try:
        if kind == "mouse":
            h = mouse.on_button(lambda: WORKQ.put(1), buttons=(val,), types=("down",))
            _trig_hook[0] = ("mouse", h)
        else:
            h = keyboard.add_hotkey(val, lambda: WORKQ.put(1))
            _trig_hook[0] = ("key", h)
    except Exception:
        log_fatal("bind_trigger:\n" + traceback.format_exc())

def _capture_trigger(on_done):
    """キーは「押している組み合わせ」を最初に離した瞬間に確定（Ctrl+Shift+P等）。
    単キー(F8)も、マウスボタンも可。on_done(kind, value) を呼ぶ。"""
    pressed = []           # 押された順のキー名（重複なし）
    state = {"done": False}
    def finish(kind, value):
        if state["done"]: return
        state["done"] = True
        try: mouse.unhook(mh)
        except Exception: pass
        try: keyboard.unhook(kh)
        except Exception: pass
        on_done(kind, value)
    def on_mouse(e):
        if isinstance(e, mouse.ButtonEvent) and e.event_type == "down":
            finish("mouse", e.button)
    def on_key(e):
        if e.event_type == "down":
            if e.name and e.name not in pressed: pressed.append(e.name)
        elif e.event_type == "up" and pressed:   # 最初に離した瞬間に組み合わせを確定
            finish("key", "+".join(pressed))
    mh = mouse.hook(on_mouse)
    kh = keyboard.hook(on_key)

def _save_hist():
    try:
        with open(HIST_FILE, "w", encoding="utf-8") as f:
            json.dump({"limit": _hist_limit[0], "hist": _hist}, f, ensure_ascii=False)
    except Exception: pass

def _load_hist():
    try:
        d = json.load(open(HIST_FILE, encoding="utf-8"))
        _hist[:] = d.get("hist", []) or []
        if isinstance(d.get("limit"), int): _hist_limit[0] = d["limit"]
    except Exception: pass

def _hist_trim():
    lim = _hist_limit[0]
    if lim <= 0 or len(_hist) <= lim: return
    favs = sum(1 for r in _hist if r.get("fav"))
    keep_nonfav = max(0, lim - favs)
    seen = 0; out = []
    for r in _hist:                       # 新しい順。お気に入りは常に残し、非お気に入りは上限まで
        if r.get("fav"): out.append(r)
        elif seen < keep_nonfav: out.append(r); seen += 1
    _hist[:] = out

def _record_history(ent):
    if not ent: return
    rec = {k: ent.get(k) for k in ("ja", "en", "rarity_en", "rarity_ja", "sell", "median",
                                   "volume", "hash", "type_ja", "type_en", "type")}
    rec["ts"] = time.strftime("%H:%M")
    if _hist and _hist[0].get("hash") == rec["hash"]:   # 直近と同一なら時刻だけ更新（fav保持）
        rec["fav"] = _hist[0].get("fav"); _hist[0] = rec
    else:
        _hist.insert(0, rec); _hist_trim()
    _save_hist()

def _round_corners(win):
    try:
        import ctypes
        win.update_idletasks()
        hwnd = ctypes.windll.user32.GetParent(win.winfo_id()) or win.winfo_id()
        ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, 33, ctypes.byref(ctypes.c_int(2)), 4)
    except Exception:
        pass

def _top_hwnd(win):
    """Tkウィンドウの本当のトップレベルHWNDを返す（overrideredirectは子HWNDが返るためGA_ROOTで解決）。"""
    import ctypes
    h = win.winfo_id()
    r = ctypes.windll.user32.GetAncestor(h, 2)   # GA_ROOT
    return r or ctypes.windll.user32.GetParent(h) or h

def _grab_foreground(win):
    """テキスト編集のため前面フォーカスを取る。NOACTIVATEを一時解除し、フォアグラウンドロックを
    AttachThreadInputで回避してSetForegroundWindow。ボーダーレスのゲームは最小化しない。"""
    try:
        import ctypes
        u = ctypes.windll.user32; k = ctypes.windll.kernel32
        h = _top_hwnd(win)
        ex = u.GetWindowLongW(h, -20)
        u.SetWindowLongW(h, -20, ex & ~0x08000000)   # WS_EX_NOACTIVATE off
        fg = u.GetForegroundWindow()
        ft = u.GetWindowThreadProcessId(fg, None)
        ct = k.GetCurrentThreadId()
        if ft and ft != ct: u.AttachThreadInput(ct, ft, True)
        u.SetForegroundWindow(h); u.BringWindowToTop(h)
        if ft and ft != ct: u.AttachThreadInput(ct, ft, False)
    except Exception: pass

def _keep_on_top(win, want_noact=lambda: True):
    """フルスクリーン(ボーダーレス)のゲームの前へ出し続ける。要点は WS_EX_NOACTIVATE:
    これを付けるとポップをクリックしてもアクティブ化が起きない＝ゲームが前面に出てこない。
    ただし編集中(want_noact()=False)は外してキーボード入力を受けられるようにする。
    TOPMOSTは常に維持し、120ms毎に再主張して背後への回り込みを防ぐ。"""
    try: import ctypes
    except Exception: return
    u = ctypes.windll.user32
    GWL_EXSTYLE = -20
    WS_EX_TOPMOST, WS_EX_NOACTIVATE = 0x00000008, 0x08000000
    HWND_TOPMOST = -1
    SWP = 0x0001 | 0x0002 | 0x0010   # NOSIZE | NOMOVE | NOACTIVATE
    def tick():
        if not win.winfo_exists(): return
        try:
            h = _top_hwnd(win)
            ex = u.GetWindowLongW(h, GWL_EXSTYLE)
            if want_noact():
                want = ex | WS_EX_TOPMOST | WS_EX_NOACTIVATE
            else:
                want = (ex | WS_EX_TOPMOST) & ~WS_EX_NOACTIVATE
            if want != ex:
                u.SetWindowLongW(h, GWL_EXSTYLE, want)
            u.SetWindowPos(h, HWND_TOPMOST, 0, 0, 0, 0, SWP)
        except Exception: pass
        win.after(120, tick)
    tick()

def _dismiss(win, is_editing=lambda: False):
    """ポップの閉じ方のマナー: ①カーソルが一度乗ってから外れて0.7秒で閉じる(ホバーアウト)
    ②UI外を左クリックで即閉じ(ライトディスミス) ③一度も乗らなければ8秒で自動消滅。
    メニュー展開中(grab)・テキスト編集中(is_editing)は判定を止める。"""
    try: import ctypes
    except Exception: return
    u = ctypes.windll.user32
    class _PT(ctypes.Structure): _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]
    s = {"entered": False, "out": 0, "age": 0}
    def tick():
        if not win.winfo_exists(): return
        s["age"] += 1
        try:
            if win.grab_current() or is_editing():   # メニュー展開中/編集中は何もしない
                s["out"] = 0; win.after(80, tick); return
        except Exception: pass
        try:
            pt = _PT(); u.GetCursorPos(ctypes.byref(pt))
            x, y, w, h = win.winfo_rootx(), win.winfo_rooty(), win.winfo_width(), win.winfo_height()
            m = 10
            inside = (x - m) <= pt.x <= (x + w + m) and (y - m) <= pt.y <= (y + h + m)
            lbtn = u.GetAsyncKeyState(0x01) & 0x8000
        except Exception:
            win.after(80, tick); return
        if inside:
            s["entered"] = True; s["out"] = 0
        elif lbtn:                            # UI外を左クリック→即閉じ
            win.destroy(); return
        elif s["entered"]:                    # 乗ってから外れた→0.7秒で閉じ
            s["out"] += 1
            if s["out"] * 0.08 >= 0.7: win.destroy(); return
        elif s["age"] * 0.08 >= 8:            # 一度も乗らず8秒→自動消滅
            win.destroy(); return
        win.after(80, tick)
    win.after(80, tick)

def round_pill(parent, text, fill, fg, cmd, font, padx=14, pady=6):
    """角丸（ピル型）ボタン。canvasで描画。"""
    tw, th = font.measure(text), font.metrics("linespace")
    w, h = tw + padx * 2, th + pady * 2
    cv = tk.Canvas(parent, width=w, height=h, bg=parent.cget("bg"), highlightthickness=0, cursor="hand2")
    cv.create_arc(0, 0, h, h, start=90, extent=180, fill=fill, outline=fill, tags="bg")
    cv.create_arc(w - h, 0, w, h, start=-90, extent=180, fill=fill, outline=fill, tags="bg")
    cv.create_rectangle(h / 2, 0, w - h / 2, h, fill=fill, outline=fill, tags="bg")
    cv.create_text(w / 2, h / 2 + 1, text=text, fill=fg, font=font, tags="txt")
    cv.bind("<Button-1>", lambda e: cmd())
    return cv

def recolor_pill(cv, color):
    try: cv.itemconfig("bg", fill=color, outline=color)
    except Exception: pass

def _rrect(cv, x1, y1, x2, y2, r, fill, tag):
    """canvasに角丸矩形を描く（四隅arc＋十字rect）。"""
    cv.create_arc(x1, y1, x1 + 2*r, y1 + 2*r, start=90, extent=90, fill=fill, outline=fill, tags=tag)
    cv.create_arc(x2 - 2*r, y1, x2, y1 + 2*r, start=0, extent=90, fill=fill, outline=fill, tags=tag)
    cv.create_arc(x1, y2 - 2*r, x1 + 2*r, y2, start=180, extent=90, fill=fill, outline=fill, tags=tag)
    cv.create_arc(x2 - 2*r, y2 - 2*r, x2, y2, start=270, extent=90, fill=fill, outline=fill, tags=tag)
    cv.create_rectangle(x1 + r, y1, x2 - r, y2, fill=fill, outline=fill, tags=tag)
    cv.create_rectangle(x1, y1 + r, x2, y2 - r, fill=fill, outline=fill, tags=tag)

def _place(win, xy):
    win.update_idletasks()
    sw, sh = win.winfo_screenwidth(), win.winfo_screenheight()
    w, h = win.winfo_width(), win.winfo_height()
    win.geometry(f"+{min(max(8, xy[0]+24), sw-w-8)}+{min(max(8, xy[1]+24), sh-h-8)}")

def show_popup(results, xy, text, root):
    for w in _open[:]:
        try: w.destroy()
        except Exception: pass
        _open.remove(w)
    lb = LBL.get(_ui_lang, LBL["ja"])
    win = tk.Toplevel(root)
    win.overrideredirect(True); win.attributes("-topmost", True); win.config(bg=C_CARD)
    f_name = tkfont.Font(family="Yu Gothic UI", size=14, weight="bold")
    f_price = tkfont.Font(family="Yu Gothic UI", size=17, weight="bold")
    f_meta = tkfont.Font(family="Yu Gothic UI", size=9)

    if results == "__processing__":
        b = tk.Frame(win, bg=C_ACCENT); b.pack()
        c = tk.Frame(b, bg=C_CARD); c.pack(padx=3, pady=3)
        tk.Label(c, text=lb["reading"], bg=C_CARD, fg=C_ACCENT, font=f_name, padx=18, pady=12).pack()
        _place(win, xy); _round_corners(win); _keep_on_top(win); _open.append(win)
        win.after(int(POPUP_SECONDS * 1000), lambda: (win.winfo_exists() and win.destroy()))
        return

    e = results[0] if results else None
    init_name = (e.get("en") if _ui_lang == "en" else e.get("ja")) if e else (text or "").strip()
    init_rar = (e.get("rarity_en") if e else "") or ""
    en2ja = {en: ja for en, ja in RARITIES}
    state = {"entry": e, "rarity": init_rar}

    content = tk.Frame(win, bg=C_CARD); content.pack()   # 枠なし（ダークカードのみ）
    content.columnconfigure(0, weight=1)

    # アイテム名：読むだけのプレーンテキスト（編集は前面を奪うので不可。等級はマウスで選び直し可）
    name_lbl = tk.Label(content, text=init_name or "—", bg=C_CARD, fg=C_NAME, font=f_name, anchor="w")
    name_lbl.grid(row=0, column=0, sticky="we", padx=14, pady=(14, 6))

    rar_holder = tk.Frame(content, bg=C_CARD); rar_holder.grid(row=1, column=0, sticky="w", padx=14, pady=2)
    rar_menu = tk.Menu(win, tearoff=0, bg="#0d1016", fg=C_NAME, activebackground="#2a2f3a",
                       activeforeground="#ffffff", bd=0, relief="flat")
    for en, ja in RARITIES:
        rar_menu.add_command(label=(ja if _ui_lang == "ja" else en), foreground=rarity_color(en),
                             command=lambda en=en: set_rarity(en))
    _rp = {"w": None}
    def build_rar_pill():
        if _rp["w"]: _rp["w"].destroy()
        r = state["rarity"]
        txt = "▾ " + ((en2ja.get(r, r) if _ui_lang == "ja" else r) if r else ("等級" if _ui_lang == "ja" else "Rarity"))
        p = round_pill(rar_holder, txt, rarity_color(r), "#0c0c0c",
                       lambda: rar_menu.tk_popup(p.winfo_rootx(), p.winfo_rooty() + p.winfo_height()), f_meta)
        p.pack(anchor="w"); _rp["w"] = p

    price_lbl = tk.Label(content, text="", bg=C_CARD, font=f_price, anchor="w")
    price_lbl.grid(row=2, column=0, sticky="we", padx=14, pady=(8, 2))
    meta_lbl = tk.Label(content, text="", bg=C_CARD, fg=C_META, font=f_meta, anchor="w")
    meta_lbl.grid(row=3, column=0, sticky="we", padx=14, pady=(0, 10))

    btnf = tk.Frame(content, bg=C_CARD); btnf.grid(row=4, column=0, sticky="we", padx=14, pady=(0, 14))
    def open_market():
        ent = state["entry"]
        if ent and ent.get("hash"):
            try: webbrowser.open(f"https://steamcommunity.com/market/listings/{APPID}/" + urllib.parse.quote(ent["hash"]))
            except Exception: pass
    mkt_pill = round_pill(btnf, "🛒 " + lb["mkt"], rarity_color(init_rar), "#0c0c0c", open_market, f_meta)
    mkt_pill.pack(side="left")
    def open_history():
        _hist_visible[0] = True; show_history(root)
    round_pill(btnf, "🕘 " + ("履歴" if _ui_lang == "ja" else "History"),
               "#2a2f3a", C_NAME, open_history, f_meta).pack(side="left", padx=(6, 0))
    round_pill(btnf, "✕", "#2a2f3a", C_NAME, win.destroy, f_meta, padx=12).pack(side="right")

    def render(ent):
        state["entry"] = ent
        ar = rarity_color(state["rarity"] or (ent.get("rarity_en") if ent else ""))
        price_lbl.config(fg=ar); recolor_pill(mkt_pill, ar)
        if ent:
            name_lbl.config(text=(ent.get("en") if _ui_lang == "en" else ent.get("ja"))
                            or ent.get("en") or ent.get("ja") or "—")
        if ent and ent.get("sell") is not None:
            price_lbl.config(text=f"{lb['low']} {price(ent['sell'])}   {lb['med']} {price(ent['median'])}")
            cat = ent.get("type_en" if _ui_lang == "en" else "type_ja") or ent.get("type", "")
            meta_lbl.config(text=f"{cat}   {lb['sold']}{ent.get('volume','—')}")
        elif ent:
            price_lbl.config(text=lb["noprice"]); meta_lbl.config(text=ent.get("type_ja", "") or ent.get("type_en", ""))
        else:
            price_lbl.config(text=lb["nomatch"]); meta_lbl.config(text="")
        _place(win, xy)

    def _lookup(nm, rar_en):                        # 名前＋等級で引き直し→現在価格を取得→描画
        def work():
            r = matcher.match_item(nm, en2ja.get(rar_en, rar_en) if rar_en else "")
            ent = r[0] if r else None
            if ent:
                lp = live_price(ent.get("hash"))
                if lp:
                    low, med, vol = lp
                    if low is not None: ent["sell"] = low
                    if med is not None: ent["median"] = med
                    if vol is not None: ent["volume"] = vol
            win.after(0, lambda: render(ent))
        threading.Thread(target=work, daemon=True).start()

    def set_rarity(en):                             # 等級を選び直し→同名×新等級で引き直し
        state["rarity"] = en; build_rar_pill()
        cur = state["entry"]
        nm = ((cur.get("ja") or cur.get("en")) if cur else (text or "")).strip()
        _lookup(nm, en)

    build_rar_pill()
    win.bind("<Escape>", lambda ev: win.destroy())

    render(e)
    _round_corners(win)        # Win11のOS角丸（透過なし＝クリックで消えない）
    _keep_on_top(win)          # ゲームの前へ（NOACTIVATE維持＝クリックで後ろに行かない）
    _dismiss(win)              # 外側クリック/ホバーアウト/無操作で閉じるマナー
    _open.append(win)


def _hist_after(fn):
    w = _hist_win[0]
    if w and w.winfo_exists():
        try: w.after(0, fn)
        except Exception: pass

def _hist_delete(rec):
    try: _hist.remove(rec)
    except ValueError: pass
    _save_hist(); _refresh_history()

def _hist_fav(rec):
    rec["fav"] = not rec.get("fav"); _save_hist(); _refresh_history()

def _hist_apply(rec, name, rarity_en):
    """name+等級で再照合し、recを新データに置換（fav/tsは保持）。価格も取得。"""
    fav, ts = rec.get("fav"), rec.get("ts")
    rmap = {en: ja for en, ja in RARITIES}
    def work():
        r = matcher.match_item(name, rmap.get(rarity_en, rarity_en) if rarity_en else "")
        ent = r[0] if r else None
        if ent:
            lp = live_price(ent.get("hash"), force=True)
            if lp:
                low, med, vol = lp
                if low is not None: ent["sell"] = low
                if med is not None: ent["median"] = med
                if vol is not None: ent["volume"] = vol
            new = {k: ent.get(k) for k in ("ja", "en", "rarity_en", "rarity_ja", "sell", "median",
                                           "volume", "hash", "type_ja", "type_en", "type")}
            new["fav"], new["ts"] = fav, ts
            if rec in _hist: _hist[_hist.index(rec)] = new
            _save_hist()
        _hist_after(_refresh_history)
    threading.Thread(target=work, daemon=True).start()

def _hist_set_rarity(rec, en):
    _hist_apply(rec, rec.get("ja") or rec.get("en") or "", en)

def _hist_rename(rec):
    def on_ok(s):
        if s: _hist_apply(rec, s, rec.get("rarity_en"))
    _ask_text("アイテム名変更" if _ui_lang == "ja" else "Rename item",
              rec.get("ja") or rec.get("en") or "", on_ok)

def _hist_update_all():
    recs = list(_hist)
    total = sum(1 for r in recs if r.get("hash"))
    def setstat(txt):
        def _s():
            if _hist_status[0] and _hist_status[0].winfo_exists():
                _hist_status[0].config(text=txt)
        _hist_after(_s)
    def work():
        n = 0
        setstat(("更新中 0/%d…" % total) if _ui_lang == "ja" else "Updating 0/%d…" % total)
        for rec in recs:
            h = rec.get("hash")
            if not h: continue
            lp = live_price(h, force=True)
            if lp:
                low, med, vol = lp
                if low is not None: rec["sell"] = low
                if med is not None: rec["median"] = med
                if vol is not None: rec["volume"] = vol
            n += 1
            setstat(("更新中 %d/%d…" % (n, total)) if _ui_lang == "ja" else "Updating %d/%d…" % (n, total))
            _hist_after(_refresh_history)         # 1件ずつ反映＝変化が見える
            time.sleep(0.3)                        # Steamのレート制限回避
        _save_hist()
        done = time.strftime("%H:%M:%S")
        setstat((f"更新 {done}") if _ui_lang == "ja" else f"Updated {done}")
        _hist_after(_refresh_history)
    threading.Thread(target=work, daemon=True).start()

def _ask_text(title, initial, on_ok):
    """履歴上で使う小さな入力ダイアログ（前面フォーカスを取って打てる）。"""
    parent = _hist_win[0]
    d = tk.Toplevel(parent); d.title(title); d.config(bg=C_CARD); d.attributes("-topmost", True)
    f = tkfont.Font(family="Yu Gothic UI", size=11)
    tk.Label(d, text=title, bg=C_CARD, fg=C_NAME, font=f, anchor="w").pack(fill="x", padx=14, pady=(12, 4))
    var = tk.StringVar(value=initial)
    ent = tk.Entry(d, textvariable=var, font=f, bg="#0d1016", fg=C_NAME,
                   insertbackground=C_NAME, relief="flat", width=26)
    ent.pack(padx=14, pady=4, ipady=4, ipadx=4)
    def ok(*_):
        on_ok(var.get().strip()); d.destroy()
    bf = tk.Frame(d, bg=C_CARD); bf.pack(padx=14, pady=(6, 12), fill="x")
    round_pill(bf, "OK", C_ACCENT, "#0c0c0c", ok, f).pack(side="right", padx=(6, 0))
    round_pill(bf, "キャンセル" if _ui_lang == "ja" else "Cancel", "#2a2f3a", C_NAME, d.destroy, f).pack(side="right")
    d.bind("<Return>", ok); d.bind("<Escape>", lambda e: d.destroy())
    _grab_foreground(d)
    ent.focus_set()
    try: ent.select_range(0, "end"); ent.icursor("end")
    except Exception: pass

def _row_menu(ev, rec):
    m = tk.Menu(_hist_win[0], tearoff=0, bg="#0d1016", fg=C_NAME, activebackground="#2a2f3a",
                activeforeground="#ffffff", bd=0)
    ja = _ui_lang == "ja"
    m.add_command(label=("★ お気に入り解除" if rec.get("fav") else "☆ お気に入り") if ja
                  else ("★ Unfavorite" if rec.get("fav") else "☆ Favorite"),
                  command=lambda: _hist_fav(rec))
    m.add_command(label="アイテム名変更" if ja else "Rename", command=lambda: _hist_rename(rec))
    rm = tk.Menu(m, tearoff=0, bg="#0d1016", fg=C_NAME, activebackground="#2a2f3a",
                 activeforeground="#ffffff", bd=0)
    for en, jaa in RARITIES:
        rm.add_command(label=(jaa if ja else en), foreground=rarity_color(en),
                       command=lambda en=en: _hist_set_rarity(rec, en))
    m.add_cascade(label="レア度変更" if ja else "Rarity", menu=rm)
    m.add_separator()
    m.add_command(label="削除" if ja else "Delete", command=lambda: _hist_delete(rec))
    m.tk_popup(ev.x_root, ev.y_root)


def _refresh_history():
    if not (_hist_win[0] and _hist_win[0].winfo_exists() and _hist_inner[0]): return
    lb = LBL.get(_ui_lang, LBL["ja"])
    canvas, inner = _hist_inner[0]
    for w in inner.winfo_children():
        try: w.destroy()
        except Exception: pass
    if not _hist:
        tk.Label(inner, text="まだ履歴がありません" if _ui_lang == "ja" else "No history yet",
                 bg=C_CARD, fg=C_META, anchor="w").pack(fill="x", padx=12, pady=10)
    for rec in sorted(_hist, key=lambda r: (not r.get("fav"),)):   # お気に入りを上に
        ar = rarity_color(rec.get("rarity_en") or "")
        nm = (rec.get("en") if _ui_lang == "en" else rec.get("ja")) or rec.get("ja") or rec.get("en") or "?"
        rj = (rec.get("rarity_ja") if _ui_lang == "ja" else rec.get("rarity_en")) or ""
        star = "★ " if rec.get("fav") else ""
        row = tk.Frame(inner, bg=C_CARD, cursor="hand2"); row.pack(fill="x", padx=6, pady=(4, 0))
        top = tk.Frame(row, bg=C_CARD); top.pack(fill="x")
        tk.Label(top, text=star + nm + (("  " + rj) if rj else ""), bg=C_CARD, fg=ar,
                 font=("Yu Gothic UI", 10, "bold"), anchor="w").pack(side="left")
        tk.Label(top, text=rec.get("ts", ""), bg=C_CARD, fg=C_META,
                 font=("Yu Gothic UI", 8), anchor="e").pack(side="right")
        if rec.get("sell") is not None:
            ptxt = f"{lb['low']} {price(rec['sell'])}   {lb['med']} {price(rec['median'])}"
            pcol = C_PRICE
        else:
            ptxt = lb["noprice"]; pcol = C_META
        cat = rec.get("type_en" if _ui_lang == "en" else "type_ja") or rec.get("type", "")
        tk.Label(row, text=ptxt, bg=C_CARD, fg=pcol, font=("Yu Gothic UI", 9), anchor="w").pack(fill="x")
        tk.Label(row, text=cat, bg=C_CARD, fg=C_META, font=("Yu Gothic UI", 8), anchor="w").pack(fill="x")
        tk.Frame(inner, bg="#2a2f3a", height=1).pack(fill="x", padx=6, pady=(4, 0))
        def _open_mkt(ev, h=rec.get("hash")):
            if h:
                try: webbrowser.open(f"https://steamcommunity.com/market/listings/{APPID}/" + urllib.parse.quote(h))
                except Exception: pass
        for wdg in (row, top, *top.winfo_children(), *row.winfo_children()):
            wdg.bind("<Button-1>", _open_mkt)
            wdg.bind("<Button-3>", lambda ev, r=rec: _row_menu(ev, r))   # 右クリックでメニュー
    canvas.update_idletasks(); canvas.configure(scrollregion=canvas.bbox("all"))

def show_history(root):
    if _hist_win[0] and _hist_win[0].winfo_exists():
        _hist_win[0].deiconify(); _refresh_history(); return
    win = tk.Toplevel(root); win.title("TBH 価格履歴"); win.config(bg=C_CARD)
    win.geometry("360x460"); win.attributes("-topmost", True)
    win.protocol("WM_DELETE_WINDOW", lambda: toggle_history(root))   # ×でオフに同期
    f_hbtn = tkfont.Font(family="Yu Gothic UI", size=9)
    hdr = tk.Frame(win, bg=C_CARD); hdr.pack(fill="x", padx=12, pady=(10, 0))
    tk.Label(hdr, text="価格履歴" if _ui_lang == "ja" else "Price history", bg=C_CARD, fg=C_NAME,
             font=("Yu Gothic UI", 13, "bold"), anchor="w").pack(side="left")
    round_pill(hdr, "↻ " + ("全部更新" if _ui_lang == "ja" else "Update all"),
               C_ACCENT, "#0c0c0c", _hist_update_all, f_hbtn).pack(side="right")
    _hist_status[0] = tk.Label(win, text="", bg=C_CARD, fg=C_ACCENT,
                               font=("Yu Gothic UI", 9), anchor="w")
    _hist_status[0].pack(fill="x", padx=12, pady=(0, 2))
    body = tk.Frame(win, bg=C_CARD); body.pack(fill="both", expand=True, padx=6, pady=(0, 8))
    canvas = tk.Canvas(body, bg=C_CARD, highlightthickness=0)
    sb = tk.Scrollbar(body, orient="vertical", command=canvas.yview)
    inner = tk.Frame(canvas, bg=C_CARD)
    canvas.create_window((0, 0), window=inner, anchor="nw", width=326)
    canvas.configure(yscrollcommand=sb.set)
    canvas.pack(side="left", fill="both", expand=True); sb.pack(side="right", fill="y")
    win.bind("<MouseWheel>", lambda e: canvas.yview_scroll(int(-e.delta / 120), "units"))
    _hist_win[0] = win; _hist_inner[0] = (canvas, inner)
    # NOACTIVATE維持＝アクティブ化で前面を奪わない→ゲームが覆い被さらない（時間で消えない）。
    # クリック/右クリックは受け取れる。スクロールはWin11の「非アクティブ窓もスクロール」既定で可。
    _keep_on_top(win)
    _refresh_history()

def hide_history():
    if _hist_win[0]:
        try: _hist_win[0].withdraw()
        except Exception: pass

def toggle_history(root):
    _hist_visible[0] = not _hist_visible[0]
    if _hist_visible[0]: show_history(root)
    else: hide_history()


def show_settings(root):
    if _set_win[0] and _set_win[0].winfo_exists():
        _set_win[0].deiconify(); _set_win[0].lift(); return
    ja = _ui_lang == "ja"
    win = tk.Toplevel(root); win.title("設定"); win.config(bg=C_CARD)
    win.attributes("-topmost", True); win.resizable(False, False)
    win.protocol("WM_DELETE_WINDOW", win.withdraw)
    f = tkfont.Font(family="Yu Gothic UI", size=12)
    fb = tkfont.Font(family="Yu Gothic UI", size=11, weight="bold")
    fs = tkfont.Font(family="Yu Gothic UI", size=9)

    tk.Label(win, text="ポップアップを出すキー" if ja else "Popup shortcut",
             bg=C_CARD, fg=C_NAME, font=fb, anchor="w").grid(row=0, column=0, sticky="w", padx=20, pady=(18, 4))
    state = {"capturing": False}
    # 現在キーの欄＝そのままクリックで割り当て開始（要素は1つ）
    field = tk.Label(win, text=_trigger_label(), bg="#0d1016", fg=C_ACCENT, font=f,
                     anchor="w", cursor="hand2", padx=12, pady=10)
    field.grid(row=1, column=0, sticky="we", padx=20)
    tk.Label(win, text=("↑ ここをクリックして、使いたいキー（Ctrl+Shift+P等の組み合わせ可）か"
                        "マウスボタンを押す" if ja else
                        "Click above, then press a key (combos like Ctrl+Shift+P ok) or mouse button"),
             bg=C_CARD, fg=C_META, font=fs, anchor="w", justify="left",
             wraplength=320).grid(row=2, column=0, sticky="we", padx=20, pady=(6, 2))

    def start_capture(*_):
        if state["capturing"]: return
        state["capturing"] = True
        field.config(text=("キーかボタンを押す…" if ja else "Press a key or button…"), fg=C_ERR)
        def done(kind, value):
            def apply():
                state["capturing"] = False
                _trigger.update(kind=kind, value=value)
                _bind_trigger(); _save_settings()
                if field.winfo_exists(): field.config(text=_trigger_label(), fg=C_ACCENT)
            if win.winfo_exists(): win.after(0, apply)
        _capture_trigger(done)
    field.bind("<Button-1>", start_capture)

    def reset():
        _trigger.update(kind="mouse", value="x"); _bind_trigger(); _save_settings()
        field.config(text=_trigger_label(), fg=C_ACCENT)
    bf = tk.Frame(win, bg=C_CARD); bf.grid(row=3, column=0, sticky="we", padx=20, pady=(12, 18))
    round_pill(bf, "既定に戻す（マウス サイド戻る）" if ja else "Reset to default",
               "#2a2f3a", C_NAME, reset, fs).pack(side="left")
    win.columnconfigure(0, weight=1)
    win.update_idletasks()
    win.geometry(f"{win.winfo_reqwidth()}x{win.winfo_reqheight()}")   # 内容ぴったりに固定
    _set_win[0] = win
    _keep_on_top(win)


def poll(root):
    try:
        while True:
            results, xy, text = PQ.get_nowait()
            if results == "__history__":           # トレイから履歴表示の同期
                if _hist_visible[0]: show_history(root)
                else: hide_history()
                continue
            if results == "__hist_trim__":         # 上限変更→切り詰め＋更新
                _hist_trim(); _save_hist(); _refresh_history()
                continue
            if results == "__settings__":          # トレイから設定を開く
                show_settings(root)
                continue
            if results == "__close__":
                for w in _open[:]:
                    try: w.destroy()
                    except Exception: pass
                    _open.remove(w)
                if _dbg_win[0] is not None:          # デバッグ窓も消す（撮影に写り込ませない）
                    try: _dbg_win[0].destroy()
                    except Exception: pass
                    _dbg_win[0] = None
                continue
            if results == "__debug__":
                show_debug(xy, root)        # xy にデバッグ画像が入っている
                continue
            show_popup(results, xy, text, root)
            if _hist_visible[0]: _refresh_history()    # 履歴を開いていれば更新
    except queue.Empty:
        pass
    root.after(80, lambda: poll(root))


# ---- タスクトレイ --------------------------------------------------------
def tray_image():
    img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle([4, 4, 60, 60], radius=14, fill=(26, 29, 36, 255),
                        outline=(45, 212, 191, 255), width=3)
    d.ellipse([20, 20, 44, 44], outline=(52, 211, 153, 255), width=4)
    d.line([32, 16, 32, 48], fill=(52, 211, 153, 255), width=3)
    return img


def run_tray(root):
    def _quit(icon, item):
        icon.stop()
        root.after(0, root.destroy)
    def _toggle_hist(icon, item):
        _hist_visible[0] = not _hist_visible[0]       # 状態を反転（×と二重反転しないようpoll側は同期のみ）
        PQ.put(("__history__", None, None))
        icon.update_menu()
    def _mk_limit(n):
        def _cb(icon, item):
            _hist_limit[0] = n
            PQ.put(("__hist_trim__", None, None))
            icon.update_menu()
        return _cb
    limit_menu = pystray.Menu(*[
        pystray.MenuItem(("無制限" if n == 0 else f"{n} 件"), _mk_limit(n),
                         checked=lambda item, n=n: _hist_limit[0] == n, radio=True)
        for n in (20, 50, 100, 200, 0)
    ])
    menu = pystray.Menu(
        pystray.MenuItem(lambda item: f"キー：{_trigger_label()}", None, enabled=False),
        pystray.MenuItem("設定", lambda icon, item: PQ.put(("__settings__", None, None))),
        pystray.MenuItem("履歴一覧", _toggle_hist, checked=lambda item: _hist_visible[0]),
        pystray.MenuItem("履歴の上限", limit_menu),
        pystray.MenuItem("終了", _quit),
    )
    pystray.Icon("tbh_price_ocr", tray_image(), "TBH 相場OCR", menu).run()


# ---- main ----------------------------------------------------------------
def main():
    _load_hist()                                               # 保存済み履歴を復元
    _load_settings()                                           # 保存済みトリガー設定を復元
    threading.Thread(target=fetch_rate, daemon=True).start()   # 円レート取得（非同期）
    root = tk.Tk()
    root.withdraw()
    threading.Thread(target=ocr_worker, daemon=True).start()    # OCR常駐ワーカー（初期化1回）
    _bind_trigger()                                             # 設定されたキー/ボタンで発動（既定:マウス戻る）
    threading.Thread(target=run_tray, args=(root,), daemon=True).start()
    poll(root)
    root.mainloop()


if __name__ == "__main__":
    try:
        main()
    except Exception:
        log_fatal("fatal:\n" + traceback.format_exc())
        try:
            import tkinter.messagebox as mb
            r = tk.Tk(); r.withdraw()
            mb.showerror("TBH相場OCR", "起動に失敗しました。error.log を確認してください。")
        except Exception:
            pass
