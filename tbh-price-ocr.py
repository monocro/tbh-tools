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
    import pystray
    from tbh_price_match import Matcher, RARITIES, norm as _norm
except Exception as e:
    log_fatal("import error:\n" + traceback.format_exc())
    try:
        import tkinter.messagebox as mb
        r = tk.Tk(); r.withdraw()
        mb.showerror("TBH相場OCR", f"必要なライブラリが不足:\n{e}\n\npip install mss pillow winocr mouse pystray")
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
def live_price(hash_name):
    """Steamマーケットの現在価格を取得（表示の瞬間に最新を取る）。失敗時None。5分キャッシュ。"""
    if not hash_name: return None
    now = time.time()
    c = _price_cache.get(hash_name)
    if c and now - c[0] < 300:
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

def _keep_on_top(win):
    """フルスクリーン(ボーダーレス)のゲームの前へ出し続ける。要点は WS_EX_NOACTIVATE:
    これを付けるとポップをクリックしてもアクティブ化が起きない＝ゲームが前面に出てこない。
    さらにTOPMOSTを維持し、120ms毎に再主張して背後への回り込みを防ぐ。"""
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
            want = ex | WS_EX_TOPMOST | WS_EX_NOACTIVATE
            if want != ex:
                u.SetWindowLongW(h, GWL_EXSTYLE, want)
            u.SetWindowPos(h, HWND_TOPMOST, 0, 0, 0, 0, SWP)
        except Exception: pass
        win.after(120, tick)
    tick()

def _dismiss(win):
    """ポップの閉じ方のマナー: ①カーソルが一度乗ってから外れて0.7秒で閉じる(ホバーアウト)
    ②UI外を左クリックで即閉じ(ライトディスミス) ③一度も乗らなければ8秒で自動消滅。
    メニュー展開中(grab)は判定を止める。"""
    try: import ctypes
    except Exception: return
    u = ctypes.windll.user32
    class _PT(ctypes.Structure): _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]
    s = {"entered": False, "out": 0, "age": 0}
    def tick():
        if not win.winfo_exists(): return
        s["age"] += 1
        try:
            if win.grab_current():            # メニュー展開中は何もしない
                win.after(80, tick); return
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

    border = tk.Frame(win, bg=rarity_color(init_rar)); border.pack()   # レア度色の枠
    content = tk.Frame(border, bg=C_CARD); content.pack(padx=3, pady=3)
    content.columnconfigure(0, weight=1)

    # アイテム名：キーボード入力はゲーム最前面を奪って消えるので、マウスのみの候補ドロップダウンに。
    def _disp(ent):
        nm = (ent.get("en") if _ui_lang == "en" else ent.get("ja")) or ent.get("en") or ent.get("ja") or "?"
        rj = (ent.get("rarity_ja") if _ui_lang == "ja" else ent.get("rarity_en")) or ""
        return (nm + ("  " + rj if rj else "")).strip()
    cand_list = []
    try: cand_list = matcher.candidates(text or "", 8)
    except Exception: cand_list = []
    if e and not any(c.get("ja") == e.get("ja") and c.get("rarity_ja") == e.get("rarity_ja") for c in cand_list):
        cand_list = [e] + cand_list
    name_menu = tk.Menu(win, tearoff=0, bg="#0d1016", fg=C_NAME, activebackground="#2a2f3a",
                        activeforeground="#ffffff", bd=0, relief="flat")
    for c in cand_list:
        name_menu.add_command(label=_disp(c), foreground=rarity_color(c.get("rarity_en") or ""),
                              command=lambda c=c: pick(c))
    name_lbl = tk.Label(content, text=(init_name or "—") + ("  ▾" if cand_list else ""),
                        bg=C_CARD, fg=C_NAME, font=f_name, anchor="w", cursor="hand2")
    name_lbl.grid(row=0, column=0, sticky="we", padx=14, pady=(14, 6))
    if cand_list:
        name_lbl.bind("<Button-1>", lambda ev: name_menu.tk_popup(
            name_lbl.winfo_rootx(), name_lbl.winfo_rooty() + name_lbl.winfo_height()))

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
    round_pill(btnf, "✕", "#2a2f3a", C_NAME, win.destroy, f_meta, padx=12).pack(side="right")

    def render(ent):
        state["entry"] = ent
        ar = rarity_color(state["rarity"] or (ent.get("rarity_en") if ent else ""))
        border.config(bg=ar); price_lbl.config(fg=ar); recolor_pill(mkt_pill, ar)
        if ent:
            name_lbl.config(text=((ent.get("en") if _ui_lang == "en" else ent.get("ja"))
                                  or ent.get("en") or ent.get("ja") or "—") + ("  ▾" if cand_list else ""))
        if ent and ent.get("sell") is not None:
            price_lbl.config(text=f"{lb['low']} {price(ent['sell'])}   {lb['med']} {price(ent['median'])}")
            cat = ent.get("type_en" if _ui_lang == "en" else "type_ja") or ent.get("type", "")
            meta_lbl.config(text=f"{cat}   {lb['sold']}{ent.get('volume','—')}")
        elif ent:
            price_lbl.config(text=lb["noprice"]); meta_lbl.config(text=ent.get("type_ja", "") or ent.get("type_en", ""))
        else:
            price_lbl.config(text=lb["nomatch"]); meta_lbl.config(text="")
        _place(win, xy)

    def _fetch_live(ent):
        def work():
            lp = live_price(ent.get("hash")) if ent else None
            if lp:
                low, med, vol = lp
                if low is not None: ent["sell"] = low
                if med is not None: ent["median"] = med
                if vol is not None: ent["volume"] = vol
            win.after(0, lambda: render(ent))
        threading.Thread(target=work, daemon=True).start()

    def pick(c):                                   # 候補名を選び直し（マウスのみ）
        state["rarity"] = c.get("rarity_en") or ""
        build_rar_pill(); render(c); _fetch_live(c)

    def set_rarity(en):                            # 等級を選び直し→同名×新等級で引き直し
        state["rarity"] = en; build_rar_pill()
        cur = state["entry"]
        nm = (cur.get("ja") or cur.get("en")) if cur else (text or "")
        def work():
            r = matcher.match_item(nm, en2ja.get(en, en))
            ent = r[0] if r else cur
            lp = live_price(ent.get("hash")) if ent else None
            if lp and ent:
                low, med, vol = lp
                if low is not None: ent["sell"] = low
                if med is not None: ent["median"] = med
                if vol is not None: ent["volume"] = vol
            win.after(0, lambda: render(ent))
        threading.Thread(target=work, daemon=True).start()

    build_rar_pill()
    win.bind("<Escape>", lambda ev: win.destroy())

    render(e)
    _round_corners(win)        # Win11のOS角丸（透過なし＝クリックで消えない）
    _keep_on_top(win)          # ゲームの前へ。背後に回り込むのを防ぐ
    _dismiss(win)              # 外側クリック/ホバーアウト/無操作で閉じるマナー
    _open.append(win)


def poll(root):
    try:
        while True:
            results, xy, text = PQ.get_nowait()
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
    menu = pystray.Menu(
        pystray.MenuItem("TBH 相場OCR  ( ゲーム前面で戻るボタン )", None, enabled=False),
        pystray.MenuItem("終了", _quit),
    )
    pystray.Icon("tbh_price_ocr", tray_image(), "TBH 相場OCR", menu).run()


# ---- main ----------------------------------------------------------------
def main():
    threading.Thread(target=fetch_rate, daemon=True).start()   # 円レート取得（非同期）
    root = tk.Tk()
    root.withdraw()
    threading.Thread(target=ocr_worker, daemon=True).start()    # OCR常駐ワーカー（初期化1回）
    # マウスの「戻る」サイドボタンで発動（押下シグナルをワーカーへ。前面判定はワーカー内）
    mouse.on_button(lambda: WORKQ.put(1), buttons=(SIDE_BUTTON,), types=("down",))
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
