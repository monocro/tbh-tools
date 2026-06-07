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
# 詳細パネルは左右どちらにも・横位置がズレて出るので、名前＋等級が出るY帯を横幅いっぱい撮る。
# 辞書側で最長一致するのでノイズが混じっても名前を抽出できる。(左, 上, 右, 下) のウィンドウ比率。
NAME_REGIONS = [
    (0.0, 0.25, 1.0, 0.42),
]
OCR_LANGS     = ["ja", "en"]
POPUP_SECONDS = 6
CALIBRATE     = True               # Trueで撮影画像を tbh-ocr-capture.png に保存（調整用・一時ON）
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
    from PIL import Image, ImageDraw
    from PIL import ImageOps
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

matcher = Matcher(os.path.join(HERE, "tbh-price-lookup.json"))
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
    """ゲームウィンドウ基準で frac=(左,上,右,下)比率の領域を撮る。"""
    import ctypes
    from ctypes import wintypes
    hwnd = ctypes.windll.user32.GetForegroundWindow()
    r = wintypes.RECT()
    ctypes.windll.user32.GetWindowRect(hwnd, ctypes.byref(r))
    W, H = r.right - r.left, r.bottom - r.top
    x0, y0, x1, y1 = frac
    region = {"left": r.left + int(W * x0), "top": r.top + int(H * y0),
              "width": max(1, int(W * (x1 - x0))), "height": max(1, int(H * (y1 - y0)))}
    with mss.mss() as sct:
        raw = sct.grab(region)
    return Image.frombytes("RGB", raw.size, raw.bgra, "raw", "BGRX")


def preprocess(img):
    """色付き文字対策: 明度(V=max(R,G,B))チャンネル→3倍拡大→コントラスト強調。
    マゼンタ/オレンジ等のレア色名でも白黒高コントラストになりOCR精度が大きく上がる。"""
    v = img.convert("HSV").split()[2]
    w, h = v.size
    v = v.resize((w * 3, h * 3), Image.LANCZOS)
    v = ImageOps.autocontrast(v)
    return v.convert("RGB")


def ocr(img):
    proc = preprocess(img)
    if CALIBRATE:
        try: proc.save(os.path.join(HERE, "tbh-ocr-proc.png"))
        except Exception: pass
    best = ""
    for lang in OCR_LANGS:
        try:
            r = winocr.recognize_pil_sync(proc, lang)
            t = r.text if hasattr(r, "text") else (r.get("text", "") if isinstance(r, dict) else "")
        except Exception:
            t = ""
        if len(t) > len(best):
            best = t
    return best


_busy = threading.Lock()

def on_trigger():
    """別スレッド: ゲームが前面の時だけ 撮影→OCR→照合 し、結果をキューに積む。"""
    if foreground_exe() != GAME_EXE:
        return                          # 他アプリでは何もしない＝「戻る」は普通に効く
    if not _busy.acquire(blocking=False):
        return                          # 処理中の連打は無視（OCR競合・古い結果表示を防ぐ）
    try:
        xy = cursor_pos()
        PQ.put(("__close__", None, None))      # ① 古いポップを消す（前の結果を撮らない＝stale防止）
        time.sleep(0.12)
        imgs = [grab(reg) for reg in NAME_REGIONS]   # ② ポップ無しの状態で先に撮影
        PQ.put(("__processing__", xy, None))   # ③ 撮影後に「読み取り中」（写り込まない）
        found, dbg = [], []
        for i, img in enumerate(imgs):
            if CALIBRATE:
                try: img.save(os.path.join(HERE, f"cap{i}.png"))
                except Exception: pass
            t = ocr(img)
            dbg.append(t)
            r = matcher.match(t)
            if r:
                found = r
                break
        if CALIBRATE:
            try:
                with open(os.path.join(HERE, "ocr-text.txt"), "w", encoding="utf-8") as f:
                    f.write(" || ".join(dbg) or "(empty)")
            except Exception:
                pass
        PQ.put((found, xy, " || ".join(dbg)))
    except Exception:
        log_fatal("trigger error:\n" + traceback.format_exc())
    finally:
        _busy.release()


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
        jp = e.get("base_ja") or e.get("ja") or e["base_en"]   # 日本語名＋等級を大きく
        row(jp, C_NAME, f_name, (16, 0))
        row(e["base_en"], C_JA, f_sub, (0, 4))                  # 英語名は小さく
        row(f"最安 {yen(e['sell'])}    中央値 {yen(e['median'])}", C_PRICE, f_price, (4, 4))
        row(f"{e.get('type','')}   出品 {e.get('listings','—')} / 売買 {e.get('volume','—')}",
            C_META, f_meta, (0, 2))
        row("クリックでSteamマーケットを開く", C_ACCENT, f_meta, (0, 2))
        row(f"相場 {matcher.marketUpdated or '—'}", C_META, f_meta, (0, 16))
        url = f"https://steamcommunity.com/market/listings/{APPID}/" + urllib.parse.quote(e["en"])

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
def warmup():
    """起動時にOCRエンジンを温める（初回押下の遅延・失敗を防ぐ）。"""
    try:
        winocr.recognize_pil_sync(Image.new("RGB", (48, 48)), "ja")
    except Exception:
        pass

def main():
    threading.Thread(target=fetch_rate, daemon=True).start()   # 円レート取得（非同期）
    threading.Thread(target=warmup, daemon=True).start()       # OCRウォームアップ
    root = tk.Tk()
    root.withdraw()
    # マウスの「戻る」サイドボタンで発動（ゲームが前面の時だけ on_trigger 内で判定）
    mouse.on_button(lambda: threading.Thread(target=on_trigger, daemon=True).start(),
                    buttons=(SIDE_BUTTON,), types=("down",))
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
