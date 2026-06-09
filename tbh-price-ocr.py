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
import os, sys, json, threading, queue, traceback, time, webbrowser, urllib.parse, urllib.request, urllib.error, re
import tkinter as tk
from tkinter import font as tkfont
from tkinter import ttk

# ---- 設定 ----------------------------------------------------------------
APP_NAME      = "TBH MarketLens"
APP_VERSION   = "1.1"
APP_AUTHOR    = "Ghost Shark Robotics"
KOFI_URL      = "https://ko-fi.com/ghostsharkrobotics"        # Ko-fi（空なら寄付ボタン非表示）
APP_REPO      = "GhostSharkRobotics/tbh-marketlens"           # 更新通知の取得元（GitHub Releases）
FEEDBACK_URL  = "https://tbh-stats.monoqulo.workers.dev/feedback"   # アプリ内フィードバック送信先（Worker→Slack）
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
C_PRICE_DIM = "#6f8a80"   # 暫定価格（リアルタイム確定前）。確定=C_PRICE鮮明 / 暫定=この控えめ色＋🕓
C_WAIT = "#e0a040"        # 待機中（レート制限＝ペース調整, BANではない）のアンバー
RARITY_COLORS = {"Common": "#c8c8c8", "Uncommon": "#5ce65c", "Rare": "#5b9bff",
                 "Legendary": "#f5a623", "Immortal": "#ff5252", "Arcana": "#c061ff",
                 "Beyond": "#ff5fb0", "Celestial": "#34d6e6", "Divine": "#ffe14d", "Cosmic": "#ff8a5c"}
def rarity_color(r):
    return RARITY_COLORS.get(r, C_ACCENT)
_ui_lang = "ja"                    # 直近に判定したゲーム言語（ja/en）
# ===== 文言カタログ（全UI文字列はここ＋T()経由。言語追加はLANGSとTRに列を足すだけ） =====
LANGS = ("ja", "en", "zh")
LANG_NAMES = {"ja": "日本語", "en": "English", "zh": "中文"}   # 言語自身の表示名（モード非依存のデータ）
TR = {
    "ja": {
        "low": "最安", "med": "中央値", "lst": "出品", "sold": "売買", "quote": "相場",
        "mkt": "クリックでSteamマーケットを開く", "noprice": "市場価格なし（非取引）",
        "nomatch": "該当なし", "reading": "🔍 読み取り中…", "read": "読取",
        "rarity": "等級", "history": "履歴",
        "hist_title": "価格履歴", "update_all": "全部更新", "hist_empty": "まだ履歴がありません",
        "updating": "更新中 {n}/{total}…", "updated": "更新 {t}", "updating_btn": "更新中…",
        "fav": "☆ お気に入り", "unfav": "★ お気に入り解除", "rename": "アイテム名変更",
        "rarity_change": "レア度変更", "delete": "削除", "rename_title": "アイテム名変更",
        "ok": "OK", "cancel": "キャンセル",
        "downloading": "ダウンロード中…", "extracting": "展開中…", "restarting": "再起動して更新…",
        "update_failed": "更新失敗→ページを開きます", "update_btn": "更新", "update_tray": "新版へ更新",
        "settings_title": "設定", "language": "表示言語", "shortcut": "発動キー",
        "capture_prompt": "キーかボタンを押す…",
        "capture_hint": "↑ クリックして、使いたいキー（Ctrl+Shift+P等の組み合わせ可）かマウスボタンを押す",
        "reset_default": "既定に戻す（マウス サイド戻る）", "howto": "使い方", "support": "☕ 応援",
        "feedback": "フィードバック", "fb_title": "フィードバック / バグ報告", "send": "送信",
        "fb_hint": "不具合・要望・気づいた点を書いて送ってください（匿名でOK）",
        "fb_contact": "返信がほしい場合の連絡先（任意）", "fb_thanks": "送信しました。ありがとう！", "fb_fail": "送信に失敗しました",
        "disclaimer": "非公式ツール · Nugem Studio / Valve とは無関係",
        "help_main": "アイテムに合わせて発動キーを押すと、そのアイテムの\nSteamマーケット価格（最安値・中央値）が出ます。",
        "help_key": "発動キーの既定はマウスのサイドボタン（戻る）。「設定」で変更できます。",
        "help_tips": ["ポップは 外をクリック / カーソルを外す / Esc で閉じる",
                      "名前が違う時はレア度ピルや名前で選び直し",
                      "履歴：トレイ『履歴一覧』で表示。右クリックでお気に入り・名前変更・レア度・削除、『全部更新』も",
                      "発動キー・表示言語は『設定』で変更",
                      "安全：ゲームには干渉しません（自分の画面OCR＋キーのみ）"],
        "close": "閉じる",
        "tray_key": "キー：", "tray_settings": "設定", "tray_history": "履歴一覧",
        "tray_limit": "履歴の上限", "tray_quit": "終了", "unlimited": "無制限", "items": "{n} 件",
        "mouse_x": "マウス サイド(戻る)", "mouse_x2": "マウス サイド(進む)", "mouse_middle": "マウス 中ボタン",
        "mouse_left": "マウス 左", "mouse_right": "マウス 右", "mouse_prefix": "マウス ",
        "dbg_title": "デバッグ",
        "err_deps": "必要なライブラリが不足:\n{e}\n\npip install mss pillow winocr mouse keyboard pystray",
        "err_start": "起動に失敗しました。error.log を確認してください。",
    },
    "en": {
        "low": "Low", "med": "Median", "lst": "List", "sold": "Sold", "quote": "Updated",
        "mkt": "Click to open Steam Market", "noprice": "Not on market",
        "nomatch": "No match", "reading": "🔍 Reading…", "read": "OCR",
        "rarity": "Rarity", "history": "History",
        "hist_title": "Price history", "update_all": "Update all", "hist_empty": "No history yet",
        "updating": "Updating {n}/{total}…", "updated": "Updated {t}", "updating_btn": "Updating…",
        "fav": "☆ Favorite", "unfav": "★ Unfavorite", "rename": "Rename",
        "rarity_change": "Rarity", "delete": "Delete", "rename_title": "Rename item",
        "ok": "OK", "cancel": "Cancel",
        "downloading": "Downloading…", "extracting": "Extracting…", "restarting": "Restarting…",
        "update_failed": "Update failed → opening page", "update_btn": "Update", "update_tray": "Update",
        "settings_title": "Settings", "language": "Language", "shortcut": "Shortcut",
        "capture_prompt": "Press a key or button…",
        "capture_hint": "↑ Click above, then press a key (combos like Ctrl+Shift+P ok) or mouse button",
        "reset_default": "Reset to default", "howto": "How to use", "support": "☕ Support",
        "feedback": "Feedback", "fb_title": "Feedback / Bug report", "send": "Send",
        "fb_hint": "Tell us about bugs, ideas, or anything — anonymous is fine.",
        "fb_contact": "Contact for a reply (optional)", "fb_thanks": "Sent. Thank you!", "fb_fail": "Failed to send",
        "disclaimer": "Unofficial tool · not affiliated with Nugem Studio or Valve",
        "help_main": "Point at an item and press your hotkey — its Steam Market\nprice (lowest + median) pops up.",
        "help_key": "Default hotkey is the mouse side (back) button. Change it in Settings.",
        "help_tips": ["Close the popup by clicking away, moving off it, or pressing Esc",
                      "Wrong name? re-pick via the rarity pill or the name",
                      "History: open from tray. Right-click a row for Favourite / Rename / Rarity / Delete, plus 'Update all'",
                      "Change the hotkey & language in Settings",
                      "Safe: it never touches the game (screen OCR + hotkey only)"],
        "close": "Close",
        "tray_key": "Key: ", "tray_settings": "Settings", "tray_history": "History",
        "tray_limit": "History limit", "tray_quit": "Quit", "unlimited": "Unlimited", "items": "{n}",
        "mouse_x": "Mouse Side (Back)", "mouse_x2": "Mouse Side (Forward)", "mouse_middle": "Mouse Middle",
        "mouse_left": "Mouse Left", "mouse_right": "Mouse Right", "mouse_prefix": "Mouse ",
        "dbg_title": "Debug",
        "err_deps": "Missing libraries:\n{e}\n\npip install mss pillow winocr mouse keyboard pystray",
        "err_start": "Failed to start. Please check error.log.",
    },
    "zh": {
        "low": "最低", "med": "中位", "lst": "在售", "sold": "成交", "quote": "行情",
        "mkt": "点击打开 Steam 市场", "noprice": "无市场价格（不可交易）",
        "nomatch": "无匹配", "reading": "🔍 识别中…", "read": "识别",
        "rarity": "品质", "history": "历史",
        "hist_title": "价格历史", "update_all": "全部更新", "hist_empty": "暂无历史",
        "updating": "更新中 {n}/{total}…", "updated": "更新 {t}", "updating_btn": "更新中…",
        "fav": "☆ 收藏", "unfav": "★ 取消收藏", "rename": "重命名",
        "rarity_change": "修改品质", "delete": "删除", "rename_title": "重命名物品",
        "ok": "确定", "cancel": "取消",
        "downloading": "下载中…", "extracting": "解压中…", "restarting": "重启更新…",
        "update_failed": "更新失败→打开页面", "update_btn": "更新", "update_tray": "更新到新版本",
        "settings_title": "设置", "language": "显示语言", "shortcut": "触发键",
        "capture_prompt": "请按下按键或鼠标按钮…",
        "capture_hint": "↑ 点击后，按下想用的按键（可组合，如 Ctrl+Shift+P）或鼠标按钮",
        "reset_default": "恢复默认（鼠标侧键·后退）", "howto": "使用方法", "support": "☕ 支持",
        "feedback": "反馈", "fb_title": "反馈 / 报告问题", "send": "发送",
        "fb_hint": "请填写问题、建议或任何想法，匿名也可。",
        "fb_contact": "如需回复请留联系方式（可选）", "fb_thanks": "已发送，谢谢！", "fb_fail": "发送失败",
        "disclaimer": "非官方工具 · 与 Nugem Studio / Valve 无关",
        "help_main": "将光标对准物品并按下触发键，即可显示该物品的\nSteam 市场价格（最低价·中位价）。",
        "help_key": "触发键默认是鼠标侧键（后退）。可在「设置」中修改。",
        "help_tips": ["点击外部 / 移开光标 / 按 Esc 关闭弹窗",
                      "名称不对时，可用品质标签或名称重新选择",
                      "历史：从托盘「历史」打开。右键可收藏·重命名·改品质·删除，并有「全部更新」",
                      "在「设置」中修改触发键和显示语言",
                      "安全：不干预游戏（仅截屏OCR＋按键）"],
        "close": "关闭",
        "tray_key": "按键：", "tray_settings": "设置", "tray_history": "历史",
        "tray_limit": "历史上限", "tray_quit": "退出", "unlimited": "无限制", "items": "{n} 条",
        "mouse_x": "鼠标侧键(后退)", "mouse_x2": "鼠标侧键(前进)", "mouse_middle": "鼠标中键",
        "mouse_left": "鼠标左键", "mouse_right": "鼠标右键", "mouse_prefix": "鼠标 ",
        "dbg_title": "调试",
        "err_deps": "缺少必要的库:\n{e}\n\npip install mss pillow winocr mouse keyboard pystray",
        "err_start": "启动失败。请查看 error.log。",
    },
}

def T(key, **kw):
    """文言を現在の言語で取得。未定義キーは英語→キー名にフォールバック。{}は.formatで差し込み。"""
    d = TR.get(_ui_lang) or TR["en"]
    s = d.get(key)
    if s is None:
        s = TR["en"].get(key, key)
    if isinstance(s, list):
        return s
    return s.format(**kw) if kw else s
# -------------------------------------------------------------------------

if getattr(sys, "frozen", False):                       # PyInstaller でexe化された場合
    RES = sys._MEIPASS                                  # 同梱リソース（読み取り専用）
    HERE = os.path.dirname(sys.executable)              # 書き込み用（exeの隣＝履歴/設定/ログ）
else:
    RES = HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, RES)
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
        mb.showerror("TBH MarketLens", T("err_deps", e=e))
    except Exception:
        pass
    sys.exit(1)

import ctypes as _ctypes
try:                                  # DPI対応: GetCursorPos/GetWindowRect を mss と同じ物理座標に揃える
    _ctypes.windll.shcore.SetProcessDpiAwareness(2)
except Exception:
    try: _ctypes.windll.user32.SetProcessDPIAware()
    except Exception: pass

matcher = Matcher(os.path.join(RES, "tbh-price-lookup.json"))
def _edges(bgr):                                            # 色に依存しない枠形状（Cannyエッジ）
    return cv2.Canny(cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY), 60, 160)
try:
    _TPL = cv2.imread(os.path.join(RES, "frame_tpl.png"))   # 名前枠の左角テンプレート（定数ピクセル）
    _TPL_E = _edges(_TPL) if _TPL is not None else None     # 高レア(背景色が変わる)用のエッジ版
except Exception:
    _TPL = _TPL_E = None
PQ = queue.Queue()          # ポップ要求キュー（別スレッド→メインスレッド）


# 価格はSteamのその国の通貨で取得（現地価格＝Steam表示と一致）。取れない時はバンドルUSDを為替で概算表示。
_CURRENCY = {"en": 1, "ja": 8, "zh": 23}   # Steamの通貨ID: 1=USD, 8=JPY, 23=CNY
JPY_RATE = 155.0     # USD→JPY（概算フォールバック用）。起動時に最新へ
CNY_RATE = 7.2       # USD→CNY（概算フォールバック用）

def fetch_rate():
    global JPY_RATE, CNY_RATE
    try:
        with urllib.request.urlopen("https://open.er-api.com/v6/latest/USD", timeout=5) as r:
            rates = json.load(r)["rates"]
            JPY_RATE = float(rates.get("JPY", JPY_RATE)); CNY_RATE = float(rates.get("CNY", CNY_RATE))
    except Exception:
        pass

def _cur_code():
    return _CURRENCY.get(_ui_lang, 1)

def price(c, src=1):
    """価格表示。src=取得時の通貨ID。表示通貨と違えば為替換算（¥はSteam取得不可なのでUSDから換算）。
    印(≈等)は付けず普通の価格として堂々と出す（壊れて見せない）。"""
    if c is None: return "—"
    cur = _cur_code()
    val = c / 100 if src == cur else (c / 100) * (JPY_RATE if cur == 8 else CNY_RATE if cur == 23 else 1.0)
    if cur == 1: return f"${val:.2f}"
    if cur == 8: return f"¥{round(val):,}"           # 円は小数なし
    return f"¥{val:,.2f}"                            # 人民元は小数2桁

def disp_name(e):                  # 現在の言語でアイテム名（zh→簡体、無ければen→ja）
    if not e: return ""
    return (e.get("zh") if _ui_lang == "zh" else e.get("en") if _ui_lang == "en" else e.get("ja")) \
           or e.get("en") or e.get("ja") or e.get("zh") or ""

def disp_type(e):                  # 種別（zhの専用訳は未収録→en、無ければja）
    return (e.get("type_ja") if _ui_lang == "ja" else e.get("type_en")) or e.get("type") or ""

def disp_rarity(e):                # 等級表示（zhの専用訳は未収録→en）
    return (e.get("rarity_ja") if _ui_lang == "ja" else e.get("rarity_en")) or ""


_price_cache = {}                  # (hash, 通貨) -> (取得time, low_cents, med_cents, volume)
# レート制限対策：Steam priceoverview は概ね 20req/分/IP で429。安全圏で自主制限＋429バックオフ。
_RL_MAX, _RL_WIN, _RL_BACKOFF = 15, 60.0, 180.0   # 60秒に最大15件 / 429食らったら180秒停止
_rl_times = []                     # 直近のネット取得時刻
_rl_blocked = [0.0]                # この時刻まではネット取得を停止
_rl_lock = threading.Lock()

def _rl_allow():
    """今ネット取得していいか（自主レート制限＋バックオフ）。OKなら記録してTrue。"""
    now = time.time()
    with _rl_lock:
        if now < _rl_blocked[0]:
            return False
        cut = now - _RL_WIN
        while _rl_times and _rl_times[0] < cut: _rl_times.pop(0)
        if len(_rl_times) >= _RL_MAX:
            return False
        _rl_times.append(now)
        return True

_SPIN = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"          # 待機/処理中の回転スピナー（動き＝生きてる、の可視化）

def _rl_state():
    """レート制限の今の状態を返す。('blocked'|'throttle'|'ok', 解除までの秒)。
    UIで「待機中（=ペース調整、BANではない）」を見せるために使う。"""
    now = time.time()
    with _rl_lock:
        if now < _rl_blocked[0]:
            return "blocked", _rl_blocked[0] - now          # 429バックオフ中＝全停止
        cut = now - _RL_WIN
        t = [x for x in _rl_times if x >= cut]
        if len(t) >= _RL_MAX:
            return "throttle", max(0.0, (t[0] + _RL_WIN) - now)   # 枠いっぱい＝少し待つ
        return "ok", 0.0

# ===== 価格取得 =====================================================
# 本線は search/render を「品名クエリ」で叩き、見ている品だけ1リクエストで最新USDを取得。
# priceoverview と違い 429 になりにくい＝BANされない。市場に無い品は0件→バンドル(USD)にフォールバック。
# 現地通貨(¥等)は priceoverview が叩ける時だけ正確値に格上げ（今は全429なので基本USD→為替概算）。
_render_cache = {}                  # hash_name -> (取得time, sell_cents_USD, listings)
_render_blocked = [0.0]             # render が万一429になった時のバックオフ（priceoverviewとは独立）

def _render_price(hash_name):
    """search/render を品名+レア度クエリで叩き、その変種の現在USD価格を返す → (usd_cents, listings) or None。"""
    if time.time() < _render_blocked[0]:          # 直近でrender自身が429→静かに待つ（nativeの429とは無関係）
        return None
    m = re.match(r"^(.*?) \(([^)]+)\)", hash_name)        # "War Bow (Legendary) A" → "War Bow Legendary"
    base = f"{m.group(1)} {m.group(2)}" if m else hash_name
    query = re.sub(r"\s+", " ", re.sub(r"[^\w ]+", " ", base)).strip()   # 記号(-,()等)除去＝検索演算子の誤爆防止
    try:
        url = (f"https://steamcommunity.com/market/search/render/?appid={APPID}"
               f"&norender=1&start=0&count=30&currency=1&query=" + urllib.parse.quote(query))
        req = urllib.request.Request(url, headers={
            "User-Agent": "Mozilla/5.0", "Referer": "https://steamcommunity.com/market/"})
        d = json.load(urllib.request.urlopen(req, timeout=8))
        for r in (d.get("results") or []):
            if r.get("hash_name") == hash_name and r.get("sell_price") is not None:
                return r["sell_price"], r.get("sell_listings")
        return None                               # 市場に出品なし→バンドルにフォールバック
    except urllib.error.HTTPError as e:
        if e.code == 429:                         # 万一の429→render専用バックオフ（UIが待機表示）
            _render_blocked[0] = time.time() + _RL_BACKOFF
        return None
    except Exception:
        return None

def _native_price(hash_name, cur, force=False):
    """priceoverview で現地通貨の正確値を取得（叩ける時だけ）。429ならNone＝USDにフォールバック。"""
    now = time.time()
    key = (hash_name, cur)
    c = _price_cache.get(key)
    if c and not force and now - c[0] < 300:
        return c[1], c[2], c[3]
    if not _rl_allow():                           # 429バックオフ/枠超過中は試さない
        return (c[1], c[2], c[3]) if c else None
    try:
        url = (f"https://steamcommunity.com/market/priceoverview/?appid={APPID}"
               f"&currency={cur}&market_hash_name=" + urllib.parse.quote(hash_name))
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        d = json.load(urllib.request.urlopen(req, timeout=6))
        def cents(s):
            if not s: return None
            m = re.search(r"[\d,.]+", s)
            return round(float(m.group().replace(",", "")) * 100) if m else None
        low, med, vol = cents(d.get("lowest_price")), cents(d.get("median_price")), d.get("volume")
        if low is None and med is None: return None
        _price_cache[key] = (now, low, med, vol)
        return low, med, vol
    except urllib.error.HTTPError as e:
        if e.code == 429:
            with _rl_lock: _rl_blocked[0] = time.time() + _RL_BACKOFF
        return (c[1], c[2], c[3]) if c else None
    except Exception:
        return (c[1], c[2], c[3]) if c else None

def live_price(hash_name, native_ok=False, force=False, cache_only=False):
    """現在価格 → (low, med, vol, src) or None。src=値の通貨ID（1=USD）。
    本線=search/renderの単品USD（5分キャッシュ）。native_ok かつ表示通貨が非USD かつ priceoverview が叩ける時だけ現地通貨に格上げ。"""
    if not hash_name: return None
    disp = _cur_code()
    now = time.time()
    if native_ok and disp != 1 and not cache_only:        # 現地通貨の正確値（叩ける時）
        nat = _native_price(hash_name, disp, force)
        if nat: return nat[0], nat[1], nat[2], disp
    if native_ok and disp != 1:                           # 格上げ不可→現地キャッシュがあれば
        c = _price_cache.get((hash_name, disp))
        if c: return c[1], c[2], c[3], disp
    rc = _render_cache.get(hash_name)                     # render USD（5分キャッシュ）
    if rc and not force and now - rc[0] < 300:
        return rc[1], rc[1], rc[2], 1
    if cache_only:
        return (rc[1], rc[1], rc[2], 1) if rc else None
    rp = _render_price(hash_name)                         # 1リクエスト（BANされない）
    if rp is not None:
        _render_cache[hash_name] = (now, rp[0], rp[1])
        return rp[0], rp[0], rp[1], 1
    return None                                           # 市場に無い→呼び出し側がバンドル価格を保持

def apply_live(ent, native_ok=False, force=False, cache_only=False):
    """entの価格を最新化（in-place）。cur/_live(=表示通貨で確定か)も設定。取れなければ既存(バンドル)保持。"""
    if not ent or not ent.get("hash"): return
    lp = live_price(ent["hash"], native_ok=native_ok, force=force, cache_only=cache_only)
    if not lp: return
    low, med, vol, src = lp
    if low is not None: ent["sell"] = low
    if med is not None: ent["median"] = med
    if vol is not None: ent["volume"] = vol
    ent["cur"] = src
    ent["_live"] = (src == _cur_code())           # 表示通貨そのもの=確定(鮮明) / USD換算=暫定(🕓)


# ---- 前面ウィンドウ判定（ゲームが前面の時だけ反応） ----------------------
def _hwnd_exe(hwnd):
    import ctypes
    from ctypes import wintypes
    try:
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

def foreground_exe():
    import ctypes
    return _hwnd_exe(ctypes.windll.user32.GetForegroundWindow())

def window_under_cursor():
    """カーソル直下のトップレベル窓の (hwnd, exe名)。ゲームを指しているか判定用。"""
    import ctypes
    from ctypes import wintypes
    try:
        pt = wintypes.POINT(); ctypes.windll.user32.GetCursorPos(ctypes.byref(pt))
        hwnd = ctypes.windll.user32.WindowFromPoint(pt)
        root = (ctypes.windll.user32.GetAncestor(hwnd, 2) if hwnd else 0) or hwnd  # GA_ROOT
        return root, _hwnd_exe(root)
    except Exception:
        return 0, ""

def focus_window(hwnd):
    """指定窓を前面化（フォアグラウンドロックはAttachThreadInputで回避）。"""
    import ctypes
    u = ctypes.windll.user32; k = ctypes.windll.kernel32
    try:
        fg = u.GetForegroundWindow()
        ft = u.GetWindowThreadProcessId(fg, None); ct = k.GetCurrentThreadId()
        if ft and ft != ct: u.AttachThreadInput(ct, ft, True)
        u.SetForegroundWindow(hwnd); u.BringWindowToTop(hwnd)
        if ft and ft != ct: u.AttachThreadInput(ct, ft, False)
    except Exception: pass


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


def _adapt(c, invert=False):
    """局所適応二値化（色付き/暗い名前も白黒高コントラスト化）。
    invert=Trueは高レア等の『明背景＋暗文字』(セレスティアル等のシアンバー)用。"""
    v = c.convert("HSV").split()[2]
    mean = v.filter(ImageFilter.BoxBlur(14))
    a = np.asarray(v, dtype=np.int16); m = np.asarray(mean, dtype=np.int16)
    cond = (a < m - 8) if invert else (a > m + 8)
    return Image.fromarray((cond * 255).astype("uint8"), "L").convert("RGB")


_OCR_LANGS = {"ja": ("ja", "en"), "en": ("en",), "zh": ("zh-Hans", "en")}

def _ocr(c):
    out = []
    langs = _OCR_LANGS.get(_ui_lang, ("ja", "en"))   # 表示言語に合わせて読む文字種を選ぶ（速度維持）
    # 通常(暗背景+明文字)＋反転(明背景+暗文字=高レアのシアンバー)の両方を読み、行ごとに照合させる
    for proc in (_adapt(c), _adapt(c, invert=True)):
        for lang in langs:
            try:
                r = winocr.recognize_pil_sync(proc, lang)
                out.append(" ".join(l.get("text", "") for l in (r.get("lines") if isinstance(r, dict) else []) or []))
            except Exception:
                pass
    return "\n".join(out)   # 各読みは改行区切り＝行ごとに照合（二重化での薄まりを防ぐ）


def detect_boxes(img):
    """名前枠テンプレートで枠を位置特定し、各枠の (名前＋等級テキスト, 枠中心x, 中心y) を返す。
    枠は毎回同じピクセル＝位置が左右・上下に動いてもテンプレートマッチで見つかる。"""
    if _TPL is None:
        return []
    arr = cv2.cvtColor(np.array(img), cv2.COLOR_RGB2BGR)
    res = cv2.matchTemplate(arr, _TPL, cv2.TM_CCOEFF_NORMED)        # 色マッチ（通常レア）
    if _TPL_E is not None:                                          # エッジマッチを合成（高レアは背景色が変わる）
        res = np.maximum(res, cv2.matchTemplate(_edges(arr), _TPL_E, cv2.TM_CCOEFF_NORMED))
    # 閾値は低め＝枠を取りこぼさない。誤検出はマッチャの確信0.85で除外される。
    ys, xs = np.where(res >= 0.55)
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
        head = f"-> {res.get('ja','')}({res.get('rarity_ja','')}) {price(res.get('sell'))}"
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
    win = tk.Toplevel(root); win.title("TBH MarketLens — " + T("dbg_title"))
    win.attributes("-topmost", True)
    ph = ImageTk.PhotoImage(pim)
    lb = tk.Label(win, image=ph, bg="#000"); lb.image = ph; lb.pack()
    win.bind("<Button-1>", lambda e: win.destroy())
    win.geometry("+20+20")
    _dbg_win[0] = win


WORKQ = queue.Queue()    # 戻るボタン押下シグナル（常駐ワーカーが処理）
_last_trig = [0.0]       # 直近の発動時刻（デバウンス用＝二重発動防止）

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
        if time.time() - _last_trig[0] < 0.25:  # デバウンス：処理中に来た重複は終了瞬間に弾く（短くて快適）
            continue
        try:
            if foreground_exe() != GAME_EXE:
                # ゲームが前面でない時：カーソルがゲーム上なら前面化してから読む（1回で済む）。
                # ゲームを指していなければ何もしない＝「戻る」は普通に効く。
                gh, gex = window_under_cursor()
                if gex != GAME_EXE:
                    continue
                focus_window(gh)
                time.sleep(0.28)                # 前面化＋ツールチップ描画待ち
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
            # 表示言語：設定（PC言語/手動）に従う
            global _ui_lang
            if _lang_mode[0] in LANGS:
                _ui_lang = _lang_mode[0]
            found, chosen = [], None
            if cands:
                ax, ay = min(cands, key=lambda c: c[1])[2:4]   # カーソル最近の枠＝指してる位置
                same = [c for c in cands if (c[2] - ax) ** 2 + (c[3] - ay) ** 2 < 80 ** 2]
                best = max(same, key=lambda c: c[0])
                if best[0] >= 0.85:
                    found, chosen = best[4], best
            if found:                             # 表示の瞬間に最新USD（叩ければ現地通貨）へ更新
                apply_live(found[0], native_ok=True)
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
        finally:
            _last_trig[0] = time.time()         # 処理完了時刻＝直後の重複シグナルだけ弾く


# ---- ポップ表示（メインスレッドで） --------------------------------------
_open = []
_hist = []                 # 価格履歴（新しい順）
_hist_win = [None]         # 履歴ウィンドウ
_hist_inner = [None]       # (canvas, inner) の参照
_hist_visible = [False]    # トレイのオン/オフ状態
_hist_limit = [50]         # 履歴の上限（0=無制限）。お気に入りは上限の対象外
_hist_status = [None]      # ヘッダの「更新中/更新時刻」ラベル
_hist_prog = [None]        # 進捗バー（Canvas）。更新中だけ可視化
_hist_prog_state = {"done": 0, "total": 0, "on": False, "phase": 0}   # 進捗とアニメ位相
_hist_geo = [None]         # 履歴ウィンドウの位置・サイズ（記憶して次回復元）
_hist_rows = []            # 表示中の行 [{rec,frame,sep,price,icon,name,ts}…]（増分更新でリストを消さない）
HIST_FILE = os.path.join(HERE, "tbh-price-history.json")   # 履歴の保存先（再起動で消えないように）
SET_FILE = os.path.join(HERE, "tbh-price-settings.json")   # 設定の保存先

# ---- 更新通知（起動時にGitHubの最新リリースを確認、新しければ控えめに告知） ----
_update_info = [None]      # 新版があれば {"ver": "1.1", "url": ...}

def _ver_tuple(s):
    out = []
    for p in str(s).split("."):
        n = "".join(ch for ch in p if ch.isdigit())
        out.append(int(n) if n else 0)
    return tuple(out)

def _check_update():
    try:
        url = f"https://api.github.com/repos/{APP_REPO}/releases/latest"
        req = urllib.request.Request(url, headers={"User-Agent": "TBH-MarketLens"})
        d = json.load(urllib.request.urlopen(req, timeout=6))
        tag = (d.get("tag_name") or "").lstrip("vV")
        if tag and _ver_tuple(tag) > _ver_tuple(APP_VERSION):
            zip_url = None
            for a in d.get("assets", []):
                if str(a.get("name", "")).lower().endswith(".zip"):
                    zip_url = a.get("browser_download_url"); break
            _update_info[0] = {"ver": tag, "zip": zip_url,
                               "url": d.get("html_url") or f"https://github.com/{APP_REPO}/releases/latest"}
    except Exception: pass

def _do_update(on_status=lambda s: None):
    """新版zipをDL→展開→ヘルパーbatが本体終了を待って差し替え→自動再起動（ワンクリック更新）。
    開発(スクリプト実行)やzip無し時はダウンロードページを開くだけ。"""
    u = _update_info[0]
    if not u: return
    if not getattr(sys, "frozen", False) or not u.get("zip"):
        webbrowser.open(u["url"]); return
    def work():
        import zipfile, subprocess, shutil as _sh
        try:
            on_status(T("downloading"))
            zpath = os.path.join(HERE, "_update.zip"); newdir = os.path.join(HERE, "_update_new")
            req = urllib.request.Request(u["zip"], headers={"User-Agent": "TBH-MarketLens"})
            with urllib.request.urlopen(req, timeout=120) as r, open(zpath, "wb") as f:
                _sh.copyfileobj(r, f)
            on_status(T("extracting"))
            if os.path.isdir(newdir): _sh.rmtree(newdir, ignore_errors=True)
            with zipfile.ZipFile(zpath) as z: z.extractall(newdir)
            inner = os.path.join(newdir, "TBH MarketLens")        # zipが1段フォルダ入りでも対応
            srcdir = inner if os.path.isdir(inner) else newdir
            bat = os.path.join(HERE, "_update.bat"); exe = os.path.join(HERE, "TBH MarketLens.exe")
            with open(bat, "w", encoding="cp932") as f:
                f.write("@echo off\r\n:wait\r\n"
                        'tasklist /fi "imagename eq TBH MarketLens.exe" | find /i "TBH MarketLens.exe" >nul '
                        "&& (timeout /t 1 >nul & goto wait)\r\n"
                        f'robocopy "{srcdir}" "{HERE}" /e /is /it >nul\r\n'
                        f'rmdir /s /q "{newdir}"\r\n' f'del "{zpath}"\r\n'
                        f'start "" "{exe}"\r\n' 'del "%~f0"\r\n')
            on_status(T("restarting"))
            subprocess.Popen(["cmd", "/c", bat], creationflags=0x00000008)   # DETACHED_PROCESS
            os._exit(0)                                           # 本体を即終了→batが差し替え＆再起動
        except Exception:
            on_status(T("update_failed"))
            try: webbrowser.open(u["url"])
            except Exception: pass
    threading.Thread(target=work, daemon=True).start()

# ---- 表示言語（ja / en）。起動時はPCの言語を自動取得して既定に ----
_lang_mode = [None]        # None=未設定（起動時にPC言語へ）, "ja" / "en"

def _detect_pc_lang():
    try:
        lid = _ctypes.windll.kernel32.GetUserDefaultUILanguage() & 0x3ff
        if lid == 0x11: return "ja"                  # 日本語の主要言語ID
        if lid == 0x04: return "zh"                  # 中国語（簡体/繁体とも主要ID 0x04）
    except Exception: pass
    try:
        import locale
        lc = (locale.getdefaultlocale()[0] or "").lower()
        if lc.startswith("ja"): return "ja"
        if lc.startswith("zh"): return "zh"
    except Exception: pass
    return "en"

def _apply_lang(m):
    global _ui_lang
    _lang_mode[0] = m
    if m in LANGS: _ui_lang = m
    _save_settings()

# ---- 発動トリガー（マウスボタン/キーボード、ユーザーが自由に割り当て） ----
_trigger = {"kind": "mouse", "value": SIDE_BUTTON}   # 既定：マウス戻る(サイド)
_trig_hook = [None]                                  # (kind, handler) 解除用
_set_win = [None]                                    # 設定ウィンドウ
_help_win = [None]                                   # 使い方ウィンドウ
_fb_win = [None]                                     # フィードバックウィンドウ
_intro_seen = [False]                                # 初回起動の使い方を表示済みか

def _trigger_label(kind=None, value=None):
    kind = kind or _trigger["kind"]; value = value if value is not None else _trigger["value"]
    if kind == "mouse":
        k = "mouse_" + str(value)
        return TR[_ui_lang].get(k) or TR["en"].get(k) or (T("mouse_prefix") + str(value))
    return " + ".join(p.capitalize() for p in str(value).split("+"))   # ctrl+shift+p → Ctrl + Shift + P

def _save_settings():
    try:
        with open(SET_FILE, "w", encoding="utf-8") as f:
            json.dump({"trigger": _trigger, "lang": _lang_mode[0], "intro_seen": _intro_seen[0],
                       "hist_geo": _hist_geo[0]},
                      f, ensure_ascii=False)
    except Exception: pass

def _load_settings():
    try:
        d = json.load(open(SET_FILE, encoding="utf-8"))
        t = d.get("trigger") or {}
        if t.get("kind") in ("mouse", "key") and t.get("value"):
            _trigger.update(kind=t["kind"], value=t["value"])
        if d.get("lang") in LANGS:
            _lang_mode[0] = d["lang"]
        _intro_seen[0] = bool(d.get("intro_seen"))
        if isinstance(d.get("hist_geo"), str): _hist_geo[0] = d["hist_geo"]
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

def _capture_trigger(on_done, on_progress=None):
    """キーは「押している組み合わせ」を最初に離した瞬間に確定（Ctrl+Shift+P等）。
    押している間は on_progress(combo) で途中経過を通知。単キー(F8)もマウスボタンも可。"""
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
            if on_progress: on_progress("+".join(pressed))   # 押している間を実況
        elif e.event_type == "up" and pressed:   # 最初に離した瞬間に組み合わせを確定
            finish("key", "+".join(pressed))
    mh = mouse.hook(on_mouse)
    kh = keyboard.hook(on_key)

def _save_hist():
    try:
        # _live は「この起動でSteamから取れた」フラグ＝保存しない（次回起動の値はもう実時刻でない＝暫定🕓に戻す）
        saved = [{k: v for k, v in r.items() if k != "_live"} for r in _hist]
        with open(HIST_FILE, "w", encoding="utf-8") as f:
            json.dump({"limit": _hist_limit[0], "hist": saved}, f, ensure_ascii=False)
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

_icon_map = [None]
def _icon_by_hash():
    """ハッシュ -> アイコンCDNハッシュ の辞書（lookupから1回だけ作る）。"""
    if _icon_map[0] is None:
        try:
            _icon_map[0] = {e.get("hash"): e.get("icon") for e in matcher.entries
                            if e.get("icon") and e.get("hash")}
        except Exception:
            _icon_map[0] = {}
    return _icon_map[0]

def _record_history(ent):
    if not ent: return
    rec = {k: ent.get(k) for k in ("ja", "en", "zh", "zh_hant", "icon", "rarity_en", "rarity_ja",
                                   "sell", "median", "volume", "cur", "hash", "type_ja", "type_en", "type", "_live")}
    rec["ts"] = time.strftime("%H:%M")
    if not rec.get("icon"):                         # 価格側エントリにicon無し→ハッシュから補完
        rec["icon"] = _icon_by_hash().get(rec.get("hash"), "")
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

def _keep_on_top(win, want_noact=lambda: True, pause=lambda: False):
    """フルスクリーン(ボーダーレス)のゲームの前へ出し続ける。要点は WS_EX_NOACTIVATE:
    これを付けるとポップをクリックしてもアクティブ化が起きない＝ゲームが前面に出てこない。
    ただし編集中(want_noact()=False)は外してキーボード入力を受けられるようにする。
    TOPMOSTは常に維持し、120ms毎に再主張して背後への回り込みを防ぐ。
    pause()=True の間は再主張を休む（他のポップと最前面を奪い合ってチラつくのを防ぐ）。"""
    try: import ctypes
    except Exception: return
    u = ctypes.windll.user32
    GWL_EXSTYLE = -20
    WS_EX_TOPMOST, WS_EX_NOACTIVATE = 0x00000008, 0x08000000
    HWND_TOPMOST = -1
    SWP = 0x0001 | 0x0002 | 0x0010   # NOSIZE | NOMOVE | NOACTIVATE
    def tick():
        if not win.winfo_exists(): return
        if not pause():                          # 休止中は z順を触らない
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

def _pill_set_text(cv, text):
    try: cv.itemconfig("txt", text=text)
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
    win = tk.Toplevel(root)
    win.overrideredirect(True); win.attributes("-topmost", True); win.config(bg=C_CARD)
    f_name = tkfont.Font(family="Yu Gothic UI", size=14, weight="bold")
    f_price = tkfont.Font(family="Yu Gothic UI", size=17, weight="bold")
    f_meta = tkfont.Font(family="Yu Gothic UI", size=9)

    if results == "__processing__":
        c = tk.Frame(win, bg=C_CARD); c.pack()      # 色枠なし＝結果ポップと統一
        lab = tk.Label(c, text="", bg=C_CARD, fg=C_ACCENT, font=f_name, padx=18, pady=12); lab.pack()
        _spin_i = [0]
        def _anim():                                # スピナーを回す＝処理中だと一目でわかる（静止文字にしない）
            if not lab.winfo_exists(): return
            lab.config(text=f"{_SPIN[_spin_i[0] % len(_SPIN)]}  {T('reading')}")
            _spin_i[0] += 1
            lab.after(90, _anim)
        _anim()
        _place(win, xy); _round_corners(win); _keep_on_top(win); _open.append(win)
        win.after(int(POPUP_SECONDS * 1000), lambda: (win.winfo_exists() and win.destroy()))
        return

    e = results[0] if results else None
    if e is None:                                     # 該当なし＝文字化けの生OCRや無関係ボタンを出さず最小表示（該当なし＋履歴だけ）
        c = tk.Frame(win, bg=C_CARD); c.pack(fill="both", expand=True)
        tk.Label(c, text=T("nomatch"), bg=C_CARD, fg=C_NAME, font=f_name).pack(padx=20, pady=(16, 8), anchor="w")
        bf = tk.Frame(c, bg=C_CARD); bf.pack(padx=14, pady=(0, 14), anchor="w")
        round_pill(bf, "🕘 " + T("history"), "#2a2f3a", C_NAME,
                   lambda: (_hist_visible.__setitem__(0, True), show_history(root)), f_meta).pack(side="left")
        xbtn = tk.Label(c, text="✕", bg=C_CARD, fg=C_META, font=f_meta, cursor="hand2")
        xbtn.place(relx=1.0, x=-10, y=8, anchor="ne")
        xbtn.bind("<Button-1>", lambda ev: win.destroy())
        xbtn.bind("<Enter>", lambda ev: xbtn.config(fg=C_NAME))
        xbtn.bind("<Leave>", lambda ev: xbtn.config(fg=C_META))
        win.bind("<Escape>", lambda ev: win.destroy())
        _place(win, xy); _round_corners(win); _keep_on_top(win); _dismiss(win); _open.append(win)
        return
    init_name = disp_name(e)
    init_rar = (e.get("rarity_en") if e else "") or ""
    en2ja = {en: ja for en, ja in RARITIES}
    state = {"entry": e, "rarity": init_rar}

    content = tk.Frame(win, bg=C_CARD); content.pack()   # 枠なし（ダークカードのみ）
    content.columnconfigure(0, weight=1)

    # アイテム名：読むだけのプレーンテキスト（編集は前面を奪うので不可。等級はマウスで選び直し可）
    name_lbl = tk.Label(content, text=init_name or "—", bg=C_CARD, fg=C_NAME, font=f_name, anchor="w")
    name_lbl.grid(row=0, column=0, sticky="we", padx=(14, 30), pady=(14, 6))   # 右上の✕分の余白

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
        txt = "▾ " + ((en2ja.get(r, r) if _ui_lang == "ja" else r) if r else T("rarity"))
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
    mkt_pill = round_pill(btnf, "🛒 " + T("mkt"), rarity_color(init_rar), "#0c0c0c", open_market, f_meta)
    mkt_pill.pack(side="left")
    def open_history():
        _hist_visible[0] = True; show_history(root)
    round_pill(btnf, "🕘 " + T("history"),
               "#2a2f3a", C_NAME, open_history, f_meta).pack(side="left", padx=(6, 0))
    # 閉じる：右上の角に配置（標準的な位置）
    xbtn = tk.Label(content, text="✕", bg=C_CARD, fg=C_META, font=f_meta, cursor="hand2")
    xbtn.place(relx=1.0, x=-10, y=8, anchor="ne")
    xbtn.bind("<Button-1>", lambda e: win.destroy())
    xbtn.bind("<Enter>", lambda e: xbtn.config(fg=C_NAME))
    xbtn.bind("<Leave>", lambda e: xbtn.config(fg=C_META))

    def render(ent):
        state["entry"] = ent
        ar = rarity_color(state["rarity"] or (ent.get("rarity_en") if ent else ""))
        price_lbl.config(fg=ar); recolor_pill(mkt_pill, ar)
        if ent:
            name_lbl.config(text=disp_name(ent) or "—")
        if ent and ent.get("sell") is not None:
            sc = ent.get("cur", 1)
            txt = f"{T('low')} {price(ent['sell'], sc)}   {T('med')} {price(ent['median'], sc)}"
            price_lbl.config(text=txt, fg=ar)       # 普通の価格色で鮮明に（概算は数値の≈だけで示す）
            cat = disp_type(ent)
            meta_lbl.config(text=f"{cat}   {T('sold')}{ent.get('volume','—')}")
        elif ent:
            price_lbl.config(text=T("noprice")); meta_lbl.config(text=ent.get("type_ja", "") or ent.get("type_en", ""))
        else:
            price_lbl.config(text=T("nomatch")); meta_lbl.config(text="")
        _place(win, xy)

    _fetching = [False]
    def _lookup(nm, rar_en):                        # 名前＋等級で引き直し→現在価格を取得→描画
        _fetching[0] = True                         # 取得中はスピナーを回す（押した手応え＝動き）
        def spin(i=0):
            if not (_fetching[0] and price_lbl.winfo_exists()): return
            price_lbl.config(text=_SPIN[i % len(_SPIN)], fg=C_META)
            price_lbl.after(90, lambda: spin(i + 1))
        spin()
        def work():
            r = matcher.match_item(nm, en2ja.get(rar_en, rar_en) if rar_en else "")
            ent = r[0] if r else None
            if ent: apply_live(ent, native_ok=True)
            def _done():
                _fetching[0] = False; render(ent)
            win.after(0, _done)
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

def _prog_draw():
    """進捗バーを描く。進んだぶん=テールカラー実線、待機中=アンバーの流れるストライプ
    （=ペース調整中でBANでないと一目でわかる）。更新していない時は空（見えない）。"""
    cv = _hist_prog[0]
    if not (cv and cv.winfo_exists()): return
    st = _hist_prog_state
    try: w = cv.winfo_width()
    except Exception: return
    if w < 4: w = 320
    h = 6
    cv.delete("all")
    if not st["on"]: return
    cv.create_rectangle(0, 0, w, h, fill="#222831", outline="")          # トラック
    done, total = st["done"], st["total"]
    frac = (done / total) if total else 0
    if frac > 0:                                                          # 確定した取得ぶん
        cv.create_rectangle(0, 0, max(h, int(w * frac)), h, fill=C_ACCENT, outline="")
    if time.time() < _render_blocked[0] and done < total:               # render待機中＝流れるアンバー帯
        seg = max(40, w // 5)
        x = int(st["phase"] * (w + seg) / 24.0) % (w + seg) - seg
        cv.create_rectangle(max(0, x), 0, min(w, x + seg), h, fill=C_WAIT, outline="")

def _prog_anim():
    """更新中だけ回り続けるアニメ：ボタンのスピナー＋件数、待機中のカウントダウン、進捗バー。"""
    st = _hist_prog_state
    if not st["on"]:
        _prog_draw(); return                                             # 終了→最後に1回消す
    st["phase"] += 1
    b = _hist_update_btn[0]
    if b and b.winfo_exists():                                           # ボタン＝スピナー＋n/total（動き＝処理中）
        sp = _SPIN[st["phase"] % len(_SPIN)]
        _pill_set_text(b, f"{sp} {st['done']}/{st['total']}")
    s = _hist_status[0]
    if s and s.winfo_exists():
        remain = _render_blocked[0] - time.time()
        if remain > 0:                                                  # render待機中＝残り秒（数字=言語非依存のUI）
            s.config(text=f"⏳ {int(remain)+1}s", fg=C_WAIT)
        else:
            s.config(text=f"{st['done']} / {st['total']}", fg=C_ACCENT)
    _prog_draw()
    w = _hist_win[0]
    if w and w.winfo_exists():
        w.after(100, _prog_anim)

def _flash_price(rd):
    """価格が更新された瞬間に一瞬だけ明るく光らせる＝どの行が今更新されたか目で追える。"""
    lbl = rd.get("price")
    if not (lbl and lbl.winfo_exists()): return
    base = lbl.cget("fg")
    lbl.config(fg="#bdffe0")
    lbl.after(260, lambda: lbl.winfo_exists() and lbl.config(fg=base))

ICON_DIR = os.path.join(HERE, "iconcache")    # アイコンのローカルキャッシュ（CDNから取得→保存）
ICON_PX = 56                                  # 履歴セルいっぱいの表示サイズ（ドット絵=NEAREST）
_icon_cache = {}                              # hash -> ImageTk.PhotoImage
_blank_icon = [None]                          # 透明（アイコン取得待ちの整列用）
_ph_icons = {}                                # レア度色 -> プレースホルダ画像（アイコンデータが無い品用）

def _placeholder_icon(color):
    """アイコンデータが無いアイテム用の控えめなレア度色タイル。"""
    if color not in _ph_icons:
        try:
            from PIL import ImageTk, ImageDraw
            c = color.lstrip("#")
            rgb = tuple(int(c[i:i + 2], 16) for i in (0, 2, 4)) if len(c) == 6 else (120, 120, 120)
            im = Image.new("RGBA", (ICON_PX, ICON_PX), (0, 0, 0, 0))
            ImageDraw.Draw(im).rounded_rectangle([4, 4, ICON_PX - 4, ICON_PX - 4], radius=10,
                                                 fill=rgb + (70,), outline=rgb + (160,), width=2)
            _ph_icons[color] = ImageTk.PhotoImage(im)
        except Exception:
            _ph_icons[color] = _blank_icon[0]
    return _ph_icons[color]

def _get_icon(h, cb):
    """アイテムアイコン(28px)を非同期取得。SteamのCDN→ローカルキャッシュ。取れたら cb(photo) を呼ぶ。"""
    if not h: return
    ph = _icon_cache.get(h)
    if ph is not None: cb(ph); return
    def work():
        try:
            import hashlib
            from PIL import ImageTk
            os.makedirs(ICON_DIR, exist_ok=True)
            # ハッシュは先頭が全アイテム共通＝切り詰めると衝突。md5で一意なファイル名にする。
            fp = os.path.join(ICON_DIR, hashlib.md5(h.encode()).hexdigest() + ".png")
            if not os.path.exists(fp):
                url = "https://community.cloudflare.steamstatic.com/economy/image/" + h + "/96x96"
                req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
                with urllib.request.urlopen(req, timeout=8) as r, open(fp, "wb") as f:
                    f.write(r.read())
            im = Image.open(fp).convert("RGBA")
            bb = im.getbbox()                        # 透明の余白を切ってセルいっぱいに
            if bb: im = im.crop(bb)
            im = im.resize((ICON_PX, ICON_PX), Image.NEAREST)
            w = _hist_win[0]
            if not (w and w.winfo_exists()): return
            def ready():
                if h not in _icon_cache:
                    try: _icon_cache[h] = ImageTk.PhotoImage(im)
                    except Exception: return
                cb(_icon_cache[h])
            w.after(0, ready)
        except Exception: pass
    threading.Thread(target=work, daemon=True).start()

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
            apply_live(ent, native_ok=True, force=True)
            new = {k: ent.get(k) for k in ("ja", "en", "rarity_en", "rarity_ja", "sell", "median",
                                           "volume", "cur", "_live", "hash", "type_ja", "type_en", "type")}
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
    _ask_text(T("rename_title"),
              rec.get("ja") or rec.get("en") or "", on_ok)

def _hist_apply_cache():
    """履歴の価格を、メモリ内キャッシュ（一括USD/現地）だけで再表示（ネット非使用＝言語切替で叩かない）。"""
    _hist_gen[0] += 1                             # 実行中の全部更新（旧通貨）を中断
    for rec in _hist:
        if not rec.get("hash"): continue
        apply_live(rec, native_ok=True, cache_only=True)

_hist_gen = [0]            # 全部更新の世代。新しい更新が始まると古い取得は中断（言語連続切替の競合防止）
_hist_updating = [False]   # 全部更新が実行中か（連打で多重起動しないように）
_hist_update_btn = [None]  # 「全部更新」ボタン（実行中は表示を変える）

def _hist_update_all(force=True):
    if _hist_updating[0]: return                       # 実行中の連打は無視（多重起動しない）
    recs = [r for r in list(_hist) if r.get("hash")]
    total = len(recs)
    if not total: return
    _hist_updating[0] = True
    _hist_gen[0] += 1; gen = _hist_gen[0]
    def _btn(text):                                    # ボタン表示を実行中⇄通常で切替（押せない理由を可視化）
        b = _hist_update_btn[0]
        if b and b.winfo_exists(): _pill_set_text(b, text)
    def alive(): return gen == _hist_gen[0]
    _hist_prog_state.update(done=0, total=total, on=True, phase=0)   # 進捗バー＋スピナー開始
    _hist_after(_prog_anim)
    import queue as _q
    work_q = _q.Queue()
    for r in recs: work_q.put(r)
    done = [0]; lock = threading.Lock()
    def worker():
        while alive():                                 # 上書き(言語切替/再押下)されたら中断
            try: rec = work_q.get_nowait()
            except _q.Empty: return
            before = rec.get("sell")
            apply_live(rec, native_ok=False, force=force)   # 単品USD（search/render・BANされない）
            if not alive(): return
            if rec.get("sell") != before or "cur" in rec:
                _hist_after(lambda rec=rec: _hist_update_price(rec))   # その場で1行だけ更新＋光らせる
            with lock:
                done[0] += 1
            _hist_prog_state["done"] = done[0]         # 進捗バー/スピナーが拾う（アニメ側が描画）
    threads = [threading.Thread(target=worker, daemon=True) for _ in range(3)]  # 一括取得を共有（実質1リクエスト群）
    for t in threads: t.start()
    def waiter():
        for t in threads: t.join()
        _hist_updating[0] = False                      # 完了→再度押せる
        _hist_prog_state["on"] = False                 # バー消灯＋アニメ停止
        if not alive():
            _hist_after(lambda: _btn("↻ " + T("update_all")))
            return
        _save_hist()
        def _fin():                                    # ✓を一瞬見せてから通常表示へ（完了の手応え）
            _btn("✓ " + T("update_all"))
            s = _hist_status[0]
            if s and s.winfo_exists():
                s.config(text="✓ " + time.strftime("%H:%M:%S"), fg=C_ACCENT)
            b = _hist_update_btn[0]
            if b and b.winfo_exists():
                b.after(1400, lambda: b.winfo_exists() and _pill_set_text(b, "↻ " + T("update_all")))
        _hist_after(_fin)
    threading.Thread(target=waiter, daemon=True).start()

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
    round_pill(bf, T("cancel"), "#2a2f3a", C_NAME, d.destroy, f).pack(side="right")
    d.bind("<Return>", ok); d.bind("<Escape>", lambda e: d.destroy())
    _grab_foreground(d)
    ent.focus_set()
    try: ent.select_range(0, "end"); ent.icursor("end")
    except Exception: pass

def _row_menu(ev, rec):
    m = tk.Menu(_hist_win[0], tearoff=0, bg="#0d1016", fg=C_NAME, activebackground="#2a2f3a",
                activeforeground="#ffffff", bd=0)
    ja = _ui_lang == "ja"
    m.add_command(label=(T("unfav") if rec.get("fav") else T("fav")),
                  command=lambda: _hist_fav(rec))
    m.add_command(label=T("rename"), command=lambda: _hist_rename(rec))
    rm = tk.Menu(m, tearoff=0, bg="#0d1016", fg=C_NAME, activebackground="#2a2f3a",
                 activeforeground="#ffffff", bd=0)
    for en, jaa in RARITIES:
        rm.add_command(label=(jaa if ja else en), foreground=rarity_color(en),
                       command=lambda en=en: _hist_set_rarity(rec, en))
    m.add_cascade(label=T("rarity_change"), menu=rm)
    m.add_separator()
    m.add_command(label=T("delete"), command=lambda: _hist_delete(rec))
    m.tk_popup(ev.x_root, ev.y_root)


def _set_row_price(rd):
    rec = rd["rec"]
    if rec.get("sell") is not None:
        sc = rec.get("cur", 1)
        txt = f"{T('low')} {price(rec['sell'], sc)}   {T('med')} {price(rec['median'], sc)}"
        rd["price"].config(text=txt, fg=C_PRICE)   # 普通の価格色（概算は数値の≈だけで示す）
    else:
        rd["price"].config(text=T("noprice"), fg=C_META)

def _build_hist_row(rec):
    """1行ぶんのウィジェットを作って返す（packは呼び出し側）。"""
    inner = _hist_inner[0][1]
    ar = rarity_color(rec.get("rarity_en") or "")
    nm = disp_name(rec) or "?"; rj = disp_rarity(rec); star = "★ " if rec.get("fav") else ""
    row = tk.Frame(inner, bg=C_CARD, cursor="hand2")
    init_img = _blank_icon[0] if rec.get("icon") else _placeholder_icon(ar)   # 無い品はレア度色タイル
    icon_lbl = tk.Label(row, bg=C_CARD, image=init_img); icon_lbl.pack(side="left", padx=(2, 8))
    col = tk.Frame(row, bg=C_CARD); col.pack(side="left", fill="x", expand=True)
    top = tk.Frame(col, bg=C_CARD); top.pack(fill="x")
    name_lbl = tk.Label(top, text=star + nm + (("  " + rj) if rj else ""), bg=C_CARD, fg=ar,
                        font=("Yu Gothic UI", 10, "bold"), anchor="w"); name_lbl.pack(side="left")
    ts_lbl = tk.Label(top, text=rec.get("ts", ""), bg=C_CARD, fg=C_META,
                      font=("Yu Gothic UI", 8), anchor="e"); ts_lbl.pack(side="right")
    price_lbl = tk.Label(col, text="", bg=C_CARD, font=("Yu Gothic UI", 9), anchor="w"); price_lbl.pack(fill="x")
    tk.Label(col, text=disp_type(rec), bg=C_CARD, fg=C_META, font=("Yu Gothic UI", 8), anchor="w").pack(fill="x")
    sep = tk.Frame(inner, bg="#2a2f3a", height=1)
    rd = {"rec": rec, "frame": row, "sep": sep, "price": price_lbl, "icon": icon_lbl, "name": name_lbl, "ts": ts_lbl}
    _set_row_price(rd)
    if rec.get("icon"):
        _get_icon(rec["icon"], lambda ph, L=icon_lbl: L.winfo_exists() and L.config(image=ph))
    def _open_mkt(ev, h=rec.get("hash")):
        if h:
            try: webbrowser.open(f"https://steamcommunity.com/market/listings/{APPID}/" + urllib.parse.quote(h))
            except Exception: pass
    stack = [row]
    while stack:
        w = stack.pop(); stack += w.winfo_children()
        w.bind("<Button-1>", _open_mkt)
        w.bind("<Button-3>", lambda ev, r=rec: _row_menu(ev, r))
    return rd

def _hist_scroll():
    c = _hist_inner[0][0] if _hist_inner[0] else None
    if c:
        try: c.update_idletasks(); c.configure(scrollregion=c.bbox("all"))
        except Exception: pass

def _refresh_history():
    """全再構築（開いた時・削除/お気に入り/改名/等級変更/言語/上限変更など構造が変わる時のみ）。"""
    if not (_hist_win[0] and _hist_win[0].winfo_exists() and _hist_inner[0]): return
    inner = _hist_inner[0][1]
    for w in inner.winfo_children():
        try: w.destroy()
        except Exception: pass
    _hist_rows.clear()
    if _blank_icon[0] is None:
        try:
            from PIL import ImageTk
            _blank_icon[0] = ImageTk.PhotoImage(Image.new("RGBA", (ICON_PX, ICON_PX), (0, 0, 0, 0)))
        except Exception: pass
    if not _hist:
        tk.Label(inner, text=T("hist_empty"), bg=C_CARD, fg=C_META, anchor="w").pack(fill="x", padx=12, pady=10)
        _hist_scroll(); return
    for rec in sorted(_hist, key=lambda r: (not r.get("fav"),)):   # お気に入りを上に
        rd = _build_hist_row(rec)
        rd["frame"].pack(fill="x", padx=6, pady=(4, 0)); rd["sep"].pack(fill="x", padx=6, pady=(4, 0))
        _hist_rows.append(rd)
    _hist_scroll()

def _hist_update_price(rec):
    """1件の価格だけ、その場で書き換える（リストは消さない）＋一瞬光らせて更新を可視化。"""
    for rd in _hist_rows:
        if rd["rec"] is rec:
            if rd["price"].winfo_exists(): _set_row_price(rd); _flash_price(rd)
            return

def _hist_sync_top():
    """レンズ時：最新の1件だけ反映。既存なら価格/時刻を更新、新規なら1行だけ差し込む（全消ししない）。"""
    if not (_hist_win[0] and _hist_win[0].winfo_exists() and _hist_inner[0] and _hist): return
    inner = _hist_inner[0][1]
    rec = _hist[0]
    for rd in _hist_rows:                            # 既出（同一recの再レンズ）→その場更新
        if rd["rec"] is rec:
            if rd["price"].winfo_exists(): _set_row_price(rd); rd["ts"].config(text=rec.get("ts", ""))
            return
    if not _hist_rows:                               # 空表示ラベルがあれば消す
        for w in inner.winfo_children():
            try: w.destroy()
            except Exception: pass
    rd = _build_hist_row(rec)                         # 新規→お気に入りの下（非お気に入りの先頭）に差し込む
    anchor = next((r for r in _hist_rows if not r["rec"].get("fav")), None)
    if anchor:
        rd["frame"].pack(fill="x", padx=6, pady=(4, 0), before=anchor["frame"])
        rd["sep"].pack(fill="x", padx=6, pady=(4, 0), before=anchor["frame"])
        _hist_rows.insert(_hist_rows.index(anchor), rd)
    else:
        rd["frame"].pack(fill="x", padx=6, pady=(4, 0)); rd["sep"].pack(fill="x", padx=6, pady=(4, 0))
        _hist_rows.append(rd)
    present = {id(r) for r in _hist}                  # 上限で押し出された行を撤去
    for rd2 in _hist_rows[:]:
        if id(rd2["rec"]) not in present:
            try: rd2["frame"].destroy(); rd2["sep"].destroy()
            except Exception: pass
            _hist_rows.remove(rd2)
    _hist_scroll()

def show_history(root):
    if _hist_win[0] and _hist_win[0].winfo_exists():
        _hist_win[0].deiconify(); _refresh_history(); return
    win = tk.Toplevel(root)
    win.title("TBH MarketLens — " + T("hist_title"))
    win.config(bg=C_CARD); win.geometry(_hist_geo[0] or "360x460"); win.attributes("-topmost", True)
    win.protocol("WM_DELETE_WINDOW", lambda: toggle_history(root))   # ×でオフに同期
    def _remember_geo(e):                        # 移動/リサイズを記憶（次回復元）
        if e.widget is win and win.winfo_width() > 80:
            _hist_geo[0] = win.geometry()
    win.bind("<Configure>", _remember_geo)
    f_hbtn = tkfont.Font(family="Yu Gothic UI", size=9)
    hdr = tk.Frame(win, bg=C_CARD); hdr.pack(fill="x", padx=12, pady=(10, 0))
    tk.Label(hdr, text=T("hist_title"), bg=C_CARD, fg=C_NAME,
             font=("Yu Gothic UI", 13, "bold"), anchor="w").pack(side="left")
    _hist_update_btn[0] = round_pill(hdr, ("⏳ " + T("updating_btn")) if _hist_updating[0] else ("↻ " + T("update_all")),
                                     C_ACCENT, "#0c0c0c", _hist_update_all, f_hbtn)
    _hist_update_btn[0].pack(side="right")
    _hist_status[0] = tk.Label(win, text="", bg=C_CARD, fg=C_ACCENT,
                               font=("Yu Gothic UI", 9), anchor="w")
    _hist_status[0].pack(side="top", fill="x", padx=12, pady=(0, 2))
    _hist_prog[0] = tk.Canvas(win, height=6, bg=C_CARD, highlightthickness=0)   # 進捗バー（更新中だけ見える）
    _hist_prog[0].pack(side="top", fill="x", padx=12, pady=(0, 4))
    if _hist_updating[0]: _prog_anim()                 # 開き直した時に更新中なら即アニメ再開
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
    # ポップ表示中(_open非空)は最前面の再主張を休む＝価格ポップと奪い合わずチラつかない。
    _keep_on_top(win, pause=lambda: bool(_open))
    _refresh_history()

def hide_history():
    if _hist_win[0]:
        try:
            if _hist_win[0].winfo_width() > 80: _hist_geo[0] = _hist_win[0].geometry()
            _hist_win[0].withdraw()
        except Exception: pass
    _save_settings()           # 位置・サイズを保存

def toggle_history(root):
    _hist_visible[0] = not _hist_visible[0]
    if _hist_visible[0]: show_history(root)
    else: hide_history()


def show_help(root):
    if _help_win[0] and _help_win[0].winfo_exists():
        _help_win[0].deiconify(); _help_win[0].lift(); return
    ja = _ui_lang == "ja"
    win = tk.Toplevel(root); win.config(bg=C_CARD); win.attributes("-topmost", True); win.resizable(False, False)
    win.title(f"{APP_NAME} — " + T("howto"))
    win.protocol("WM_DELETE_WINDOW", win.withdraw)
    ft = tkfont.Font(family="Yu Gothic UI", size=15, weight="bold")
    fh = tkfont.Font(family="Yu Gothic UI", size=10, weight="bold")
    fb = tkfont.Font(family="Yu Gothic UI", size=10)
    W = 380
    tk.Label(win, text=APP_NAME, bg=C_CARD, fg=C_ACCENT, font=ft, anchor="w").pack(fill="x", padx=22, pady=(18, 0))
    tk.Label(win, text=T("help_main"), bg=C_CARD, fg=C_NAME, font=fh, anchor="w", justify="left",
             wraplength=W - 16).pack(fill="x", padx=22, pady=(2, 8))
    tk.Label(win, text=T("help_key"), bg=C_CARD, fg=C_META, font=fb, anchor="w", justify="left",
             wraplength=W - 16).pack(fill="x", padx=22, pady=(0, 2))
    tk.Frame(win, bg="#2a2f3a", height=1).pack(fill="x", padx=22, pady=(10, 8))
    for t in T("help_tips"):
        tk.Label(win, text="・ " + t, bg=C_CARD, fg=C_META, font=fb, anchor="w",
                 justify="left", wraplength=W - 8).pack(fill="x", padx=22, pady=1)
    round_pill(win, T("close"), C_ACCENT, "#0c0c0c", win.withdraw, fb).pack(pady=(12, 18))
    win.update_idletasks()
    win.geometry(f"{max(W, win.winfo_reqwidth())}x{win.winfo_reqheight()}")
    _help_win[0] = win
    _keep_on_top(win)


def show_feedback(root):
    if _fb_win[0] and _fb_win[0].winfo_exists():
        _fb_win[0].deiconify(); _fb_win[0].lift(); return
    win = tk.Toplevel(root); win.config(bg=C_CARD); win.attributes("-topmost", True); win.resizable(False, False)
    win.title(f"{APP_NAME} — " + T("fb_title")); win.protocol("WM_DELETE_WINDOW", win.withdraw)
    fbf = tkfont.Font(family="Yu Gothic UI", size=11, weight="bold")
    f = tkfont.Font(family="Yu Gothic UI", size=10)
    tk.Label(win, text=T("fb_title"), bg=C_CARD, fg=C_NAME, font=fbf, anchor="w").pack(fill="x", padx=18, pady=(16, 2))
    tk.Label(win, text=T("fb_hint"), bg=C_CARD, fg=C_META, font=f, anchor="w", justify="left",
             wraplength=360).pack(fill="x", padx=18, pady=(0, 8))
    txt = tk.Text(win, width=42, height=6, bg="#0d1016", fg=C_NAME, insertbackground=C_NAME,
                  relief="flat", font=f, wrap="word"); txt.pack(padx=18)
    tk.Label(win, text=T("fb_contact"), bg=C_CARD, fg=C_META, font=f, anchor="w").pack(fill="x", padx=18, pady=(8, 2))
    cvar = tk.StringVar()
    tk.Entry(win, textvariable=cvar, bg="#0d1016", fg=C_NAME, insertbackground=C_NAME,
             relief="flat", font=f).pack(fill="x", padx=18, ipady=4, ipadx=4)
    status = tk.Label(win, text="", bg=C_CARD, fg=C_ACCENT, font=f, anchor="w")
    status.pack(fill="x", padx=18, pady=(6, 2))
    def send():
        msg = txt.get("1.0", "end").strip()
        if not msg: return
        status.config(text="…", fg=C_META)
        payload = json.dumps({"msg": msg, "contact": cvar.get().strip(),
                              "ver": APP_VERSION, "lang": _ui_lang}).encode("utf-8")
        def work():
            ok = False
            try:
                req = urllib.request.Request(FEEDBACK_URL, data=payload,
                                             headers={"content-type": "application/json", "User-Agent": "MarketLens"})
                urllib.request.urlopen(req, timeout=10).read(); ok = True
            except Exception: ok = False
            def done():
                if not win.winfo_exists(): return
                if ok:
                    status.config(text=T("fb_thanks"), fg=C_ACCENT)
                    txt.delete("1.0", "end"); cvar.set("")
                    win.after(1500, win.withdraw)
                else:
                    status.config(text=T("fb_fail"), fg=C_ERR)
            win.after(0, done)
        threading.Thread(target=work, daemon=True).start()
    bf = tk.Frame(win, bg=C_CARD); bf.pack(fill="x", padx=18, pady=(4, 16))
    round_pill(bf, T("send"), C_ACCENT, "#0c0c0c", send, fbf).pack(side="right")
    _fb_win[0] = win
    win.update_idletasks(); win.geometry(f"{win.winfo_reqwidth()}x{win.winfo_reqheight()}")
    _grab_foreground(win); txt.focus_set()           # 入力できるよう前面フォーカス（NOACTIVATEは付けない）


def show_settings(root):
    if _set_win[0] and _set_win[0].winfo_exists():
        _set_win[0].deiconify(); _set_win[0].lift(); return
    ja = _ui_lang == "ja"
    win = tk.Toplevel(root); win.title("TBH MarketLens — " + T("settings_title"))
    win.config(bg=C_CARD); win.attributes("-topmost", True); win.resizable(False, False)
    win.protocol("WM_DELETE_WINDOW", win.withdraw)
    f = tkfont.Font(family="Yu Gothic UI", size=12)
    fb = tkfont.Font(family="Yu Gothic UI", size=11, weight="bold")
    fs = tkfont.Font(family="Yu Gothic UI", size=9)

    state = {"capturing": False}
    def section(title):                          # カテゴリーごとの枠（見出し付きカード）
        c = tk.Frame(win, bg="#11141a", highlightbackground="#2a2f3a", highlightthickness=1)
        c.pack(fill="x", padx=16, pady=(14, 0))
        tk.Label(c, text=title, bg="#11141a", fg=C_ACCENT, font=fb, anchor="w").pack(fill="x", padx=14, pady=(10, 8))
        return c

    # ── 表示言語 ──
    c1 = section(T("language"))
    langf = tk.Frame(c1, bg="#11141a"); langf.pack(fill="x", padx=14, pady=(0, 14))
    def choose_lang(m):
        if _lang_mode[0] == m: return
        _apply_lang(m)
        # 言語が混ざらないよう、開いている副ウィンドウは破棄→次回開いた時に新言語で再生成
        for w in (_help_win, _fb_win, _hist_win, _hist_inner):
            if w[0] is not None:
                try:
                    if hasattr(w[0], "destroy"): w[0].destroy()
                except Exception: pass
                w[0] = None
        _hist_apply_cache()                          # 新通貨のキャッシュ価格を反映（ネット非使用）
        if _hist_visible[0]:
            show_history(root)                       # 履歴は開いていたら新言語で開き直す
        geo = win.geometry()
        pos = ("+" + geo.split("+", 1)[1]) if "+" in geo else ""   # 位置を保持して建て直し（動かない）
        win.destroy(); _set_win[0] = None; show_settings(root)
        if pos and _set_win[0]:
            try: _set_win[0].geometry(pos)
            except Exception: pass
    for m in LANGS:
        on = _lang_mode[0] == m
        round_pill(langf, ("● " if on else "") + LANG_NAMES[m], C_ACCENT if on else "#2a2f3a",
                   "#0c0c0c" if on else C_NAME, lambda m=m: choose_lang(m), fs).pack(side="left", padx=(0, 6))

    # ── 発動キー ──
    c2 = section(T("shortcut"))
    field = tk.Label(c2, text=_trigger_label(), bg="#0d1016", fg=C_ACCENT, font=f,
                     anchor="w", cursor="hand2", padx=12, pady=10)
    field.pack(fill="x", padx=14)
    tk.Label(c2, text=T("capture_hint"),
             bg="#11141a", fg=C_META, font=fs, anchor="w", justify="left",
             wraplength=300).pack(fill="x", padx=14, pady=(6, 4))

    def start_capture(*_):
        if state["capturing"]: return
        state["capturing"] = True
        field.config(text=T("capture_prompt"), fg=C_ERR)
        def done(kind, value):
            def apply():
                state["capturing"] = False
                _trigger.update(kind=kind, value=value)
                _bind_trigger(); _save_settings()
                if field.winfo_exists(): field.config(text=_trigger_label(), fg=C_ACCENT)
            if win.winfo_exists(): win.after(0, apply)
        def prog(combo):                         # 押している最中のキーを実況表示
            def up():
                if field.winfo_exists() and state["capturing"]:
                    field.config(text=_trigger_label("key", combo), fg=C_NAME)
            if win.winfo_exists(): win.after(0, up)
        _capture_trigger(done, prog)
    field.bind("<Button-1>", start_capture)
    def reset():
        _trigger.update(kind="mouse", value="x"); _bind_trigger(); _save_settings()
        field.config(text=_trigger_label(), fg=C_ACCENT)
    rf = tk.Frame(c2, bg="#11141a"); rf.pack(fill="x", padx=14, pady=(2, 12))
    round_pill(rf, T("reset_default"),
               "#2a2f3a", C_NAME, reset, fs).pack(side="left")

    # ── フッター：クレジット＋控えめな寄付＋免責 ──
    hf = tk.Frame(win, bg=C_CARD); hf.pack(fill="x", padx=18, pady=(6, 0))
    round_pill(hf, "❓ " + T("howto"), "#2a2f3a", C_NAME,
               lambda: show_help(root), fs).pack(side="left")
    round_pill(hf, "💬 " + T("feedback"), "#2a2f3a", C_NAME,
               lambda: show_feedback(root), fs).pack(side="left", padx=(6, 0))

    foot = tk.Frame(win, bg=C_CARD); foot.pack(fill="x", padx=18, pady=(10, 2))
    tk.Label(foot, text=f"{APP_NAME} v{APP_VERSION} · by {APP_AUTHOR}",
             bg=C_CARD, fg=C_META, font=fs, anchor="w").pack(side="left")
    if KOFI_URL:
        round_pill(foot, T("support"), "#2a2f3a", C_NAME,
                   lambda: webbrowser.open(KOFI_URL), fs).pack(side="right")
    if _update_info[0]:                          # 新版があればワンクリック更新
        u = _update_info[0]
        def _ustatus(s):
            if win.winfo_exists(): win.after(0, lambda: _pill_set_text(upill, s))
        upill = round_pill(foot, f"⬆ {T('update_btn')} v{u['ver']}", C_ACCENT, "#0c0c0c",
                           lambda: _do_update(_ustatus), fs)
        upill.pack(side="right", padx=(0, 8))
    tk.Label(win, text=T("disclaimer"),
             bg=C_CARD, fg="#5a5f6a", font=fs, anchor="w").pack(fill="x", padx=18, pady=(0, 14))
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
            if results == "__help__":              # 使い方を開く
                show_help(root)
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
            # レンズ時は最新1件だけ増分反映（全消ししない＝チラつかない）。読み取り中は何もしない。
            if _hist_visible[0] and results != "__processing__":
                _hist_sync_top()
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
    def _limit_label(n):
        return T("unlimited") if n == 0 else T("items", n=n)
    limit_menu = pystray.Menu(*[
        pystray.MenuItem(lambda item, n=n: _limit_label(n), _mk_limit(n),
                         checked=lambda item, n=n: _hist_limit[0] == n, radio=True)
        for n in (20, 50, 100, 200, 0)
    ])
    menu = pystray.Menu(
        pystray.MenuItem(lambda item: f"⬆ {T('update_tray')} v{(_update_info[0] or {}).get('ver','')}",
                         lambda icon, item: _do_update(),
                         visible=lambda item: _update_info[0] is not None),
        pystray.MenuItem(lambda item: f"{T('tray_key')}{_trigger_label()}", None, enabled=False),
        pystray.MenuItem(lambda item: T("howto"),
                         lambda icon, item: PQ.put(("__help__", None, None))),
        pystray.MenuItem(lambda item: T("tray_settings"),
                         lambda icon, item: PQ.put(("__settings__", None, None))),
        pystray.MenuItem(lambda item: T("tray_history"), _toggle_hist,
                         checked=lambda item: _hist_visible[0]),
        pystray.MenuItem(lambda item: T("tray_limit"), limit_menu),
        pystray.MenuItem(lambda item: T("tray_quit"), _quit),
    )
    pystray.Icon("tbh_marketlens", tray_image(), "TBH MarketLens", menu).run()


# ---- main ----------------------------------------------------------------
def main():
    _load_hist()                                               # 保存済み履歴を復元
    _im = _icon_by_hash()                                       # 既存履歴のアイコンをハッシュから補完
    for _r in _hist:
        if not _r.get("icon") and _r.get("hash"): _r["icon"] = _im.get(_r["hash"], "")
    _load_settings()                                           # 保存済み設定を復元
    if _lang_mode[0] is None:                                  # 初回はPCの言語を既定に
        _lang_mode[0] = _detect_pc_lang()
    _apply_lang(_lang_mode[0])                                 # _ui_langへ反映
    threading.Thread(target=_check_update, daemon=True).start()   # 新版チェック（非同期）
    threading.Thread(target=fetch_rate, daemon=True).start()      # 為替レート（概算フォールバック用）
    root = tk.Tk()
    root.withdraw()
    threading.Thread(target=ocr_worker, daemon=True).start()    # OCR常駐ワーカー（初期化1回）
    _bind_trigger()                                             # 設定されたキー/ボタンで発動（既定:マウス戻る）
    threading.Thread(target=run_tray, args=(root,), daemon=True).start()
    if not _intro_seen[0]:                                     # 初回起動：使い方を表示
        _intro_seen[0] = True; _save_settings()
        root.after(700, lambda: show_help(root))
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
            mb.showerror("TBH MarketLens", T("err_start"))
        except Exception:
            pass
