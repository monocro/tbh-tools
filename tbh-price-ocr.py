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
import os, sys, json, threading, queue, traceback
import tkinter as tk
from tkinter import font as tkfont

# ---- 設定 ----------------------------------------------------------------
SIDE_BUTTON   = "x"                # マウスの「戻る」(XBUTTON1)。効かなければ "x2" に変更
GAME_EXE      = "taskbarhero.exe"  # この実行ファイルが前面の時だけ反応
BOX_LEFT, BOX_RIGHT = -60, 460     # カーソル基準の撮影ボックス(px)。全画面は撮らない。
BOX_UP,   BOX_DOWN  = -40, 300
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


def cents(c):
    return "—" if c is None else f"${c/100:.2f}"


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
def grab_box():
    import ctypes
    from ctypes import wintypes
    pt = wintypes.POINT()
    ctypes.windll.user32.GetCursorPos(ctypes.byref(pt))
    cx, cy = pt.x, pt.y
    region = {"left": cx + BOX_LEFT, "top": cy + BOX_UP,
              "width": BOX_RIGHT - BOX_LEFT, "height": BOX_DOWN - BOX_UP}
    with mss.mss() as sct:
        raw = sct.grab(region)
    img = Image.frombytes("RGB", raw.size, raw.bgra, "raw", "BGRX")
    if CALIBRATE:
        img.save(os.path.join(HERE, "tbh-ocr-capture.png"))
    return img, (cx, cy)


def ocr(img):
    best = ""
    for lang in OCR_LANGS:
        try:
            r = winocr.recognize_pil_sync(img, lang)
            t = r.text if hasattr(r, "text") else (r.get("text", "") if isinstance(r, dict) else "")
        except Exception:
            t = ""
        if len(t) > len(best):
            best = t
    return best


def on_trigger():
    """別スレッド: ゲームが前面の時だけ 撮影→OCR→照合 し、結果をキューに積む。"""
    try:
        if foreground_exe() != GAME_EXE:
            return                      # 他アプリでは何もしない＝「戻る」は普通に効く
        img, xy = grab_box()
        text = ocr(img)
        if CALIBRATE:
            try:
                with open(os.path.join(HERE, "ocr-text.txt"), "w", encoding="utf-8") as f:
                    f.write(text or "(empty)")
            except Exception:
                pass
        results = matcher.match(text)
        PQ.put((results, xy, text))
    except Exception:
        log_fatal("trigger error:\n" + traceback.format_exc())


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
    card.pack(padx=(3, 1), pady=1)   # 左に細いアクセント帯

    f_name  = tkfont.Font(family="Yu Gothic UI", size=13, weight="bold")
    f_price = tkfont.Font(family="Yu Gothic UI", size=15, weight="bold")
    f_sub   = tkfont.Font(family="Yu Gothic UI", size=10)
    f_meta  = tkfont.Font(family="Yu Gothic UI", size=9)

    def row(txt, color, fnt, pady=(1, 1)):
        tk.Label(card, text=txt, bg=C_CARD, fg=color, font=fnt,
                 anchor="w", justify="left").pack(fill="x", padx=12, pady=pady)

    if not results:
        row("該当なし", C_ERR, f_name, (10, 2))
        snip = (text or "").strip().replace("\n", " ")[:36] or "(読取なし)"
        row(f"読取: {snip}", C_META, f_meta, (0, 10))
    else:
        e = results[0]
        name = e["base_en"] + (f"  [{e['variant']}]" if e["variant"] else "")
        row(name, C_NAME, f_name, (10, 0))
        if e.get("ja"):
            row(e["ja"], C_JA, f_sub, (0, 2))
        row(f"最安 {cents(e['sell'])}    中央値 {cents(e['median'])}", C_PRICE, f_price, (2, 2))
        row(f"{e.get('type','')}   出品 {e.get('listings','—')} / 売買 {e.get('volume','—')}",
            C_META, f_meta, (0, 0))
        row(f"相場 {matcher.marketUpdated or '—'}", C_META, f_meta, (0, 10))

    win.update_idletasks()
    sw, sh = win.winfo_screenwidth(), win.winfo_screenheight()
    x = sw - win.winfo_width() - 24      # 画面右上に固定（撮影範囲＝カーソル周辺と重ならない）
    y = 70
    win.geometry(f"+{max(8, x)}+{max(8, y)}")
    win.bind("<Button-1>", lambda ev: win.destroy())
    win.after(int(POPUP_SECONDS * 1000), lambda: (win.winfo_exists() and win.destroy()))
    _open.append(win)


def poll(root):
    try:
        while True:
            results, xy, text = PQ.get_nowait()
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
