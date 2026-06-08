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
import os, sys, json, threading, queue, traceback, time, webbrowser, urllib.parse
import tkinter as tk
from tkinter import font as tkfont

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
CALIBRATE     = True               # Trueで撮影画像を保存（調整用）
DEBUG_UI      = True               # Trueで押下毎に「撮影＋枠＋読取＋結果」を1枚のウィンドウ表示
# 配色
C_CARD, C_ACCENT = "#1a1d24", "#2dd4bf"
C_NAME, C_JA, C_PRICE, C_META, C_ERR = "#ffffff", "#8ab4f8", "#34d399", "#8b909a", "#f87171"
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
    from tbh_price_match import Matcher
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

def yen(c):
    return "—" if c is None else f"¥{round(c / 100 * JPY_RATE):,}"


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
    try:
        r = winocr.recognize_pil_sync(_adapt(c), "ja")
        return " ".join(l.get("text", "") for l in (r.get("lines") if isinstance(r, dict) else []) or [])
    except Exception:
        return ""


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
    for x, y, s in peaks:                       # 近接ピークをまとめる
        if all(abs(x - px) > 45 or abs(y - py) > 26 for px, py, _ in picked):
            picked.append((x, y, s))
        if len(picked) >= 14:
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
                best_r = None
                for probe in (name, name + " " + rank):   # 名前単独(素材)と名前＋等級(装備)両方→高い方
                    rr = matcher.match(probe)
                    if rr and (best_r is None or rr[0]["score"] > best_r[0]["score"]):
                        best_r = rr
                cx, cy = bx + 250, by + 30
                if best_r:
                    sx, sy = ox + cx, oy + cy
                    d2 = (sx - xy[0]) ** 2 + (sy - xy[1]) ** 2
                    cands.append((best_r[0]["score"], d2, sx, sy, best_r, bx, by, name, rank))
            found, chosen = [], None
            if cands:
                ax, ay = min(cands, key=lambda c: c[1])[2:4]   # カーソル最近の枠＝指してる位置
                same = [c for c in cands if (c[2] - ax) ** 2 + (c[3] - ay) ** 2 < 80 ** 2]
                best = max(same, key=lambda c: c[0])
                if best[0] >= 0.85:
                    found, chosen = best[4], best
            if DEBUG_UI:                          # デバッグUI: 撮影画像＋枠＋読取＋結果を1枚に
                try:
                    PQ.put(("__debug__", _annotate(img, boxes, cands, chosen, xy, (ox, oy)), None))
                except Exception:
                    log_fatal("annotate:\n" + traceback.format_exc())
            PQ.put((found, xy, ""))
        except Exception:
            log_fatal("worker error:\n" + traceback.format_exc())


# ---- ポップ表示（メインスレッドで） --------------------------------------
_open = []
def show_popup(results, xy, text, root):
    for w in _open[:]:
        try: w.destroy()
        except Exception: pass
        _open.remove(w)

    win = tk.Toplevel(root)
    win.overrideredirect(True)
    win.attributes("-topmost", True)
    try: win.attributes("-alpha", 0.97)
    except Exception: pass

    border = tk.Frame(win, bg=C_ACCENT)
    border.pack()
    card = tk.Frame(border, bg=C_CARD)
    card.pack(padx=(6, 2), pady=2)   # 左に太めのアクセント帯

    f_name  = tkfont.Font(family="Yu Gothic UI", size=20, weight="bold")
    f_price = tkfont.Font(family="Yu Gothic UI", size=26, weight="bold")
    f_sub   = tkfont.Font(family="Yu Gothic UI", size=15)
    f_meta  = tkfont.Font(family="Yu Gothic UI", size=12)

    def row(txt, color, fnt, pady=(2, 2)):
        tk.Label(card, text=txt, bg=C_CARD, fg=color, font=fnt,
                 anchor="w", justify="left").pack(fill="x", padx=20, pady=pady)

    url = None
    if results == "__processing__":
        row("🔍 読み取り中…", C_ACCENT, f_name, (16, 16))
    elif not results:
        row("該当なし", C_ERR, f_name, (16, 4))
        snip = (text or "").strip().replace("\n", " ")[:36] or "(読取なし)"
        row(f"読取: {snip}", C_META, f_meta, (0, 16))
    else:
        e = results[0]
        rj = e.get("rarity_ja") or ""
        jp = e.get("ja", "") + (f"（{rj}）" if rj else "")     # 日本語名＋等級を大きく
        row(jp, C_NAME, f_name, (16, 0))
        en_line = e.get("en", "") + (f" ({e['rarity_en']})" if e.get("rarity_en") else "")
        row(en_line, C_JA, f_sub, (0, 4))                      # 英語名は小さく
        if e.get("sell") is not None:
            row(f"最安 {yen(e['sell'])}    中央値 {yen(e['median'])}", C_PRICE, f_price, (4, 4))
            row(f"{e.get('type','')}   出品 {e.get('listings','—')} / 売買 {e.get('volume','—')}",
                C_META, f_meta, (0, 2))
            row("クリックでSteamマーケットを開く", C_ACCENT, f_meta, (0, 2))
            row(f"相場 {matcher.marketUpdated or '—'}", C_META, f_meta, (0, 16))
            url = f"https://steamcommunity.com/market/listings/{APPID}/" + \
                  urllib.parse.quote(e.get("hash") or e.get("en", ""))
        else:
            row("市場価格なし（非取引）", C_PRICE, f_price, (4, 4))
            row(e.get("type", ""), C_META, f_meta, (0, 16))

    win.update_idletasks()
    sw, sh = win.winfo_screenwidth(), win.winfo_screenheight()
    pw, ph = win.winfo_width(), win.winfo_height()
    x = min(max(8, xy[0] + 26), sw - pw - 8)     # カーソル近くに表示
    y = min(max(8, xy[1] + 26), sh - ph - 8)
    win.geometry(f"+{x}+{y}")

    def on_click(ev):
        if url:
            try: webbrowser.open(url)
            except Exception: pass
        win.destroy()
    for w in [win, border, card] + list(card.winfo_children()):
        w.bind("<Button-1>", on_click)
    win.after(int(POPUP_SECONDS * 1000), lambda: (win.winfo_exists() and win.destroy()))
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
