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
import os, sys, json, threading, queue, traceback, time, webbrowser, urllib.parse, urllib.request, urllib.error, re, uuid
import tkinter as tk
from tkinter import font as tkfont
from tkinter import ttk

# ---- 設定 ----------------------------------------------------------------
APP_NAME      = "TBH MarketLens"
APP_VERSION   = "1.1.1"
APP_AUTHOR    = "Ghost Shark Robotics"
KOFI_URL      = "https://ko-fi.com/ghostsharkrobotics"        # Ko-fi（空なら寄付ボタン非表示）
APP_REPO      = "GhostSharkRobotics/tbh-marketlens"           # 更新通知の取得元（GitHub Releases）
FEEDBACK_URL  = "https://tbh-stats.monoqulo.workers.dev/feedback"   # アプリ内フィードバック送信先（Worker→Slack）
STATS_URL     = "https://tbh-stats.monoqulo.workers.dev/ml"         # 匿名の利用テレメトリ送信先（IP非保存・国はエッジ付与）
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
DEBUG_UI      = True               # Trueで押下毎に「撮影＋枠＋読取＋結果」を1枚のウィンドウ表示（クリックで閉じる窓）
# 配色
C_CARD, C_ACCENT = "#1a1d24", "#2dd4bf"
_KEYCLR = "#ff00fe"   # 角丸の外側を透過させる魔法色（どの配色とも被らない）
C_NAME, C_JA, C_PRICE, C_META, C_ERR = "#ffffff", "#8ab4f8", "#34d399", "#8b909a", "#f87171"
C_PRICE_DIM = "#6f8a80"   # 暫定価格（リアルタイム確定前）。確定=C_PRICE鮮明 / 暫定=この控えめ色＋🕓
C_WAIT = "#e0a040"        # 待機中（レート制限＝ペース調整, BANではない）のアンバー
RARITY_COLORS = {"Common": "#c8c8c8", "Uncommon": "#5ce65c", "Rare": "#5b9bff",
                 "Legendary": "#f5a623", "Immortal": "#ff5252", "Arcana": "#c061ff",
                 "Beyond": "#ff5fb0", "Celestial": "#34d6e6", "Divine": "#ffe14d", "Cosmic": "#ff8a5c"}
C_NORARITY = "#c8ccd4"        # レア度の無いアイテム（CRAFTING素材等。データ上rarity_en="")の中立色
def rarity_color(r):
    # 既定をC_ACCENT(ティール≒Celestialのシアン)にすると、レア度無しの素材が色付き＝紛らわしい→中立グレー
    return RARITY_COLORS.get(r) or C_NORARITY
_ui_lang = "ja"                    # 直近に判定したゲーム言語（ja/en）
_suggest_lang = [None]             # 該当なし時に検出した「ゲームの言語」。ポップで切替を促す
_LANG_NAME = {"ja": "日本語", "en": "English", "zh": "中文"}   # 言語の表示名（どの言語でも同じ）
# ===== 文言カタログ（全UI文字列はここ＋T()経由。言語追加はLANGSとTRに列を足すだけ） =====
LANGS = ("ja", "en", "zh")
LANG_NAMES = {"ja": "日本語", "en": "English", "zh": "中文"}   # 言語自身の表示名（モード非依存のデータ）
TR = {
    "ja": {
        "low": "最安", "med": "中央値", "lst": "出品", "sold": "売買", "quote": "相場", "sort_added": "追加日", "hist_last": "最終",
        "mkt": "クリックでSteamマーケットを開く", "noprice": "市場価格なし（非取引）", "nolisting": "出品なし",
        "nomatch": "該当なし", "reading": "🔍 読み取り中…", "read": "読取", "lang_switch": "🌐 ゲームは{lang}のよう → 切替",
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
        "startup": "起動", "autostart_label": "Windowsと一緒に起動", "autostart_hint": "サインイン時にMarketLensを自動で起動します。",
        "alwaystop_label": "履歴・出品待ちを常に前面", "alwaystop_hint": "オフにすると他のウィンドウの後ろに回せます（レンズの価格ポップは常に前面のまま）。",
        "privacy": "利用統計", "telemetry_label": "匿名の利用統計を送る",
        "telemetry_hint": "改善のため、起動・参照したアイテム・エラーを匿名で送ります。IP・Steam在庫・個人情報は送りません。いつでもオフにできます。",
        "help_main": "アイテムに合わせて発動キーを押すと、そのアイテムの\nSteamマーケット価格（最安値・中央値）が出ます。",
        "help_key": "発動キーの既定はマウスのサイドボタン（戻る）。「設定」で変更できます。",
        "help_tips": ["ポップは 外をクリック / カーソルを外す / Esc で閉じる",
                      "名前や等級が違う時は履歴一覧の右クリックで修正",
                      "履歴：トレイ『履歴一覧』で表示。右クリックでお気に入り・名前変更・レア度・削除、『全部更新』も",
                      "発動キー・表示言語は『設定』で変更",
                      "安全：ゲームには干渉しません（自分の画面OCR＋キーのみ）"],
        "ocr_missing_title": "文字認識(OCR)の言語が未インストール",
        "ocr_missing_msg": "このPCに{lang}の文字認識が入っていないため、アイテム名を読み取れません。\nWindowsの「設定 → 時刻と言語 → 言語と地域」で{lang}を追加すると使えるようになります。",
        "ocr_open_settings": "言語設定を開く",
        "close": "閉じる",
        "tray_key": "キー：", "tray_settings": "設定", "tray_history": "履歴一覧",
        "tray_limit": "履歴の上限", "tray_quit": "終了", "unlimited": "無制限", "items": "{n} 件",
        "mouse_x": "マウス サイド(戻る)", "mouse_x2": "マウス サイド(進む)", "mouse_middle": "マウス 中ボタン",
        "mouse_left": "マウス 左", "mouse_right": "マウス 右", "mouse_prefix": "マウス ",
        "dbg_title": "デバッグ",
        "err_deps": "必要なライブラリが不足:\n{e}\n\npip install mss pillow winocr mouse keyboard pystray",
        "err_start": "起動に失敗しました。error.log を確認してください。",
        "tray_sell": "出品待ち", "sell_title": "出品待ち", "sell_refresh": "更新", "sell_recheck": "再確認",
        "sell_loading": "確認中…", "sell_ready_group": "売れる", "sell_ready": "売れる",
        "sell_locked_group": "出品不可",
        "sell_private_title": "インベントリが非公開です",
        "sell_private_msg": "Steam在庫を読むにはインベントリの公開が必要です。下のボタンから設定を開き「ゲームの詳細」を公開にしてください。",
        "sell_open_privacy": "公開設定を開く", "sell_empty": "Steam在庫にアイテムがありません",
        "sell_no_steam": "Steamが見つかりません。起動してログインしてください",
        "sell_error": "取得に失敗しました", "sell_notify_title": "出品できます",
        "sell_notify": "「{name}」が出品可能になりました",
    },
    "en": {
        "low": "Low", "med": "Median", "lst": "List", "sold": "Sold", "quote": "Updated", "sort_added": "Added", "hist_last": "Last",
        "mkt": "Click to open Steam Market", "noprice": "Not on market", "nolisting": "No listings",
        "nomatch": "No match", "reading": "🔍 Reading…", "read": "OCR", "lang_switch": "🌐 Game looks {lang} → switch",
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
        "startup": "Startup", "autostart_label": "Start with Windows", "autostart_hint": "Launches MarketLens automatically when you sign in.",
        "alwaystop_label": "Keep history & sell timer on top", "alwaystop_hint": "Turn off to let other windows cover them (the price popup stays on top).",
        "privacy": "Usage stats", "telemetry_label": "Send anonymous usage stats",
        "telemetry_hint": "To improve the app, anonymous launches, looked-up items and errors are sent. No IP, Steam inventory or personal data is sent. You can turn this off anytime.",
        "help_main": "Point at an item and press your hotkey — its Steam Market\nprice (lowest + median) pops up.",
        "help_key": "Default hotkey is the mouse side (back) button. Change it in Settings.",
        "help_tips": ["Close the popup by clicking away, moving off it, or pressing Esc",
                      "Wrong name or rarity? fix it from the history window (right-click)",
                      "History: open from tray. Right-click a row for Favourite / Rename / Rarity / Delete, plus 'Update all'",
                      "Change the hotkey & language in Settings",
                      "Safe: it never touches the game (screen OCR + hotkey only)"],
        "ocr_missing_title": "OCR language not installed",
        "ocr_missing_msg": "{lang} text recognition isn't installed on this PC, so item names can't be read.\nAdd {lang} in Windows Settings → Time & language → Language & region.",
        "ocr_open_settings": "Open language settings",
        "close": "Close",
        "tray_key": "Key: ", "tray_settings": "Settings", "tray_history": "History",
        "tray_limit": "History limit", "tray_quit": "Quit", "unlimited": "Unlimited", "items": "{n}",
        "mouse_x": "Mouse Side (Back)", "mouse_x2": "Mouse Side (Forward)", "mouse_middle": "Mouse Middle",
        "mouse_left": "Mouse Left", "mouse_right": "Mouse Right", "mouse_prefix": "Mouse ",
        "dbg_title": "Debug",
        "err_deps": "Missing libraries:\n{e}\n\npip install mss pillow winocr mouse keyboard pystray",
        "err_start": "Failed to start. Please check error.log.",
        "tray_sell": "Sell timer", "sell_title": "Sell timer", "sell_refresh": "Refresh", "sell_recheck": "Re-check",
        "sell_loading": "Checking…", "sell_ready_group": "Sellable", "sell_ready": "Ready",
        "sell_locked_group": "Not sellable",
        "sell_private_title": "Inventory is private",
        "sell_private_msg": "To read your Steam inventory, set it to public. Open settings below and make 'Game details' public.",
        "sell_open_privacy": "Open privacy settings", "sell_empty": "No items in your Steam inventory",
        "sell_no_steam": "Steam not found. Launch and sign in.",
        "sell_error": "Failed to fetch", "sell_notify_title": "Ready to sell",
        "sell_notify": "“{name}” can now be listed",
    },
    "zh": {
        "low": "最低", "med": "中位", "lst": "在售", "sold": "成交", "quote": "行情", "sort_added": "添加", "hist_last": "最近",
        "mkt": "点击打开 Steam 市场", "noprice": "无市场价格（不可交易）", "nolisting": "暂无在售",
        "nomatch": "无匹配", "reading": "🔍 识别中…", "read": "识别", "lang_switch": "🌐 游戏似乎是{lang} → 切换",
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
        "startup": "启动", "autostart_label": "随 Windows 启动", "autostart_hint": "登录时自动启动 MarketLens。",
        "alwaystop_label": "历史与可出售计时始终置顶", "alwaystop_hint": "关闭后可被其他窗口遮挡（价格弹窗仍始终置顶）。",
        "privacy": "使用统计", "telemetry_label": "发送匿名使用统计",
        "telemetry_hint": "为改进应用，会匿名发送启动、查询的物品和错误信息。不会发送IP、Steam库存或个人信息。可随时关闭。",
        "help_main": "将光标对准物品并按下触发键，即可显示该物品的\nSteam 市场价格（最低价·中位价）。",
        "help_key": "触发键默认是鼠标侧键（后退）。可在「设置」中修改。",
        "help_tips": ["点击外部 / 移开光标 / 按 Esc 关闭弹窗",
                      "名称或品质不对时，可在历史窗口右键修改",
                      "历史：从托盘「历史」打开。右键可收藏·重命名·改品质·删除，并有「全部更新」",
                      "在「设置」中修改触发键和显示语言",
                      "安全：不干预游戏（仅截屏OCR＋按键）"],
        "ocr_missing_title": "文字识别(OCR)语言未安装",
        "ocr_missing_msg": "此电脑未安装{lang}的文字识别，无法读取物品名称。\n在 Windows 设置 → 时间和语言 → 语言和区域 中添加{lang}即可使用。",
        "ocr_open_settings": "打开语言设置",
        "close": "关闭",
        "tray_key": "按键：", "tray_settings": "设置", "tray_history": "历史",
        "tray_limit": "历史上限", "tray_quit": "退出", "unlimited": "无限制", "items": "{n} 条",
        "mouse_x": "鼠标侧键(后退)", "mouse_x2": "鼠标侧键(前进)", "mouse_middle": "鼠标中键",
        "mouse_left": "鼠标左键", "mouse_right": "鼠标右键", "mouse_prefix": "鼠标 ",
        "dbg_title": "调试",
        "err_deps": "缺少必要的库:\n{e}\n\npip install mss pillow winocr mouse keyboard pystray",
        "err_start": "启动失败。请查看 error.log。",
        "tray_sell": "可出售计时", "sell_title": "可出售计时", "sell_refresh": "刷新", "sell_recheck": "重新检查",
        "sell_loading": "检查中…", "sell_ready_group": "可出售", "sell_ready": "可售",
        "sell_locked_group": "不可出售",
        "sell_private_title": "库存未公开",
        "sell_private_msg": "要读取 Steam 库存，请将其设为公开。点击下方打开设置，将「游戏详情」设为公开。",
        "sell_open_privacy": "打开隐私设置", "sell_empty": "Steam 库存中没有物品",
        "sell_no_steam": "未找到 Steam，请启动并登录。",
        "sell_error": "获取失败", "sell_notify_title": "可以出售",
        "sell_notify": "“{name}”现在可以上架了",
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
    try:
        _telemetry_send("error", err=msg)   # 匿名でリモートにも送る（デバッグ用）。未定義/失敗は黙殺
    except Exception:
        pass


# ---- 匿名の利用テレメトリ（オーナーが利用状況を把握＋エラーをリモート収集） -------------
# 送るもの: 匿名ランダムID(cid) / イベント(launch|lookup|error) / 版・言語 / 参照アイテム名・等級 / エラー本文。
# 送らないもの: IP（国はサーバ側でエッジ付与）・Steam在庫/所持品・個人情報。設定でオフにできる(_telemetry)。
_telemetry = [True]     # オン/オフ（設定で変更・既定オン）
_cid = [None]           # 匿名クライアントID（初回生成して設定に永続化。IP由来ではない）
_always_top = [True]    # 履歴/出品待ちウィンドウを常に前面に出すか（設定・既定オン）。レンズのポップは対象外

def _scrub(s):
    """エラー本文から環境依存の個人情報（Windowsユーザー名/ホームパス）を伏字化してから送る。"""
    s = str(s)
    try:
        home = os.path.expanduser("~")
        if home and home != "~":
            s = s.replace(home, "~")
        s = re.sub(r"(?i)([A-Z]:\\Users\\)[^\\/:*?\"<>|\r\n]+", r"\1<user>", s)
        user = os.environ.get("USERNAME") or os.environ.get("USER")
        if user and len(user) >= 3:
            s = s.replace(user, "<user>")
    except Exception:
        pass
    return s[:4000]

def _telemetry_send(ev, item=None, rarity=None, err=None):
    """匿名テレメトリを1件、別スレッドで送る。オフ/ID未設定なら何もしない。失敗は黙殺。"""
    if not _telemetry[0] or not _cid[0]:
        return
    body = {"cid": _cid[0], "ev": ev, "ver": APP_VERSION, "lang": _ui_lang}
    if item:   body["item"]   = str(item)[:80]
    if rarity: body["rarity"] = str(rarity)[:20]
    if err is not None: body["msg"] = _scrub(err)
    data = json.dumps(body).encode("utf-8")
    def work():
        try:
            req = urllib.request.Request(STATS_URL, data=data,
                                         headers={"content-type": "application/json", "User-Agent": "MarketLens"})
            urllib.request.urlopen(req, timeout=8).read()
        except Exception:
            pass
    threading.Thread(target=work, daemon=True).start()


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
    from tbh_price_match import Matcher, RARITIES, extract_rarity, norm as _norm
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
def _hi_variant(tpl):
    """セレスティアル用テンプレ：枠の内側(通常は暗)を実機実測のシアンに塗替えたもの。
    明色バーはエッジ版でも小UI倍率で相関が閾値に届かず枠ごと検出落ちする（実機cap0で確定）。"""
    v = cv2.cvtColor(tpl, cv2.COLOR_BGR2GRAY)
    m = v < 70                                              # 暗部のうち枠内側だけ（縁・角ドットは残す）
    m[:13, :] = False; m[52:, :] = False; m[:, :44] = False
    out = tpl.copy(); out[m] = (230, 175, 15)               # BGR=セレスティアルバーの実測色
    return out
try:
    _TPL = cv2.imread(os.path.join(RES, "frame_tpl.png"))   # 名前枠の左角テンプレート（定数ピクセル）
    _TPL_E = _edges(_TPL) if _TPL is not None else None     # 高レア(背景色が変わる)用のエッジ版
    _TPL_H = _hi_variant(_TPL) if _TPL is not None else None
except Exception:
    _TPL = _TPL_E = _TPL_H = None
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
    return f"CN¥{val:,.2f}"                          # 人民元は小数2桁。円(¥)と区別するため CN¥

def disp_name(e):                  # 現在の言語でアイテム名（zh→簡体、無ければen→ja）
    if not e: return ""
    return (e.get("zh") if _ui_lang == "zh" else e.get("en") if _ui_lang == "en" else e.get("ja")) \
           or e.get("en") or e.get("ja") or e.get("zh") or ""

def disp_type(e):                  # 種別（zh→中国語訳、無ければen、最後にtype）
    if _ui_lang == "ja": return e.get("type_ja") or e.get("type") or ""
    if _ui_lang == "zh": return e.get("type_zh") or e.get("type_en") or e.get("type") or ""
    return e.get("type_en") or e.get("type") or ""

def split_type_level(e):           # 種別を「部位」と「必要Lv」に分離（type系は「弓 Lv.80」「Bow - Lv. 80」形式）
    t = disp_type(e)
    if not t: return "", ""
    m = re.search(r"Lv\.?\s*(\d+)", t, re.I)
    lv = ("Lv" + m.group(1)) if m else ""
    part = re.sub(r"\s*-?\s*Lv\.?\s*\d+\s*$", "", t, flags=re.I).strip()
    return part, lv

def disp_rarity(e):                # 等級表示（zh→中国語訳、無ければen）
    if _ui_lang == "ja": return e.get("rarity_ja") or ""
    if _ui_lang == "zh": return e.get("rarity_zh") or e.get("rarity_en") or ""
    return e.get("rarity_en") or ""


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
_render_cache = {}                  # hash_name -> (取得time, sell_cents_USD or None, listings)。Noneは出品なし
_render_blocked = [0.0]             # render が万一429になった時のバックオフ（priceoverviewとは独立）
_RENDER_EMPTY = "empty"             # クエリ成功・該当変種が市場に無い＝「現在出品なし」(取得失敗のNoneとは区別)

def _render_price(hash_name):
    """search/render を品名+レア度クエリで叩く。戻り: (usd_cents, listings)＝出品あり / _RENDER_EMPTY＝出品なし /
    None＝取得失敗(429・通信エラー。呼び出し側はバンドル価格を保持)。"""
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
        return _RENDER_EMPTY                       # クエリは成功・該当変種が無い＝現在出品なし
    except urllib.error.HTTPError as e:
        if e.code == 429:                         # 万一の429→render専用バックオフ（UIが待機表示）
            _render_blocked[0] = time.time() + _RL_BACKOFF
        return None                               # 取得失敗＝出品なしと断定せずバンドル保持
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

def _usd_no_listing(hash_name, force, cache_only):
    """USD表示で現在出品なしの時、priceoverview(USD)の中央値を補う（renderは中央値を返さないため、
    ja/zhと同じく「出品なし＋中央値」を出せるように）。中央値が取れなければ _RENDER_EMPTY。
    cache_only時はネットを叩かず _price_cache の中央値だけ使う。"""
    if cache_only:
        c = _price_cache.get((hash_name, 1))              # (取得time, low, med, vol)
        if c and c[2] is not None:
            return None, c[2], c[3], 1
        return _RENDER_EMPTY
    nat = _native_price(hash_name, 1, force)              # USD priceoverview（429時はNone/キャッシュ＝穏当に劣化）
    if nat and nat[1] is not None:
        return nat[0], nat[1], nat[2], 1                  # lowは大抵None＋medあり → 呼び出し側で「出品なし＋中央値」
    return _RENDER_EMPTY

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
    rc = _render_cache.get(hash_name)                     # render USD（5分キャッシュ）。sell=None は出品なし
    if rc and not force and now - rc[0] < 300:
        return _usd_no_listing(hash_name, force, cache_only) if rc[1] is None else (rc[1], rc[1], rc[2], 1)
    if cache_only:
        if not rc: return None
        return _usd_no_listing(hash_name, force, True) if rc[1] is None else (rc[1], rc[1], rc[2], 1)
    rp = _render_price(hash_name)                         # 1リクエスト（BANされない）
    if rp == _RENDER_EMPTY:                                # クエリ成功・出品なし（キャッシュして再取得を抑える）
        _render_cache[hash_name] = (now, None, 0)
        return _usd_no_listing(hash_name, force, cache_only)   # USDでも中央値はpriceoverviewで補う
    if rp is not None:
        _render_cache[hash_name] = (now, rp[0], rp[1])
        return rp[0], rp[0], rp[1], 1
    return None                                           # 取得失敗→呼び出し側がバンドル価格を保持

def apply_live(ent, native_ok=False, force=False, cache_only=False):
    """entの価格を最新化（in-place）。cur/_live(=表示通貨で確定か)も設定。取れなければ既存(バンドル)保持。"""
    if not ent or not ent.get("hash"): return
    lp = live_price(ent["hash"], native_ok=native_ok, force=force, cache_only=cache_only)
    if CALIBRATE:                                 # 実機MarketLensが品ごとに何を計算したかを記録（原因調査用）
        try:
            with open(os.path.join(HERE, "price-debug.log"), "a", encoding="utf-8") as _fd:
                _fd.write(f"{time.strftime('%H:%M:%S')} hash={ent.get('hash')!r} disp_cur={_cur_code()} "
                          f"lp={lp!r} render_blocked={time.time() < _render_blocked[0]} "
                          f"cache={_render_cache.get(ent.get('hash'))} bundle_sell={ent.get('sell')}\n")
        except Exception: pass
    if lp is None: return                         # 取得失敗→既存(バンドル)価格を保持（従来通り）
    if lp == _RENDER_EMPTY:                        # render検索で該当なし＝出品なし
        ent["_nolist"] = True
        return
    low, med, vol, src = lp
    if low is None:
        # lowest_price(現在の最安＝買える価格)が無い＝現在出品0件＝出品なし。
        # ただし median(過去の中央値)があれば表示する。sellは必ずNoneにしてから cur/median を src通貨で入れる
        # （古いUSDバンドル値を残したまま cur=¥ にすると¥31/¥1等の誤値になる＝過去の不具合。要厳守）。
        ent["_nolist"] = True
        ent["sell"] = None
        if med is not None:
            ent["median"] = med
            if vol is not None: ent["volume"] = vol
            ent["cur"] = src
            ent["_live"] = (src == _cur_code())
        return
    ent["_nolist"] = False
    ent["sell"] = low
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

def _ocr(c, lang=None):
    out = []
    langs = _OCR_LANGS.get(lang or _ui_lang, ("ja", "en"))   # 表示言語に合わせて読む文字種を選ぶ（速度維持）
    # 通常(暗背景+明文字)＋反転(明背景+暗文字=高レアのシアンバー)の両方を読み、行ごとに照合させる
    for proc in (_adapt(c), _adapt(c, invert=True)):
        for lang in langs:
            try:
                r = winocr.recognize_pil_sync(proc, lang)
                out.append(" ".join(l.get("text", "") for l in (r.get("lines") if isinstance(r, dict) else []) or []))
            except Exception:
                pass
    return "\n".join(out)   # 各読みは改行区切り＝行ごとに照合（二重化での薄まりを防ぐ）

_OCR_PRIMARY = {"ja": "ja", "en": "en", "zh": "zh-hans"}   # 表示言語→必須のOCRタグ（先頭一致）
_ocr_avail = [None]
def _ocr_available_tags():
    """このPCで使えるWindows OCRの言語タグ（小文字）。取得は1回だけ。"""
    if _ocr_avail[0] is None:
        try:
            from winrt.windows.media.ocr import OcrEngine
            _ocr_avail[0] = [l.language_tag.lower() for l in OcrEngine.available_recognizer_languages]
        except Exception:
            _ocr_avail[0] = []
    return _ocr_avail[0]

def _ocr_lang_missing():
    """現在の表示言語の文字認識がこのPCに無ければTrue。取得失敗時は誤検出回避でFalse。"""
    tags = _ocr_available_tags()
    if not tags: return False
    need = _OCR_PRIMARY.get(_ui_lang, "ja")
    return not any(t.startswith(need) for t in tags)   # 例: "zh-hans-cn".startswith("zh-hans")


# テンプレ frame_tpl.png は TBH ウィンドウ倍率「2x」で撮影した固定ピクセル＝倍率1.0の基準。
# ゲームのUI倍率(1x/1.25x/1.5x/2x/3x。解像度で同じ表記でも実ピクセルが変わる)を毎回テンプレ側を
# リサイズしながら相関が最大の倍率を探して自動追従する。当たった倍率は全クロップ座標に乗せる。
_SCALE_CACHE = [1.0]      # 直近に当選した実倍率（探索の初手＝同じ倍率なら相関1回で確定）
_DBG_LAST    = [(1.0, 0.0)]   # CALIBRATE用：直近検出の (倍率f, テンプレ相関ピーク)
_SCALE_GRID  = [0.45, 0.5, 0.55, 0.625, 0.7, 0.75, 0.85, 1.0, 1.15, 1.3, 1.5, 1.65]
_SCALE_STRONG = 0.6       # キャッシュ倍率でこの相関(通常/エッジ版)が出れば即確定（倍率不変＝再探索しない高速パス）
_SCALE_STRONG_HI = 0.72   # セレ版だけの相関で即確定する閾値。セレ版は内側単色塗りで誤倍率でも0.6超が出る
                          # （実機cap0: 誤倍率0.55で0.651、正倍率1.0で0.736）ため通常より高く要求する
_SEARCH_MAXW = 1100       # スケール探索はこの幅以下へ縮小した画像で行う（3x等の大画像でも相関を軽く）

def _match_at(arr, arr_e, tf):
    """テンプレを倍率tf(画像上の枠の見かけ倍率)にリサイズして相関マップを返す。入らない倍率はNone。
    通常版＋エッジ版＋セレスティアル版(内側シアン)の3本をmax合成（明色バーは通常/エッジとも落ちるため）。"""
    th, tw = _TPL.shape[:2]
    if abs(tf - 1.0) < 1e-6:
        tpl, tpl_e, tpl_h = _TPL, _TPL_E, _TPL_H
    else:
        nw, nh = max(8, int(round(tw * tf))), max(8, int(round(th * tf)))
        interp = cv2.INTER_AREA if tf < 1.0 else cv2.INTER_CUBIC
        tpl = cv2.resize(_TPL, (nw, nh), interpolation=interp)
        tpl_e = _edges(tpl) if _TPL_E is not None else None
        tpl_h = cv2.resize(_TPL_H, (nw, nh), interpolation=interp) if _TPL_H is not None else None
    if tpl.shape[0] >= arr.shape[0] or tpl.shape[1] >= arr.shape[1]:
        return None                                   # テンプレが画像より大きい＝この倍率は不可
    res = cv2.matchTemplate(arr, tpl, cv2.TM_CCOEFF_NORMED)
    if tpl_e is not None:
        res = np.maximum(res, cv2.matchTemplate(arr_e, tpl_e, cv2.TM_CCOEFF_NORMED))
    conf = float(res.max())                           # 倍率確定用＝通常/エッジ版のみのピーク（セレ版は倍率判別力が無い）
    if tpl_h is not None:
        res = np.maximum(res, cv2.matchTemplate(arr, tpl_h, cv2.TM_CCOEFF_NORMED))
    return res, conf

def _best_template_factor(arr, arr_e, grid_tf, cached_tf):
    """画像上の枠倍率(テンプレ倍率)を探す。まずキャッシュ倍率だけ試し、強ければ即確定(=相関1回)。
    弱い時だけ全gridを走査。戻り: (tf, peak, 相関マップ)。
    即確定は通常/エッジ版conf>=_SCALE_STRONG、またはセレ版込み>=_SCALE_STRONG_HI（誤倍率ロック防止）。"""
    m = _match_at(arr, arr_e, cached_tf)
    if m is not None and (m[1] >= _SCALE_STRONG or float(m[0].max()) >= _SCALE_STRONG_HI):
        return cached_tf, float(m[0].max()), m[0]     # 倍率変わってない＝再探索不要（高速パス）
    best_t = cached_tf
    best_s, best_res = (float(m[0].max()), m[0]) if m is not None else (-1.0, None)
    for t in grid_tf:                                 # 倍率が変わった時だけ全候補を走査
        if abs(t - cached_tf) < 1e-6:
            continue
        r = _match_at(arr, arr_e, t)
        if r is None:
            continue
        s = float(r[0].max())
        if s > best_s:
            best_s, best_t, best_res = s, t, r[0]
    return best_t, best_s, best_res

_BAR_GAP = 48.0          # f=1.0でのバー上下ベゼル中心の間隔（実機実測）＝倍率はここから直接出る
_BAR_OFF = (39.0, 11.0)  # ベゼル線左端→テンプレ角(bx,by)のオフセット（f=1.0px。検証相関の±12pxで微補正）
_K1x25 = np.ones((1, 25), np.uint8); _K7x1 = np.ones((7, 1), np.uint8); _K3x3 = np.ones((3, 3), np.uint8)

def _detect_bars(full):
    """名前バーの構造検出（一次。多倍率テンプレ走査の約100倍速）。
    等級で変わるのはバー内側の色だけで、細いタン色ベゼル2本(間隔48f)は全等級・全UI倍率で不変。
    戻り: [(bx, by, f)] テンプレ角座標と倍率の候補（テンプレ1回照合での検証が必要）。"""
    b = full[..., 0].astype(np.int16); g = full[..., 1].astype(np.int16); r = full[..., 2].astype(np.int16)
    # ベゼル色 BGR(127,157,182)±45 ＋ 暖色勾配(r>g>b)。±45は縮小描画/AAの色ズレ許容（合成0.5x〜1.5xで実証）
    m = ((np.abs(b - 127) < 45) & (np.abs(g - 157) < 45) & (np.abs(r - 182) < 45)
         & (r > g) & (g > b)).astype(np.uint8)
    horiz = cv2.erode(m, _K1x25)             # 水平に25px以上続く画素だけ＝線
    thick = cv2.erode(m, _K7x1)              # 縦7px以上の塊（パネル飾り等）はベゼルでない
    thin = horiz & ~cv2.dilate(thick, _K3x3)
    n, _l, stats, _c = cv2.connectedComponentsWithStats(cv2.dilate(thin, _K1x25))
    lines = sorted(((int(s[0]), int(s[1] + s[3] // 2), int(s[2])) for s in stats[1:n]
                    if s[2] >= 80 and s[3] <= 8), key=lambda l: l[1])
    out = []
    for i, (x1, y1, w1) in enumerate(lines):
        for x2, y2, w2 in lines[i + 1:]:
            dy = y2 - y1
            if dy > 110: break                            # y昇順＝以降は離れる一方
            if dy < 20: continue
            ov = min(x1 + w1, x2 + w2) - max(x1, x2)
            if ov < 0.7 * max(w1, w2): continue           # 上下ベゼルは水平に重なる
            if max(w1, w2) < 4.5 * dy: continue           # 名前バーは横長（実バー比8.7／誤候補3.4を排除）
            f = dy / _BAR_GAP
            out.append((x1 - _BAR_OFF[0] * f, y1 - _BAR_OFF[1] * f, f))
    return out

def detect_frames(img):
    """名前枠の位置だけ検出（OCRしない）。戻り: ([(x, y, 一致度) 元解像度], 検出倍率f)。
    一次＝構造検出(_detect_bars)→確定倍率でテンプレ1回照合の検証（数十ms・等級色/倍率に非依存）。
    構造検出が0件の時だけ従来の多倍率テンプレ走査にフォールバック（ベゼル遮蔽等の保険）。"""
    if _TPL is None:
        return [], 1.0
    full = cv2.cvtColor(np.array(img), cv2.COLOR_RGB2BGR)
    H, W = full.shape[:2]
    th, tw = _TPL.shape[:2]
    picked, fs = [], []
    for cx, cy, f in _detect_bars(full):
        if not (0.2 <= f <= 2.0):
            continue
        x0, y0 = max(0, int(cx - 12)), max(0, int(cy - 12))
        x1, y1 = min(W, int(cx + tw * f + 12)), min(H, int(cy + th * f + 12))
        roi = full[y0:y1, x0:x1]
        if roi.shape[0] < 16 or roi.shape[1] < 16:
            continue
        mm = _match_at(roi, _edges(roi) if _TPL_E is not None else None, f)
        if mm is None:
            continue
        s = float(mm[0].max())
        if s < 0.55:
            continue                                      # 構造は合うが角がテンプレ不一致＝誤候補
        my, mx = np.unravel_index(int(mm[0].argmax()), mm[0].shape)
        bx, by = x0 + int(mx), y0 + int(my)
        if all(abs(bx - px) > 420 * f or abs(by - py) > 36 * f for px, py, _s in picked):
            picked.append((bx, by, s)); fs.append(f)
    if picked:
        f = sorted(fs)[len(fs) // 2]
        _SCALE_CACHE[0] = f
        _DBG_LAST[0] = (f, max(s for _x, _y, s in picked))
        picked.sort(key=lambda p: -p[2])
        return picked[:10], f
    # ---- フォールバック：従来の多倍率テンプレ走査（縮小画像で探索し元解像度へ戻す） ----
    ds = min(1.0, _SEARCH_MAXW / float(W))            # 探索用の縮小率(<=1)。大画像ほど効く
    if ds < 1.0:
        small = cv2.resize(full, (max(1, int(W * ds)), max(1, int(H * ds))), interpolation=cv2.INTER_AREA)
    else:
        ds, small = 1.0, full
    small_e = _edges(small) if _TPL_E is not None else None
    grid_tf = [g * ds for g in _SCALE_GRID]           # 実倍率fの枠は縮小空間でテンプレ倍率 f*ds で一致
    tf, peak, res = _best_template_factor(small, small_e, grid_tf, _SCALE_CACHE[0] * ds)
    f = tf / ds                                       # 元解像度での実倍率
    _SCALE_CACHE[0] = f
    _DBG_LAST[0] = (f, peak)
    if res is None:
        return [], f
    dx, dy = round(420 * tf), round(36 * tf)          # 重複ピーク間引き（縮小空間px＝枠サイズ比例）
    ys, xs = np.where(res >= 0.55)                    # 閾値低め＝取りこぼさない。誤検出はマッチャ確信0.85で除外
    pk = sorted(zip(xs.tolist(), ys.tolist(), res[ys, xs].tolist()), key=lambda p: -p[2])
    picked = []
    for x, y, s in pk:                          # 同じ枠の重複ピークをまとめる（高スコア順なので最良が残る）
        # ※比較は縮小空間で統一（元解像度に混ぜると長名で重複が消えず誤マッチ＝過去の不具合）
        if all(abs(x - px) > dx or abs(y - py) > dy for px, py, _ in picked):
            picked.append((x, y, s))                  # 縮小空間のまま保持
        if len(picked) >= 10:
            break
    picked = [(int(round(x / ds)), int(round(y / ds)), s) for x, y, s in picked]   # 最後に元解像度へ
    return picked, f

def _ocr_frame(img, x, y, f, lang=None):
    """1枠ぶんをOCR→(名前, 等級)。OCR/_adapt は2xベース文字サイズ前提なので 1/f 倍で正規化。
    lang指定で別言語のOCRも可（該当なし時の言語検出に使う）。"""
    Sf = lambda v: int(round(v * f))
    def norm(crop):
        if abs(f - 1.0) > 0.02 and crop.width > 0 and crop.height > 0:
            crop = crop.resize((max(1, int(round(crop.width / f))),
                                max(1, int(round(crop.height / f)))), Image.LANCZOS)
        return _ocr(crop, lang)
    name = norm(img.crop((max(0, x - Sf(90)), y + Sf(6), x + Sf(560), y + Sf(56))))    # 枠内＝名前（左に広め＝短名対策）
    rank = norm(img.crop((max(0, x - Sf(90)), y + Sf(56), x + Sf(560), y + Sf(122))))  # 枠直下＝等級
    return name, rank

_RMAP_JA = dict(RARITIES)              # en -> ja
_RARITY_HUES = [None]
def _rarity_hues():
    """各レア度の色相(度)。RARITY_COLORSから1回だけ算出。"""
    if _RARITY_HUES[0] is None:
        import colorsys
        d = {}
        for k, hx in RARITY_COLORS.items():
            r, g, b = (int(hx[i:i+2], 16) / 255 for i in (1, 3, 5))
            d[k] = colorsys.rgb_to_hsv(r, g, b)[0] * 360
        _RARITY_HUES[0] = d
    return _RARITY_HUES[0]

def _frame_rarity(img, x, y, f):
    """枠の『○○等級』テキスト色から等級(en)を推定（OCRより頑健）。
    色が乏しい(素材等)・不確実な時はNone。等級OCRが読めない時の救済に使う。"""
    Sf = lambda v: int(round(v * f))
    try:
        arr = np.asarray(img.crop((max(0, x - Sf(60)), y + Sf(58), x + Sf(260), y + Sf(92))))
        if arr.size == 0: return None
        hsv = cv2.cvtColor(arr, cv2.COLOR_RGB2HSV)        # H:0-179 S/V:0-255
        mask = (hsv[..., 1] > 70) & (hsv[..., 2] > 110)   # 彩度・明度高め＝色付きの等級テキスト
        hue = hsv[..., 0][mask].astype(np.int32) * 2      # cv2の0-179 → 0-358度
        if hue.size < 25: return None                     # 色付き画素が少ない＝素材等→色判定しない
        deg = int(np.bincount(hue, minlength=360).argmax())   # 最頻色相
        rh = _rarity_hues()
        best = min(rh, key=lambda k: min(abs(deg - rh[k]), 360 - abs(deg - rh[k])))
        dist = min(abs(deg - rh[best]), 360 - abs(deg - rh[best]))
        return best if dist <= 22 else None
    except Exception:
        return None


def _annotate(img, boxes, cands, chosen, xy, off, scale=1.0):
    """デバッグ用: 撮影画像に 検出枠・読取・マッチ結果・カーソル を描いて縮小して返す。"""
    from PIL import ImageDraw, ImageFont
    ox, oy = off
    S = lambda v: int(round(v * scale))               # 検出倍率に合わせて枠位置を描く
    im = img.convert("RGB").copy()
    d = ImageDraw.Draw(im)
    try:
        fnt = ImageFont.truetype("YuGothM.ttc", 22); fbig = ImageFont.truetype("YuGothB.ttc", 30)
    except Exception:
        fnt = ImageFont.load_default(); fbig = fnt
    for name, rank, bx, by, sc_t in boxes:
        d.rectangle([bx - S(90), by + S(6), bx + S(560), by + S(56)], outline=(0, 255, 255), width=3)
        d.rectangle([bx - S(90), by + S(56), bx + S(560), by + S(122)], outline=(0, 160, 255), width=2)
        d.text((bx - S(88), by - 26), f"枠 t={sc_t:.2f} x{scale:.2f} 名[{name}] 級[{rank}]", fill=(255, 255, 0), font=fnt)
    for c in cands:
        sc, d2, sx, sy, r, bx, by, name, rank = c
        e = r[0]
        col = (0, 255, 0) if sc >= 0.85 else (255, 120, 120)
        d.text((bx - S(88), by + S(124)), f"= {e.get('ja','')}({e.get('rarity_ja','')}) s={sc} d={int(d2**0.5)}", fill=col, font=fnt)
    if chosen:
        bx, by = chosen[5], chosen[6]
        d.rectangle([bx - S(94), by + S(2), bx + S(564), by + S(126)], outline=(0, 255, 0), width=6)
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
    _keep_on_top(win)          # ゲーム（ウィンドウ/ボーダーレス）の前へ出し続ける＝背面に回って見えない問題の対策
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
            frames, scale = detect_frames(img)   # 枠の位置だけ検出（OCRはまだ）。scale=検出UI倍率
            oS = lambda v: int(round(v * scale))
            # カーソルに近い枠から処理＝指している枠を最優先。普通は最近1枠で確定しOCRを激減させる。
            frames.sort(key=lambda fr: (ox + fr[0] + oS(250) - xy[0]) ** 2 + (oy + fr[1] + oS(30) - xy[1]) ** 2)
            _dbg = CALIBRATE or DEBUG_UI                 # デバッグ時は全枠OCRして注釈に出す
            boxes = []                                   # OCRした枠（近い順。ヒント/デバッグ用）
            cands = []
            for bx, by, sc_t in frames:
                name, rank = _ocr_frame(img, bx, by, scale)
                if not extract_rarity(rank):              # 等級OCRが読めない時は枠の色で補う（最高値変種への誤フォールバック防止）
                    crar = _frame_rarity(img, bx, by, scale)
                    if crar: rank = _RMAP_JA[crar]        # 色から等級を確定し、それを等級テキストとして渡す
                boxes.append((name, rank, bx, by, sc_t))
                best_r = matcher.match_item(name, rank)   # 名前で特定＋等級（OCRまたは色）から正しい変種を引く
                if best_r:
                    sx, sy = ox + bx + oS(250), oy + by + oS(30)
                    d2 = (sx - xy[0]) ** 2 + (sy - xy[1]) ** 2
                    cands.append((best_r[0]["score"], d2, sx, sy, best_r, bx, by, name, rank))
                    if best_r[0]["score"] >= 0.85 and not _dbg:
                        break                            # 最近枠で確信マッチ＝以後はOCRしない（高速）
                if not _dbg and len(boxes) >= 3:
                    break                                # 近い3枠で無確信なら打ち切り（暴走防止）
            # 表示言語：設定（PC言語/手動）に従う
            global _ui_lang
            if _lang_mode[0] in LANGS:
                _ui_lang = _lang_mode[0]
            found, chosen = [], None
            _suggest_lang[0] = None
            if cands:
                ax, ay = min(cands, key=lambda c: c[1])[2:4]   # カーソル最近の枠＝指してる位置
                rad = (80 * scale) ** 2
                same = [c for c in cands if (c[2] - ax) ** 2 + (c[3] - ay) ** 2 < rad]
                best = max(same, key=lambda c: c[0])
                if best[0] >= 0.85:
                    found, chosen = best[4], best
            if not found and frames and not _dbg:
                # 該当なし＝表示言語とゲームの言語がズレ、OCRが別言語で読んでいる可能性。
                # 他の(OCRパックが入っている)言語で最近枠を読み直し、当たればそれがゲームの言語＝切替を提案。
                avail = _ocr_available_tags()
                bx, by, sc_t = frames[0]
                for L in LANGS:
                    if L == _ui_lang: continue
                    need = _OCR_PRIMARY.get(L, "")
                    if avail and not any(t.startswith(need) for t in avail):
                        continue                              # その言語のOCRが無い→切替ても読めないので提案しない
                    n2, r2 = _ocr_frame(img, bx, by, scale, lang=L)
                    if not extract_rarity(r2):
                        cr = _frame_rarity(img, bx, by, scale)
                        if cr: r2 = _RMAP_JA[cr]
                    m2 = matcher.match_item(n2, r2)
                    if m2 and m2[0]["score"] >= 0.85:        # 別言語で当たった＝ゲームはその言語
                        sx, sy = ox + bx + oS(250), oy + by + oS(30)
                        found = m2
                        chosen = (m2[0]["score"], (sx - xy[0]) ** 2 + (sy - xy[1]) ** 2, sx, sy, m2, bx, by, n2, r2)
                        _suggest_lang[0] = L                  # ポップで「ゲームは{L}のよう→切替」を出す
                        break
            if found and chosen and not found[0].get("rarity_en"):
                # CRAFTING素材等＝データ上レア度無し。ツールチップの実レア度(等級OCR/色)を表示用に補う
                # ＝価格は素材共通のまま、色とラベルだけ実レア度（例ビヨンド）で出す（found[0]は_collectのdictコピーなので安全）。
                det = extract_rarity(chosen[8]) or _frame_rarity(img, chosen[5], chosen[6], scale)
                if det:
                    found[0]["rarity_en"] = det
                    found[0]["rarity_ja"] = _RMAP_JA.get(det, det)
            if found:                             # 表示の瞬間に最新USD（叩ければ現地通貨）へ更新
                apply_live(found[0], native_ok=True)
                _telemetry_send("lookup", item=found[0].get("en"), rarity=found[0].get("rarity_en"))
                # 履歴記録(_histの構造変更)はメインスレッド(poll)で行う＝反復中の競合を避ける
            if CALIBRATE:                         # 失敗時の画像とログを残す（私が原因を見る用）
                try:
                    _f, _peak = _DBG_LAST[0]
                    with open(os.path.join(HERE, "ocr-text.txt"), "w", encoding="utf-8") as f:
                        f.write(f"lang={_ui_lang} cursor={xy} off=({ox},{oy}) imgWH=({img.width}x{img.height}) "
                                f"scale={_f:.3f} peak={_peak:.3f} 枠数={len(boxes)} 結果={'OK' if found else 'なし'}\n")
                        for n, r, bx, by, st in boxes:
                            f.write(f" 枠@({bx},{by}) t={st:.2f} 名[{(n or '')[:26]}] 級[{(r or '')[:16]}]\n")
                        for c in sorted(cands, key=lambda c: c[1]):
                            f.write(f" 候補 {c[4][0].get('en')} / {c[4][0].get('ja')} s={c[0]} d={int(c[1]**.5)}\n")
                    try:                              # 注釈画像（枠・倍率・読取・候補・カーソル）を常に保存＝私が見る用
                        _annotate(img, boxes, cands, chosen, xy, (ox, oy), scale).save(os.path.join(HERE, "annot.png"))
                    except Exception:
                        pass
                    if not found:
                        img.save(os.path.join(HERE, "fail.png"))
                        import shutil; shutil.copy(os.path.join(HERE, "ocr-text.txt"), os.path.join(HERE, "fail.txt"))
                except Exception:
                    pass
            if DEBUG_UI:
                try:
                    PQ.put(("__debug__", _annotate(img, boxes, cands, chosen, xy, (ox, oy), scale), None))
                except Exception:
                    log_fatal("annotate:\n" + traceback.format_exc())
            hint = ""                              # カーソル最近枠の読取生テキスト（候補選び直し用）
            if boxes:
                hcx, hcy = int(round(250 * scale)), int(round(30 * scale))
                bb = min(boxes, key=lambda b: (ox + b[2] + hcx - xy[0]) ** 2 + (oy + b[3] + hcy - xy[1]) ** 2)
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
_hist_sort = ["date"]      # 並び順: "date"=追加日 / "sell"=最安 / "median"=中央値
_hist_sort_desc = [True]   # True=降順（新しい順/高い順）。同じソートを再タップで反転
_hist_sort_pills = []      # ソートpill [(mode, pill, label_fn)]
_hist_sort_face = [None]   # pillの見た目更新関数（_refresh時に同期）
_hist_last_update = [None] # 最終「全部更新」時刻の表示文字列（更新ボタンに常表示。設定で永続）
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
_ocrwarn_win = [None]                                # OCR言語未インストールの案内ウィンドウ
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
                       "hist_geo": _hist_geo[0], "sell_geo": _sell_geo[0],
                       "hist_open": _hist_visible[0], "sell_open": _sell_visible[0],
                       "hist_sort": _hist_sort[0], "hist_sort_desc": _hist_sort_desc[0],
                       "hist_last_update": _hist_last_update[0], "always_top": _always_top[0],
                       "cid": _cid[0], "telemetry": _telemetry[0]},
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
        if isinstance(d.get("sell_geo"), str): _sell_geo[0] = d["sell_geo"]
        _hist_visible[0] = bool(d.get("hist_open"))    # 前回の開閉状態（再起動で復元）
        _sell_visible[0] = bool(d.get("sell_open"))
        if d.get("hist_sort") in ("date", "sell", "median"): _hist_sort[0] = d["hist_sort"]
        if "hist_sort_desc" in d: _hist_sort_desc[0] = bool(d["hist_sort_desc"])
        if isinstance(d.get("hist_last_update"), str): _hist_last_update[0] = d["hist_last_update"]
        if "always_top" in d: _always_top[0] = bool(d["always_top"])
        if isinstance(d.get("cid"), str) and d["cid"]: _cid[0] = d["cid"]
        if "telemetry" in d: _telemetry[0] = bool(d["telemetry"])
    except Exception: pass
    if not _cid[0]:                       # 初回（または欠落）は匿名IDを生成して永続化
        _cid[0] = uuid.uuid4().hex
        _save_settings()

# ---- Windows起動時の自動起動（HKCU\...\Run。管理者不要。レジストリ自体が真実＝設定jsonに持たない） ----
_RUN_KEY  = r"Software\Microsoft\Windows\CurrentVersion\Run"
_RUN_NAME = "TBH MarketLens"

def _autostart_command():
    """ログオン時に起動するコマンド。exe化時はexe単体、.py時は pythonw + スクリプト。"""
    if getattr(sys, "frozen", False):
        return f'"{sys.executable}"'
    py = sys.executable
    pyw = os.path.join(os.path.dirname(py), "pythonw.exe")   # コンソールを出さない方を優先
    exe = pyw if os.path.exists(pyw) else py
    return f'"{exe}" "{os.path.abspath(__file__)}"'

def _autostart_get():
    """現在この実行ファイルで自動起動が有効か（Runキーの有無で判定）。"""
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _RUN_KEY) as k:
            winreg.QueryValueEx(k, _RUN_NAME)
            return True
    except FileNotFoundError:
        return False
    except Exception:
        return False

def _autostart_set(on):
    """自動起動を有効/無効に。有効化時は現在のexe/スクリプトパスで書き直す（移動・更新に追従）。"""
    try:
        import winreg
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, _RUN_KEY) as k:
            if on:
                winreg.SetValueEx(k, _RUN_NAME, 0, winreg.REG_SZ, _autostart_command())
            else:
                try: winreg.DeleteValue(k, _RUN_NAME)
                except FileNotFoundError: pass
        return True
    except Exception:
        log_fatal("autostart_set:\n" + traceback.format_exc())
        return False

def _autostart_refresh():
    """既に有効なら、現在のパスでコマンドを貼り直す（フォルダ移動やバージョン更新でパスが変わった時の保険）。"""
    if _autostart_get():
        _autostart_set(True)

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
        # _live/_nolist はこの起動の取得状態＝保存しない（次回起動で再取得して判定し直す）
        saved = [{k: v for k, v in r.items() if k not in ("_live", "_nolist")} for r in _hist]
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

def _stamp_str():                  # 追加日時/最終更新時刻（年なし。例 6/9 21:30）。Win strftimeの-m非対応を避け手組み
    t = time.localtime()
    return f"{t.tm_mon}/{t.tm_mday} {t.tm_hour:02d}:{t.tm_min:02d}"

def _record_history(ent):
    if not ent: return
    rec = {k: ent.get(k) for k in ("ja", "en", "zh", "zh_hant", "icon", "rarity_en", "rarity_ja",
                                   "sell", "median", "volume", "cur", "hash", "type_ja", "type_en", "type", "_live", "_nolist")}
    rec["ts"] = _stamp_str()
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

def _modern_titlebar(win, title_text, on_close):
    """OS標準タイトルバーを外し、ダーク・Win11角丸・ドラッグ移動・✕ のカスタム上部にする。
    戻り: row(Frame) … 呼び出し側がタイトルと✕の間に操作ボタンを side='right' で足せる。"""
    win.overrideredirect(True)
    _round_corners(win)
    tk.Frame(win, bg=C_CARD, height=8).pack(side="top", fill="x")          # 上の余白（角丸が映える）
    row = tk.Frame(win, bg=C_CARD); row.pack(side="top", fill="x", padx=(12, 8))
    title = tk.Label(row, text=title_text, bg=C_CARD, fg=C_NAME, font=("Yu Gothic UI", 12, "bold"))
    title.pack(side="left")
    close = tk.Label(row, text="✕", bg=C_CARD, fg=C_META, font=("Yu Gothic UI", 12), cursor="hand2")
    close.pack(side="right", padx=(8, 2))
    close.bind("<Button-1>", lambda e: on_close())
    close.bind("<Enter>", lambda e: close.config(fg=C_NAME))
    close.bind("<Leave>", lambda e: close.config(fg=C_META))
    d = {"x": 0, "y": 0}                                                    # タイトル行の空き＋文字でドラッグ移動
    def press(e): d["x"], d["y"] = e.x_root, e.y_root
    def move(e):
        win.geometry(f"+{win.winfo_x() + (e.x_root - d['x'])}+{win.winfo_y() + (e.y_root - d['y'])}")
        d["x"], d["y"] = e.x_root, e.y_root
    for w in (row, title):
        w.bind("<Button-1>", press); w.bind("<B1-Motion>", move)
    return row

def _add_resize_grip(win, min_w=300, min_h=240):
    """overrideredirectで失われるリサイズを右下グリップで代替（ドラッグで幅高さ変更）。"""
    grip = tk.Label(win, text="⤡", bg=C_CARD, fg="#3a3f4b", cursor="size_nw_se", font=("Yu Gothic UI", 11))
    grip.place(relx=1.0, rely=1.0, anchor="se", x=-1, y=-1)
    s = {}
    def press(e):
        s["x"], s["y"], s["w"], s["h"] = e.x_root, e.y_root, win.winfo_width(), win.winfo_height()
    def drag(e):
        win.geometry(f"{max(min_w, s['w'] + (e.x_root - s['x']))}x{max(min_h, s['h'] + (e.y_root - s['y']))}")
    grip.bind("<Button-1>", press); grip.bind("<B1-Motion>", drag)
    return grip

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

def _keep_on_top(win, want_noact=lambda: True, pause=lambda: False, respect_toggle=False):
    """フルスクリーン(ボーダーレス)のゲームの前へ出し続ける。要点は WS_EX_NOACTIVATE:
    これを付けるとポップをクリックしてもアクティブ化が起きない＝ゲームが前面に出てこない。
    ただし編集中(want_noact()=False)は外してキーボード入力を受けられるようにする。
    TOPMOSTは常に維持し、120ms毎に再主張して背後への回り込みを防ぐ。
    pause()=True の間は再主張を休む（他のポップと最前面を奪い合ってチラつくのを防ぐ）。
    respect_toggle=True の窓は、設定『常に前面』がオフだと通常窓に戻す（履歴/出品待ち用）。"""
    try: import ctypes
    except Exception: return
    u = ctypes.windll.user32
    GWL_EXSTYLE = -20
    WS_EX_TOPMOST, WS_EX_NOACTIVATE = 0x00000008, 0x08000000
    HWND_TOPMOST, HWND_NOTOPMOST = -1, -2
    SWP = 0x0001 | 0x0002 | 0x0010   # NOSIZE | NOMOVE | NOACTIVATE
    st = {"off": None}
    def tick():
        if not win.winfo_exists(): return
        if not win.winfo_viewable():                  # 非表示(withdraw)中は z順を触らない＝無駄なSetWindowPosを止める
            win.after(200, tick); return
        if respect_toggle and not _always_top[0]:    # 設定で常に前面オフ→通常窓に戻す（1回だけ・以後触らない）
            if st["off"] is not True:
                try:
                    win.attributes("-topmost", False)
                    h = _top_hwnd(win)
                    u.SetWindowLongW(h, GWL_EXSTYLE, u.GetWindowLongW(h, GWL_EXSTYLE) & ~WS_EX_NOACTIVATE)
                    u.SetWindowPos(h, HWND_NOTOPMOST, 0, 0, 0, 0, SWP)
                except Exception: pass
                st["off"] = True
        else:
            st["off"] = False
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

def _pill_draw(cv, text):
    """pillをテキストに合わせて再描画（canvas幅・背景・文字を作り直す＝文字がはみ出さない）。
    minw を持つpillはそれ未満に縮めない（トグルで幅が変わって行が再レイアウト＝残像を防ぐ）。"""
    p = cv._pill
    font, padx, pady = p["font"], p["padx"], p["pady"]
    tw, th = font.measure(text), font.metrics("linespace")
    w, h = max(tw + padx * 2, p.get("minw", 0)), th + pady * 2
    cv.configure(width=w, height=h)
    cv.delete("all")
    f = p["fill"]
    cv.create_arc(0, 0, h, h, start=90, extent=180, fill=f, outline=f, tags="bg")
    cv.create_arc(w - h, 0, w, h, start=-90, extent=180, fill=f, outline=f, tags="bg")
    cv.create_rectangle(h / 2, 0, w - h / 2, h, fill=f, outline=f, tags="bg")
    cv.create_text(w / 2, h / 2 + 1, text=text, fill=p["fg"], font=font, tags="txt")

def round_pill(parent, text, fill, fg, cmd, font, padx=14, pady=6):
    """角丸（ピル型）ボタン。canvasで描画。テキスト変更時は _pill_set_text が幅ごと作り直す。"""
    cv = tk.Canvas(parent, bg=parent.cget("bg"), highlightthickness=0, cursor="hand2")
    cv._pill = {"font": font, "padx": padx, "pady": pady, "fill": fill, "fg": fg}
    cv.bind("<Button-1>", lambda e: cmd())
    _pill_draw(cv, text)
    return cv

def recolor_pill(cv, color):
    try:
        cv._pill["fill"] = color                        # 次回再描画でも色を保つ
        cv.itemconfig("bg", fill=color, outline=color)
    except Exception: pass

def _pill_set_text(cv, text):
    try: _pill_draw(cv, text)                            # 文字に合わせて幅・背景も作り直す（はみ出し防止）
    except Exception: pass

def _rrect(cv, x1, y1, x2, y2, r, fill, tag):
    """canvasに角丸矩形を描く（四隅arc＋十字rect）。"""
    cv.create_arc(x1, y1, x1 + 2*r, y1 + 2*r, start=90, extent=90, fill=fill, outline=fill, tags=tag)
    cv.create_arc(x2 - 2*r, y1, x2, y1 + 2*r, start=0, extent=90, fill=fill, outline=fill, tags=tag)
    cv.create_arc(x1, y2 - 2*r, x1 + 2*r, y2, start=180, extent=90, fill=fill, outline=fill, tags=tag)
    cv.create_arc(x2 - 2*r, y2 - 2*r, x2, y2, start=270, extent=90, fill=fill, outline=fill, tags=tag)
    cv.create_rectangle(x1 + r, y1, x2 - r, y2, fill=fill, outline=fill, tags=tag)
    cv.create_rectangle(x1, y1 + r, x2, y2 - r, fill=fill, outline=fill, tags=tag)

def _scrolling_body(win, inner_w=326):
    """縦スクロールする本体（履歴/出品待ち共通）。ダークテーマに馴染む自作スクロールバー付き。
    つまみの高さ＝『あと全体のどれだけあるか』、位置＝今どこか。掴んでドラッグ／トラッククリックで移動。
    全部が一画面に収まる時はバーを描かない（不要な飾りを出さない）。窓幅にも追従する。"""
    SBW = 12
    body = tk.Frame(win, bg=C_CARD); body.pack(fill="both", expand=True, padx=6, pady=(0, 8))
    canvas = tk.Canvas(body, bg=C_CARD, highlightthickness=0)
    inner = tk.Frame(canvas, bg=C_CARD)
    inner_id = canvas.create_window((0, 0), window=inner, anchor="nw", width=inner_w)
    bar = tk.Canvas(body, width=SBW, bg=C_CARD, highlightthickness=0, cursor="hand2")
    def redraw(*_):
        # 高さは本体canvasから取る（barウィジェットはfill="y"でも初期化が遅れ winfo_height()=1 のまま
        # ＝以前は毎回 h<=1 で早期returnして何も描かず「リサイズしないと出ない」不具合になっていた）。
        if not bar.winfo_exists(): return
        h = canvas.winfo_height()
        if h <= 1: return
        try: top, bot = canvas.yview()
        except Exception: return
        bar.configure(height=h)                       # bar自体も本体と同じ高さに（pack任せにしない）
        bar.delete("all")
        if bot - top >= 0.999: return                 # 全部見えてる→バー不要
        usable = h - 4
        _rrect(bar, 2, 2, SBW - 2, h - 2, (SBW - 4) / 2, "#3a3f4b", "trk")   # トラック
        y1, y2 = 2 + top * usable, 2 + bot * usable
        if y2 - y1 < 18:                              # 最小つまみ高（掴みやすく）
            mid = (y1 + y2) / 2
            y1, y2 = max(2, mid - 9), min(h - 2, mid + 9)
        _rrect(bar, 2, y1, SBW - 2, y2, (SBW - 4) / 2, "#6b7280", "thm")     # つまみ
    canvas.configure(yscrollcommand=redraw)            # スクロール時につまみ追従
    canvas._sb_redraw = redraw                          # 行の増減後に外から再描画させる用
    # scrollregion=bbox("all") は全行を走査するため、リサイズ中の連続Configureで毎回やると重い。
    # デバウンス：最後のイベントから60ms後に1回だけ再計算（リサイズが滑らかになる）。
    _rz = [None]
    def _recalc():
        _rz[0] = None
        if not canvas.winfo_exists(): return
        try: canvas.configure(scrollregion=canvas.bbox("all"))
        except Exception: return
        redraw()
    def _schedule_recalc():
        if _rz[0]:
            try: canvas.after_cancel(_rz[0])
            except Exception: pass
        _rz[0] = canvas.after(60, _recalc)
    canvas.bind("<Configure>", lambda e: (canvas.itemconfig(inner_id, width=e.width), _schedule_recalc()))
    inner.bind("<Configure>", lambda e: _schedule_recalc())
    def jump(ev):
        h = canvas.winfo_height()
        if h <= 1: return
        top, bot = canvas.yview(); span = bot - top
        frac = (ev.y - 2) / max(1, h - 4) - span / 2  # 掴んだ位置をつまみ中央に
        canvas.yview_moveto(max(0.0, min(1.0 - span, frac)))
    bar.bind("<Button-1>", jump); bar.bind("<B1-Motion>", jump)
    # 固定幅のバーを先にpackして右端の領域を確保→残りをcanvasがexpandで埋める（順序が逆だと
    # expandするcanvasが余白を食い、バーの取り分＝幅依存になって「狭いと縦バーが消える」不具合になる）
    bar.pack(side="right", fill="y", padx=(2, 0)); canvas.pack(side="left", fill="both", expand=True)
    def _wheel(e):                                     # 1ノッチ＝約100px（canvasの"units"は極小なのでpx換算＝ブラウザ並み）
        bb = canvas.bbox("all")
        if not bb: return
        content_h = bb[3] - bb[1]
        if content_h <= 1: return
        canvas.yview_moveto(canvas.yview()[0] + (-e.delta / 120) * 100 / content_h)
    win.bind("<MouseWheel>", _wheel)
    for _d in (60, 250):                               # 開いた直後＝レイアウト確定後に必ず一度描く
        canvas.after(_d, redraw)
    return canvas, inner

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
    state = {"entry": e, "rarity": init_rar}

    content = tk.Frame(win, bg=C_CARD); content.pack()   # 枠なし（ダークカードのみ）
    content.columnconfigure(0, weight=1)

    # アイテム名：読むだけのプレーンテキスト（編集は前面を奪うので不可。等級はマウスで選び直し可）
    name_lbl = tk.Label(content, text=init_name or "—", bg=C_CARD, fg=C_NAME, font=f_name, anchor="w")
    name_lbl.grid(row=0, column=0, sticky="we", padx=(14, 30), pady=(14, 6))   # 右上の✕分の余白

    # 等級：読むだけのプレーンテキスト。以前はドロップダウンだったが前面から裏に回る/消えて邪魔だったので廃止。
    # 選び直しは履歴一覧の右クリック（レア度変更）で行える。
    rar_lbl = tk.Label(content, text="", bg=C_CARD, font=f_meta, anchor="w")
    rar_lbl.grid(row=1, column=0, sticky="w", padx=14, pady=2)
    def build_rar_pill():
        r = state["rarity"]
        ent = state["entry"]
        rar_lbl.config(text=(disp_rarity(ent) if ent else "") or "", fg=rarity_color(r))

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
    if _suggest_lang[0]:                          # 別言語で当てて復帰した＝ゲームの言語に切替を促す
        _sl = _suggest_lang[0]
        def _do_switch(L=_sl):
            _apply_lang(L)                        # _ui_lang切替＋永続化（次回レンズから通常パスで読める）
            win.destroy()
        round_pill(btnf, T("lang_switch", lang=_LANG_NAME.get(_sl, _sl)),
                   C_WAIT, "#0c0c0c", _do_switch, f_meta).pack(side="left", padx=(6, 0))
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
        build_rar_pill()
        if ent and ent.get("_nolist"):             # 現在出品0件＝最安は「出品なし」。中央値(過去取引)があれば併記
            sc = ent.get("cur", 1)
            if ent.get("median") is not None:
                price_lbl.config(text=f"{T('low')} {T('nolisting')}   {T('med')} {price(ent['median'], sc)}", fg=ar)
                meta_lbl.config(text=f"{disp_type(ent)}   {T('sold')}{ent.get('volume','—')}")
            else:
                price_lbl.config(text=T("nolisting"), fg=C_META); meta_lbl.config(text=disp_type(ent))
        elif ent and ent.get("sell") is not None:
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

    win.bind("<Escape>", lambda ev: win.destroy())

    render(e)        # render 内で等級ラベルも更新
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
            w = next((x[0] for x in (_sell_win, _hist_win)              # 履歴/出品待ちのどちらか生きてる窓で描画
                      if x[0] and x[0].winfo_exists()), None)
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
    _save_hist(); _hist_remove_row(rec)              # その1行だけ撤去（全消ししない）

def _hist_fav(rec):
    rec["fav"] = not rec.get("fav"); _save_hist()
    for rd in _hist_rows:                            # 星をその場で反映（名前ラベルだけ書き換え）
        if rd["rec"] is rec and rd["name"].winfo_exists():
            rd["name"].config(text=("★ " if rec.get("fav") else "") + (disp_name(rec) or "?")); break
    _reorder_rows()                                  # お気に入りは上＝並べ替え（破棄しない）

def _hist_apply_ent(rec, ent):
    """確定したentで履歴recを差し替え（価格取得→その1行だけ作り直し）。fav/tsは保持。"""
    if not ent:
        _hist_after(_reorder_rows); return
    fav, ts = rec.get("fav"), rec.get("ts")
    ent = dict(ent)
    def work():
        apply_live(ent, native_ok=True, force=True)
        new = {k: ent.get(k) for k in ("ja", "en", "zh", "zh_hant", "icon", "rarity_en", "rarity_ja",
                                       "sell", "median", "volume", "cur", "hash",
                                       "type_ja", "type_en", "type", "_live", "_nolist")}
        new["fav"], new["ts"] = fav, ts
        if not new.get("icon"):                    # iconを落とすとレア度色タイル（背景）が出る＝必ず補完
            new["icon"] = _icon_by_hash().get(new.get("hash"), "")
        if rec in _hist: _hist[_hist.index(rec)] = new
        _save_hist()
        _hist_after(lambda: (_hist_replace_row(rec, new), _reorder_rows()))  # その1行だけ作り直し→並べ替え
    threading.Thread(target=work, daemon=True).start()

def _hist_set_rarity(rec, en):
    """レア度変更は完全一致で実エントリを引く（ファジー一致で別レア度に化けさせない＝Cosmic→Beyond根絶）。"""
    cands = list(_item_rarity_index().get(rec.get("en"), {}).get(en, []))
    if not cands: return                           # そのレア度は無い（メニューにも出さないので通常来ない）
    var = rec.get("variant")
    cands.sort(key=lambda e: (e.get("variant") != var, e.get("sell") is None, -(e.get("sell") or 0)))
    _hist_apply_ent(rec, cands[0])

def _hist_rename(rec):
    def on_ok(s):
        if not s: return
        rmap = {en: ja for en, ja in RARITIES}
        rar = rec.get("rarity_en")
        r = matcher.match_item(s, rmap.get(rar, rar) if rar else "")   # 改名は名前ファジー一致
        _hist_apply_ent(rec, r[0] if r else None)
    _ask_text(T("rename_title"), rec.get("ja") or rec.get("en") or "", on_ok)

def _hist_apply_cache():
    """履歴の価格を、メモリ内キャッシュ（一括USD/現地）だけで再表示（ネット非使用＝言語切替で叩かない）。"""
    _hist_gen[0] += 1                             # 実行中の全部更新（旧通貨）を中断
    for rec in _hist:
        if not rec.get("hash"): continue
        apply_live(rec, native_ok=True, cache_only=True)

_hist_gen = [0]            # 全部更新の世代。新しい更新が始まると古い取得は中断（言語連続切替の競合防止）
_hist_updating = [False]   # 全部更新が実行中か（連打で多重起動しないように）
_HIST_UPD_COOLDOWN = 8     # 全部更新の最短間隔(秒)。完了直後の連打でSteamを叩きすぎないように
_hist_upd_cooldown = [0.0] # 次に全部更新を受け付けてよい時刻（monotonic）
_hist_update_btn = [None]  # 「全部更新」ボタン（実行中は表示を変える）

def _upd_btn_text():
    """全部更新ボタンの通常表示。最終更新時刻があれば併記（pillは自動で幅が伸びる）。"""
    t = "↻ " + T("update_all")
    if _hist_last_update[0]:
        t += f"  {T('hist_last')} {_hist_last_update[0]}"
    return t

def _hist_update_all(force=True):
    if _hist_updating[0]: return                       # 実行中の連打は無視（多重起動しない）
    if time.monotonic() < _hist_upd_cooldown[0]: return  # 完了直後の連打も無視（Steam連打防止のクールダウン）
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
            before = rec.get("sell"); before_cur = rec.get("cur", 1)
            apply_live(rec, native_ok=False, force=force)   # 単品USD（search/render・BANされない）
            if not alive(): return
            after = rec.get("sell")
            if before is not None and after is not None and after != before \
               and rec.get("cur", 1) == before_cur:        # 同一通貨同士でだけ差額を出す（換算前の生値で比較）
                rec["_delta"] = after - before; rec["_delta_cur"] = before_cur
            else:
                rec.pop("_delta", None); rec.pop("_delta_cur", None)   # 変化なし/比較不能は前回の上下を消す
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
        _hist_upd_cooldown[0] = time.monotonic() + _HIST_UPD_COOLDOWN   # 直後の連打は無視（クールダウン）
        _hist_prog_state["on"] = False                 # バー消灯＋アニメ停止
        if not alive():
            _hist_after(lambda: _btn(_upd_btn_text()))
            return
        _hist_last_update[0] = _stamp_str()            # 最終更新時刻＝ボタンに常表示（設定で永続）
        _save_hist(); _save_settings()
        def _fin():                                    # ✓を一瞬見せてから通常表示（時刻入り）へ（完了の手応え）
            _btn("✓ " + T("update_all"))
            s = _hist_status[0]
            if s and s.winfo_exists(): s.config(text="")   # 時刻はボタンに出すのでステータスは消す
            b = _hist_update_btn[0]
            if b and b.winfo_exists():
                b.after(1400, lambda: b.winfo_exists() and _pill_set_text(b, _upd_btn_text()))
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

_item_idx = [None]
def _item_rarity_index():
    """en名 → {rarity_en: [entry,...]} を一度だけ構築（右クリック毎の全件走査をやめる）。"""
    if _item_idx[0] is None:
        idx = {}
        for e in matcher.entries:
            en, rar = e.get("en"), e.get("rarity_en")
            if en and rar: idx.setdefault(en, {}).setdefault(rar, []).append(e)
        _item_idx[0] = idx
    return _item_idx[0]

def _rarities_for(rec):
    """このアイテムが実際に持つレア度(en)を Legendary〜Cosmic の順で返す。
    存在しないレア度を選ぶと別レア度に化ける（例 Cosmic→Beyond）ので、無いものはメニューに出さない。"""
    have = _item_rarity_index().get(rec.get("en"), {})
    order = [r for r, _ in RARITIES[3:]]         # レジェンダリー〜コズミック
    return [r for r in order if r in have] or order

_row_menu_ref = [None]                            # 直近の右クリックメニュー（次回生成時に破棄＝溜めない）
def _row_menu(ev, rec):
    if _row_menu_ref[0] is not None:              # tk_popupは破棄しないので、前回のMenuをここで捨てる（リーク防止）
        try: _row_menu_ref[0].destroy()
        except Exception: pass
    m = tk.Menu(_hist_win[0], tearoff=0, bg="#0d1016", fg=C_NAME, activebackground="#2a2f3a",
                activeforeground="#ffffff", bd=0)
    _row_menu_ref[0] = m
    ja = _ui_lang == "ja"
    m.add_command(label=(T("unfav") if rec.get("fav") else T("fav")),
                  command=lambda: _hist_fav(rec))
    m.add_command(label=T("rename"), command=lambda: _hist_rename(rec))
    rm = tk.Menu(m, tearoff=0, bg="#0d1016", fg=C_NAME, activebackground="#2a2f3a",
                 activeforeground="#ffffff", bd=0)
    rja = dict(RARITIES)
    for en in _rarities_for(rec):                # そのアイテムが実際に持つレア度だけ（化け防止＋レジェ〜コズミック限定）
        rm.add_command(label=(rja[en] if ja else en), foreground=rarity_color(en),
                       command=lambda en=en: _hist_set_rarity(rec, en))
    m.add_cascade(label=T("rarity_change"), menu=rm)
    m.add_separator()
    m.add_command(label=T("delete"), command=lambda: _hist_delete(rec))
    m.tk_popup(ev.x_root, ev.y_root)


def _set_row_price(rd):
    rec = rd["rec"]
    if rec.get("_nolist"):                          # 現在出品なし。中央値(過去取引)があれば併記
        sc = rec.get("cur", 1)
        if rec.get("median") is not None:
            rd["price"].config(text=f"{T('low')} {T('nolisting')}   {T('med')} {price(rec['median'], sc)}", fg=C_PRICE)
        else:
            rd["price"].config(text=T("nolisting"), fg=C_META)
    elif rec.get("sell") is not None:
        sc = rec.get("cur", 1)
        txt = f"{T('low')} {price(rec['sell'], sc)}   {T('med')} {price(rec['median'], sc)}"
        rd["price"].config(text=txt, fg=C_PRICE)   # 普通の価格色（概算は数値の≈だけで示す）
    else:
        rd["price"].config(text=T("noprice"), fg=C_META)
    # 前回更新からの最安値の上下（↑/↓＋差額）。安くなった=緑・高くなった=赤、変化なしは非表示
    dl = rd.get("delta")
    if dl is not None and dl.winfo_exists():
        d = rec.get("_delta")
        if d:
            up = d > 0
            dl.config(text=("↑ " if up else "↓ ") + price(abs(d), rec.get("_delta_cur", rec.get("cur", 1))),
                      fg=("#f87171" if up else "#34d399"))
        else:
            dl.config(text="")

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
    part, lv = split_type_level(rec)
    name_lbl = tk.Label(top, text=star + nm, bg=C_CARD, fg=ar,
                        font=("Yu Gothic UI", 10, "bold"), anchor="w"); name_lbl.pack(side="left")
    if lv:                                              # 名前のうしろに必要Lv（グレー・小さめ）：「アイテム名 Lv80」
        tk.Label(top, text=lv, bg=C_CARD, fg=C_META, font=("Yu Gothic UI", 8),
                 anchor="sw").pack(side="left", padx=(4, 0), pady=(0, 1))
    prow = tk.Frame(col, bg=C_CARD); prow.pack(fill="x")
    price_lbl = tk.Label(prow, text="", bg=C_CARD, font=("Yu Gothic UI", 9), anchor="w"); price_lbl.pack(side="left")
    delta_lbl = tk.Label(prow, text="", bg=C_CARD, font=("Yu Gothic UI", 9, "bold"), anchor="e"); delta_lbl.pack(side="right")
    bot = tk.Frame(col, bg=C_CARD); bot.pack(fill="x")  # 下の段：左にレア度＋部位、右下に追加日時
    meta_txt = " ".join(t for t in (rj, part) if t)    # レア度＋部位を空白区切り（例「アルカナ 弓」、Lvは上段へ）
    if meta_txt:
        tk.Label(bot, text=meta_txt, bg=C_CARD, fg=C_META, font=("Yu Gothic UI", 8), anchor="w").pack(side="left")
    ts_lbl = tk.Label(bot, text=rec.get("ts", ""), bg=C_CARD, fg=C_META,
                      font=("Yu Gothic UI", 8), anchor="e"); ts_lbl.pack(side="right")
    sep = tk.Frame(inner, bg="#2a2f3a", height=1)
    rd = {"rec": rec, "frame": row, "sep": sep, "price": price_lbl, "delta": delta_lbl,
          "icon": icon_lbl, "name": name_lbl, "ts": ts_lbl}
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
        try:
            c.update_idletasks(); c.configure(scrollregion=c.bbox("all"))
            if hasattr(c, "_sb_redraw"): c._sb_redraw()   # 行数が変わったらスクロールバーも更新
        except Exception: pass

def _hist_ordered():
    """ソート設定に従って履歴を並べる（お気に入りは常に上）。価格なしは末尾。"""
    items = list(_hist)
    mode, desc = _hist_sort[0], _hist_sort_desc[0]
    if mode in ("sell", "median"):
        def num(r):
            v = r.get(mode)
            return v if isinstance(v, (int, float)) else None
        have = sorted([r for r in items if num(r) is not None], key=num, reverse=desc)
        items = have + [r for r in items if num(r) is None]   # 価格なし(出品なし等)は常に末尾
    elif not desc:                                            # 追加日・昇順（古い順）。降順は_hist既定(新しい順)のまま
        items = list(reversed(items))
    favs = [r for r in items if r.get("fav")]                 # お気に入りは各並びを保ったまま上へ
    return favs + [r for r in items if not r.get("fav")]

def _refresh_history():
    """全再構築（開いた時・並べ替え・削除/お気に入り/改名/等級変更/言語/上限変更など構造が変わる時）。"""
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
    for rec in _hist_ordered():                                    # ソート設定順（お気に入りは上）
        rd = _build_hist_row(rec)
        rd["frame"].pack(fill="x", padx=6, pady=(4, 0)); rd["sep"].pack(fill="x", padx=6, pady=(4, 0))
        _hist_rows.append(rd)
    if _hist_sort_face[0]: _hist_sort_face[0]()                    # ソートpillの見た目も同期（言語切替時など）
    _hist_scroll()

def _reorder_rows():
    """既存の行ウィジェットを破棄せずソート順に並べ替える（全再構築のチラつき＝崩れ を避ける）。
    アイコン再取得も無く、pack順だけ変えるので軽い。"""
    if not (_hist_win[0] and _hist_win[0].winfo_exists() and _hist_inner[0] and _hist_rows): return
    pos = {id(rec): i for i, rec in enumerate(_hist_ordered())}
    _hist_rows.sort(key=lambda rd: pos.get(id(rd["rec"]), 10**9))
    for rd in _hist_rows:                                          # 一旦外して
        rd["frame"].pack_forget(); rd["sep"].pack_forget()
    for rd in _hist_rows:                                          # 新しい順に貼り直す（同一ハンドラ内＝中間描画なし）
        rd["frame"].pack(fill="x", padx=6, pady=(4, 0)); rd["sep"].pack(fill="x", padx=6, pady=(4, 0))
    if _hist_sort_face[0]: _hist_sort_face[0]()
    _hist_scroll()

def _hist_remove_row(rec):
    """1行だけ撤去（全消ししない）。削除用。"""
    for rd in _hist_rows[:]:
        if rd["rec"] is rec:
            try: rd["frame"].destroy(); rd["sep"].destroy()
            except Exception: pass
            _hist_rows.remove(rd); break
    if not _hist_rows and _hist_win[0] and _hist_inner[0]:          # 空になったら空表示へ
        _refresh_history(); return
    _hist_scroll()

def _hist_replace_row(old_rec, new_rec):
    """1行だけ作り直して同じ位置に置く（全消ししない）。改名/レア度変更用。"""
    for i, rd in enumerate(_hist_rows):
        if rd["rec"] is old_rec:
            nrd = _build_hist_row(new_rec)
            nrd["frame"].pack(fill="x", padx=6, pady=(4, 0), before=rd["frame"])
            nrd["sep"].pack(fill="x", padx=6, pady=(4, 0), before=rd["frame"])
            try: rd["frame"].destroy(); rd["sep"].destroy()
            except Exception: pass
            _hist_rows[i] = nrd; return nrd
    return None

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
    for rd in _hist_rows:                            # 既出（同一recの再レンズ）→その場更新（位置はそのまま）
        if rd["rec"] is rec:
            if rd["price"].winfo_exists(): _set_row_price(rd); rd["ts"].config(text=rec.get("ts", ""))
            return
    if not _hist_rows:                               # 空表示ラベルがあれば消す
        for w in inner.winfo_children():
            try: w.destroy()
            except Exception: pass
    rd = _build_hist_row(rec)
    # 新規1行だけを「ソート順での正しい位置」に差し込む。既存行は一切動かさない＝全消し/再構築なし
    # ＝レンズ中に履歴が一瞬消える現象を防ぐ（どのソートでも増分挿入）。
    order = _hist_ordered(); ids = [id(r) for r in order]
    try: nxt = order[ids.index(id(rec)) + 1]          # recの直後に来るべきrec
    except (ValueError, IndexError): nxt = None
    anchor = next((r for r in _hist_rows if r["rec"] is nxt), None) if nxt is not None else None
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
    _hist_visible[0] = True; _save_settings()      # 開いた状態を保存（再起動後も復元）
    if _hist_win[0] and _hist_win[0].winfo_exists():
        _hist_win[0].deiconify(); _refresh_history(); return
    win = tk.Toplevel(root)
    win.config(bg=C_CARD); win.attributes("-topmost", True)
    f_hbtn = tkfont.Font(family="Yu Gothic UI", size=9)
    # カスタム上部（ダーク・角丸・ドラッグ・✕）。タイトルと✕の間に「全部更新」を置く＝1行に集約
    hdr = _modern_titlebar(win, T("hist_title"), lambda: toggle_history(root))
    _hist_update_btn[0] = round_pill(hdr, ("⏳ " + T("updating_btn")) if _hist_updating[0] else _upd_btn_text(),
                                     C_ACCENT, "#0c0c0c", _hist_update_all, f_hbtn)
    _hist_update_btn[0].pack(side="right", padx=(0, 6))
    win.geometry(_hist_geo[0] or "360x460")      # overrideredirect後にサイズ/位置を適用
    def _remember_geo(e):                        # 移動/リサイズを記憶（次回復元）
        if e.widget is win and win.winfo_width() > 80:
            _hist_geo[0] = win.geometry()
    win.bind("<Configure>", _remember_geo)
    # ── 並べ替え：追加日 / 最安 / 中央値（単一選択。選択中を再タップで昇順⇄降順） ──
    sortf = tk.Frame(win, bg=C_CARD); sortf.pack(side="top", fill="x", padx=12, pady=(2, 2))
    _hist_sort_pills.clear()
    def _sort_face():
        for mode, pill, lab in _hist_sort_pills:
            on = (_hist_sort[0] == mode)
            arrow = (" ▼" if _hist_sort_desc[0] else " ▲") if on else ""
            pill._pill["fill"] = C_ACCENT if on else "#2a2f3a"   # 色を先に確定→1回の再描画で確定（残像なし）
            pill._pill["fg"] = "#0c0c0c" if on else C_NAME
            _pill_set_text(pill, lab() + arrow)
    _hist_sort_face[0] = _sort_face
    def _pick_sort(m):
        if _hist_sort[0] == m: _hist_sort_desc[0] = not _hist_sort_desc[0]   # 同じソート再タップ＝方向反転
        else: _hist_sort[0] = m; _hist_sort_desc[0] = True                   # 切替時は降順（新しい/高い順）から
        _save_settings(); _sort_face(); _reorder_rows()                      # 破棄せず並べ替え（崩れない）
    for m, lab in (("date", lambda: T("sort_added")), ("sell", lambda: T("low")), ("median", lambda: T("med"))):
        p = round_pill(sortf, lab() + " ▼", "#2a2f3a", C_NAME, (lambda mm=m: _pick_sort(mm)), f_hbtn)
        p._pill["minw"] = f_hbtn.measure(lab() + " ▼") + p._pill["padx"] * 2   # 矢印込み幅で固定＝トグルで幅が変わらない
        p.pack(side="left", padx=(0, 6)); _hist_sort_pills.append((m, p, lab))
    _sort_face()
    _hist_status[0] = tk.Label(win, text="", bg=C_CARD, fg=C_ACCENT,
                               font=("Yu Gothic UI", 9), anchor="w")
    _hist_status[0].pack(side="top", fill="x", padx=12, pady=(0, 2))
    _hist_prog[0] = tk.Canvas(win, height=6, bg=C_CARD, highlightthickness=0)   # 進捗バー（更新中だけ見える）
    _hist_prog[0].pack(side="top", fill="x", padx=12, pady=(0, 4))
    if _hist_updating[0]: _prog_anim()                 # 開き直した時に更新中なら即アニメ再開
    canvas, inner = _scrolling_body(win)
    _hist_win[0] = win; _hist_inner[0] = (canvas, inner)
    # NOACTIVATE維持＝アクティブ化で前面を奪わない→ゲームが覆い被さらない（時間で消えない）。
    # クリック/右クリックは受け取れる。スクロールはWin11の「非アクティブ窓もスクロール」既定で可。
    # ポップ表示中(_open非空)は最前面の再主張を休む＝価格ポップと奪い合わずチラつかない。
    _keep_on_top(win, pause=lambda: bool(_open), respect_toggle=True)   # 『常に前面』設定に従う
    _add_resize_grip(win)                        # 右下グリップでリサイズ（OS枠が無いため）
    _refresh_history()

def hide_history():
    _hist_visible[0] = False
    if _hist_win[0]:
        try:
            if _hist_win[0].winfo_width() > 80: _hist_geo[0] = _hist_win[0].geometry()
            _hist_win[0].withdraw()
        except Exception: pass
    _save_settings()           # 位置・サイズ＋開閉状態を保存

def toggle_history(root):
    _hist_visible[0] = not _hist_visible[0]
    if _hist_visible[0]: show_history(root)
    else: hide_history()


def show_ocr_warn(root):
    """このPCに表示言語のOCRが無い時、原因と直し方をUIで案内（黙って「該当なし」にしない）。"""
    if _ocrwarn_win[0] and _ocrwarn_win[0].winfo_exists():
        _ocrwarn_win[0].deiconify(); _ocrwarn_win[0].lift(); return
    win = tk.Toplevel(root); win.config(bg=C_CARD); win.attributes("-topmost", True); win.resizable(False, False)
    win.title(APP_NAME); _ocrwarn_win[0] = win
    f = tkfont.Font(family="Yu Gothic UI", size=11)
    lang = LANG_NAMES.get(_ui_lang, _ui_lang)
    tk.Label(win, text="⚠  " + T("ocr_missing_title"), bg=C_CARD, fg=C_NAME,
             font=("Yu Gothic UI", 13, "bold"), anchor="w").pack(fill="x", padx=18, pady=(16, 6))
    tk.Label(win, text=T("ocr_missing_msg", lang=lang), bg=C_CARD, fg=C_META, font=f,
             justify="left", anchor="w").pack(fill="x", padx=18, pady=(0, 14))
    bf = tk.Frame(win, bg=C_CARD); bf.pack(fill="x", padx=18, pady=(0, 16))
    def _open():
        try: os.startfile("ms-settings:regionlanguage")     # Windowsの言語設定を開く
        except Exception: pass
    round_pill(bf, T("ocr_open_settings"), C_ACCENT, "#0c0c0c", _open, f).pack(side="left")
    round_pill(bf, T("close"), "#2a2f3a", C_NAME, win.destroy, f).pack(side="right")
    win.bind("<Escape>", lambda e: win.destroy())
    _grab_foreground(win)

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
        for w in (_help_win, _fb_win, _hist_win, _hist_inner, _sell_win, _sell_inner, _ocrwarn_win):
            if w[0] is not None:
                try:
                    if hasattr(w[0], "destroy"): w[0].destroy()
                except Exception: pass
                w[0] = None
        _hist_apply_cache()                          # 新通貨のキャッシュ価格を反映（ネット非使用）
        if _hist_visible[0]:
            show_history(root)                       # 履歴は開いていたら新言語で開き直す
        if _sell_visible[0]:
            show_sell(root)                          # 出品待ちも開いていたら新言語で開き直す
        geo = win.geometry()
        pos = ("+" + geo.split("+", 1)[1]) if "+" in geo else ""   # 位置を保持して建て直し（動かない）
        win.destroy(); _set_win[0] = None; show_settings(root)
        if pos and _set_win[0]:
            try: _set_win[0].geometry(pos)
            except Exception: pass
        if _ocr_lang_missing():                      # 切替先言語のOCRが無い→その場で案内（黙って読めない、を避ける）
            root.after(200, lambda: show_ocr_warn(root))
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

    # ── Windowsと一緒に起動（HKCU\Run。レジストリが真実） ──
    cas = section(T("startup"))
    asf = tk.Frame(cas, bg="#11141a"); asf.pack(fill="x", padx=14, pady=(0, 4))
    as_state = [_autostart_get()]
    as_pill = [None]
    def _as_face():
        on = as_state[0]
        _pill_set_text(as_pill[0], ("☑ " if on else "☐ ") + T("autostart_label"))
        recolor_pill(as_pill[0], C_ACCENT if on else "#2a2f3a")
        try: as_pill[0].itemconfig("txt", fill="#0c0c0c" if on else C_NAME)
        except Exception: pass
    def toggle_as():
        want = not as_state[0]
        if _autostart_set(want): as_state[0] = want   # 書けた時だけトグル（失敗時は状態維持）
        _as_face()
    as_pill[0] = round_pill(asf, "☑ " + T("autostart_label"), C_ACCENT, "#0c0c0c", toggle_as, fs)
    as_pill[0].pack(side="left"); _as_face()
    tk.Label(cas, text=T("autostart_hint"), bg="#11141a", fg=C_META, font=fs,
             anchor="w", justify="left", wraplength=300).pack(fill="x", padx=14, pady=(4, 10))
    # 常に前面（履歴/出品待ち。実行中の窓は120ms毎のループが拾うので即反映）
    atf = tk.Frame(cas, bg="#11141a"); atf.pack(fill="x", padx=14, pady=(0, 4))
    at_pill = [None]
    def _at_face():
        on = _always_top[0]
        _pill_set_text(at_pill[0], ("☑ " if on else "☐ ") + T("alwaystop_label"))
        recolor_pill(at_pill[0], C_ACCENT if on else "#2a2f3a")
        try: at_pill[0].itemconfig("txt", fill="#0c0c0c" if on else C_NAME)
        except Exception: pass
    def toggle_at():
        _always_top[0] = not _always_top[0]; _save_settings(); _at_face()
    at_pill[0] = round_pill(atf, "☑ " + T("alwaystop_label"), C_ACCENT, "#0c0c0c", toggle_at, fs)
    at_pill[0].pack(side="left"); _at_face()
    tk.Label(cas, text=T("alwaystop_hint"), bg="#11141a", fg=C_META, font=fs,
             anchor="w", justify="left", wraplength=300).pack(fill="x", padx=14, pady=(4, 12))

    # ── 利用統計（匿名・オフ可） ──
    c3 = section(T("privacy"))
    pf = tk.Frame(c3, bg="#11141a"); pf.pack(fill="x", padx=14, pady=(0, 4))
    tele_pill = [None]
    def _tele_face():
        on = _telemetry[0]
        _pill_set_text(tele_pill[0], ("☑ " if on else "☐ ") + T("telemetry_label"))
        recolor_pill(tele_pill[0], C_ACCENT if on else "#2a2f3a")
        try: tele_pill[0].itemconfig("txt", fill="#0c0c0c" if on else C_NAME)
        except Exception: pass
    def toggle_tele():
        _telemetry[0] = not _telemetry[0]; _save_settings(); _tele_face()
    tele_pill[0] = round_pill(pf, "☑ " + T("telemetry_label"), C_ACCENT, "#0c0c0c", toggle_tele, fs)
    tele_pill[0].pack(side="left"); _tele_face()
    tk.Label(c3, text=T("telemetry_hint"), bg="#11141a", fg=C_META, font=fs,
             anchor="w", justify="left", wraplength=300).pack(fill="x", padx=14, pady=(4, 12))

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
    tk.Frame(win, bg=C_CARD, height=12).pack(fill="x")   # 下の余白
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
            if results == "__sell__":              # トレイから出品待ち表示の同期
                if _sell_visible[0]: show_sell(root)
                else: hide_sell(root)
                continue
            if results == "__hist_trim__":         # 上限変更→切り詰め。押し出された行だけ撤去（全消ししない）
                _hist_trim(); _save_hist()
                present = {id(r) for r in _hist}
                for rd in _hist_rows[:]:
                    if id(rd["rec"]) not in present:
                        try: rd["frame"].destroy(); rd["sep"].destroy()
                        except Exception: pass
                        _hist_rows.remove(rd)
                _hist_scroll()
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
            if isinstance(results, list) and results:   # マッチ時のみ履歴記録（メインスレッド＝_hist競合回避）
                _record_history(results[0])
            # レンズ時は最新1件だけ増分反映（全消ししない＝チラつかない）。読み取り中は何もしない。
            if _hist_visible[0] and results != "__processing__":
                _hist_sync_top()
    except queue.Empty:
        pass
    root.after(80, lambda: poll(root))


# ---- 出品待ち（Steam在庫の出品可否追跡）-----------------------------------
# 新inventory API（assets/descriptions形式）を使う。インベントリが「公開」なら未ログインで200、
# 非公開だと403。取れるのは marketable/tradable フラグだけ。正確な解除日(owner_descriptions)は
# owner限定でCookieが要り公開では取れない→「あと何日」は出さず、取れた事実(出品可/不可)のみ表示し、
# marketableが0→1に変わった瞬間（＝実取得のflip）をトレイ通知する。
TBH_INV_APPID = "3678970"
SELL_FILE = os.path.join(HERE, "tbh-sell-state.json")

_sell_win = [None]
_sell_inner = [None]               # (canvas, inner)
_sell_visible = [False]
_sell_geo = [None]
_sell_state = {"status": "init", "items": [], "ts": 0}   # 直近の取得結果
_sell_known = {}                   # assetid -> 直近のmarketable（出品可flip通知用・永続）
_tray_icon = [None]                # 通知用にトレイIconを保持

def _detect_steamid():
    """ローカルSteamからログイン中のSteamID64を取得（レジストリ→loginusers.vdf）。配布時も各PCで自動。"""
    try:
        import winreg
        k = winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam\ActiveProcess")
        au = winreg.QueryValueEx(k, "ActiveUser")[0]
        if au:
            return str(76561197960265728 + int(au))
    except Exception:
        pass
    try:
        import winreg
        k = winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam")
        path = winreg.QueryValueEx(k, "SteamPath")[0]
    except Exception:
        path = r"C:\Program Files (x86)\Steam"
    try:
        vdf = os.path.join(path.replace("/", "\\"), "config", "loginusers.vdf")
        txt = open(vdf, encoding="utf-8", errors="ignore").read()
        best = None
        for m in re.finditer(r'"(7656\d{13})"\s*\{(.*?)\}', txt, re.S):
            sid, body = m.group(1), m.group(2)
            if best is None:
                best = sid
            if re.search(r'"MostRecent"\s*"1"', body):
                return sid
        return best
    except Exception:
        return None

def _steam_privacy_url():
    sid = _detect_steamid()
    return f"https://steamcommunity.com/profiles/{sid}/edit/settings" if sid \
        else "https://steamcommunity.com/my/edit/settings"

def _save_sell_state():
    try:
        with open(SELL_FILE, "w", encoding="utf-8") as f:
            json.dump({"known": _sell_known}, f, ensure_ascii=False)
    except Exception:
        pass

def _load_sell_state():
    try:
        d = json.load(open(SELL_FILE, encoding="utf-8"))
        _sell_known.update({str(k): int(v) for k, v in (d.get("known") or {}).items()})
    except Exception:
        pass

def _sell_notify(names):
    try:
        ic = _tray_icon[0]
        if ic:
            body = "、".join(names[:5]) + ("…" if len(names) > 5 else "")
            ic.notify(T("sell_notify", name=body), T("sell_notify_title"))
    except Exception:
        pass

def _sell_fetch():
    """Steam在庫を取得して _sell_state を更新（バックグラウンドスレッドから呼ぶ）。
    新inventory API（assets/descriptions形式）を使う。公開インベントリなら未ログインで200、
    非公開だと403。出品可否は marketable で判定（owner限定の解除日は公開では取れない）。"""
    sid = _detect_steamid()
    if not sid:
        _sell_state.update(status="no_steam", items=[]); return
    lang = {"ja": "japanese", "zh": "schinese"}.get(_ui_lang, "english")
    url = f"https://steamcommunity.com/inventory/{sid}/{TBH_INV_APPID}/2?l={lang}&count=2000"
    try:
        req = urllib.request.Request(url, headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120 Safari/537.36"})
        d = json.loads(urllib.request.urlopen(req, timeout=20).read())
    except urllib.error.HTTPError as e:
        _sell_state.update(status="private" if e.code == 403 else "error"); return   # 非公開＝403
    except Exception:
        _sell_state.update(status="error"); return
    if not d.get("success"):
        _sell_state.update(status="error"); return
    descs = {f"{x.get('classid')}_{x.get('instanceid')}": x for x in (d.get("descriptions") or [])}
    now = time.time()
    items = []
    for a in (d.get("assets") or []):
        x = descs.get(f"{a.get('classid')}_{a.get('instanceid')}") or {}
        rar = ""
        for t in (x.get("tags") or []):
            if (t.get("category") or "").lower() in ("rarity", "quality"):
                rar = t.get("internal_name") or t.get("name") or ""
        items.append({
            "assetid": str(a.get("assetid") or ""),
            "name": x.get("name") or x.get("market_hash_name") or "?",
            "icon": x.get("icon_url") or "",
            "tradable": int(x.get("tradable") or 0),
            "marketable": int(x.get("marketable") or 0),
            "rarity": rar,
        })
    notify = []
    for it in items:
        aid = it["assetid"]
        if _sell_known.get(aid) == 0 and it["marketable"] == 1:  # 出品不可→出品可 を検知（通知の根拠＝実取得のflip）
            notify.append(it["name"])
        _sell_known[aid] = it["marketable"]
    live = {it["assetid"] for it in items}                       # 在庫から消えたものを掃除
    for aid in list(_sell_known):
        if aid not in live: _sell_known.pop(aid, None)
    _save_sell_state()
    _sell_state.update(status=("empty" if not items else "ok"), items=items, ts=now)
    if notify:
        _sell_notify(notify)

def _sell_refresh_async():
    def work():
        _sell_fetch()
        w = _sell_win[0]
        if w:
            try: w.after(0, _refresh_sell)
            except Exception: pass
    threading.Thread(target=work, daemon=True).start()

def _sell_poller():
    """30分ごとに在庫を確認（窓を開いていなくても解除通知が出るように）。"""
    while True:
        try:
            _sell_fetch()
            w = _sell_win[0]
            if w:
                try: w.after(0, _refresh_sell)
                except Exception: pass
        except Exception:
            pass
        time.sleep(1800)

def _set_sell_loading():
    _sell_state["status"] = "loading"; _refresh_sell()

def _sell_scroll():
    c = _sell_inner[0][0] if _sell_inner[0] else None
    if c:
        try:
            c.update_idletasks(); c.configure(scrollregion=c.bbox("all"))
            if hasattr(c, "_sb_redraw"): c._sb_redraw()   # 行数が変わったらスクロールバーも更新
        except Exception: pass

def _sell_agg(items):
    """同名アイテムを ×個数 にまとめる（解除日でまとめた後、見た目の重複を畳む）。"""
    out = {}
    order = []
    for it in items:
        k = it["name"]
        if k not in out:
            out[k] = {"name": it["name"], "icon": it["icon"], "rarity": it["rarity"], "count": 0}
            order.append(k)
        out[k]["count"] += 1
    return [out[k] for k in order]

def _sell_header(inner, text, color):
    h = tk.Frame(inner, bg=C_CARD); h.pack(fill="x", padx=8, pady=(10, 2))
    tk.Label(h, text=text, bg=C_CARD, fg=color, font=("Yu Gothic UI", 10, "bold"), anchor="w").pack(side="left")

def _sell_item_row(inner, g, right_text, right_color):
    ar = rarity_color(g.get("rarity") or "")
    row = tk.Frame(inner, bg=C_CARD); row.pack(fill="x", padx=6, pady=(4, 0))
    img = _blank_icon[0] if g.get("icon") else _placeholder_icon(ar)
    il = tk.Label(row, bg=C_CARD, image=img); il.pack(side="left", padx=(2, 8))
    if right_text:
        tk.Label(row, text=right_text, bg=C_CARD, fg=right_color,
                 font=("Yu Gothic UI", 9, "bold")).pack(side="right", padx=(6, 4))
    nm = g["name"] + (f"  ×{g['count']}" if g.get("count", 1) > 1 else "")
    tk.Label(row, text=nm, bg=C_CARD, fg=ar, font=("Yu Gothic UI", 10, "bold"),
             anchor="w").pack(side="left", fill="x", expand=True)
    if g.get("icon"):
        _get_icon(g["icon"], lambda ph, L=il: L.winfo_exists() and L.config(image=ph))
    tk.Frame(inner, bg="#2a2f3a", height=1).pack(fill="x", padx=6, pady=(4, 0))

def _refresh_sell():
    if not (_sell_win[0] and _sell_win[0].winfo_exists() and _sell_inner[0]): return
    inner = _sell_inner[0][1]
    for w in inner.winfo_children():
        try: w.destroy()
        except Exception: pass
    if _blank_icon[0] is None:
        try:
            from PIL import ImageTk
            _blank_icon[0] = ImageTk.PhotoImage(Image.new("RGBA", (ICON_PX, ICON_PX), (0, 0, 0, 0)))
        except Exception: pass
    fb = tkfont.Font(family="Yu Gothic UI", size=10)
    st = _sell_state.get("status")
    if st in (None, "init", "loading"):
        tk.Label(inner, text=T("sell_loading"), bg=C_CARD, fg=C_META, anchor="w").pack(fill="x", padx=12, pady=10)
        _sell_scroll(); return
    if st == "no_steam":
        tk.Label(inner, text=T("sell_no_steam"), bg=C_CARD, fg=C_META, anchor="w",
                 justify="left", wraplength=300).pack(fill="x", padx=12, pady=10)
        _sell_scroll(); return
    if st in ("private", "error"):
        box = tk.Frame(inner, bg=C_CARD); box.pack(fill="x", padx=14, pady=12)
        if st == "private":
            tk.Label(box, text=T("sell_private_title"), bg=C_CARD, fg=C_NAME, font=("Yu Gothic UI", 11, "bold"),
                     anchor="w", justify="left", wraplength=300).pack(fill="x", anchor="w")
            tk.Label(box, text=T("sell_private_msg"), bg=C_CARD, fg=C_META,
                     anchor="w", justify="left", wraplength=300).pack(fill="x", anchor="w", pady=(4, 10))
            round_pill(box, "🔓 " + T("sell_open_privacy"), C_ACCENT, "#0c0c0c",
                       lambda: webbrowser.open(_steam_privacy_url()), fb).pack(anchor="w")
        else:
            tk.Label(box, text=T("sell_error"), bg=C_CARD, fg=C_ERR, anchor="w").pack(fill="x", anchor="w", pady=(0, 8))
        round_pill(box, "↻ " + T("sell_recheck"), "#2a2f3a", C_NAME,
                   lambda: (_set_sell_loading(), _sell_refresh_async()), fb).pack(anchor="w", pady=(6, 0))
        _sell_scroll(); return
    items = _sell_state.get("items") or []
    if st == "empty" or not items:
        tk.Label(inner, text=T("sell_empty"), bg=C_CARD, fg=C_META, anchor="w",
                 justify="left", wraplength=300).pack(fill="x", padx=12, pady=10)
        _sell_scroll(); return
    ready = [it for it in items if it["marketable"] == 1]
    locked = [it for it in items if it["marketable"] == 0]
    if ready:                                          # 取れた事実だけ表示（解除日は公開データに無いので出さない）
        _sell_header(inner, f"🟢 {T('sell_ready_group')} ({len(ready)})", C_PRICE)
        for g in _sell_agg(ready):
            _sell_item_row(inner, g, T("sell_ready"), C_PRICE)
    if locked:
        _sell_header(inner, f"🔒 {T('sell_locked_group')} ({len(locked)})", C_WAIT)
        for g in _sell_agg(locked):
            _sell_item_row(inner, g, "", C_META)
    _sell_scroll()

def show_sell(root):
    _sell_visible[0] = True; _save_settings()      # 開いた状態を保存（再起動後も復元）
    if _sell_win[0] and _sell_win[0].winfo_exists():
        _sell_win[0].deiconify(); _refresh_sell(); return
    win = tk.Toplevel(root)
    win.config(bg=C_CARD); win.attributes("-topmost", True)
    f_btn = tkfont.Font(family="Yu Gothic UI", size=9)
    hdr = _modern_titlebar(win, T("sell_title"), lambda: toggle_sell(root))   # ダーク・角丸・ドラッグ・✕
    round_pill(hdr, "↻ " + T("sell_refresh"), C_ACCENT, "#0c0c0c",
               lambda: (_set_sell_loading(), _sell_refresh_async()), f_btn).pack(side="right", padx=(0, 6))
    win.geometry(_sell_geo[0] or "360x460")      # overrideredirect後に適用
    def _rg(e):
        if e.widget is win and win.winfo_width() > 80:
            _sell_geo[0] = win.geometry()
    win.bind("<Configure>", _rg)
    canvas, inner = _scrolling_body(win)
    _sell_win[0] = win; _sell_inner[0] = (canvas, inner)
    _keep_on_top(win, pause=lambda: bool(_open), respect_toggle=True)   # 『常に前面』設定に従う
    _add_resize_grip(win)                        # 右下グリップでリサイズ
    _refresh_sell()
    _set_sell_loading(); _sell_refresh_async()    # 開いたら最新を取りに行く

def hide_sell(root=None):
    _sell_visible[0] = False
    if _sell_win[0]:
        try:
            if _sell_win[0].winfo_width() > 80: _sell_geo[0] = _sell_win[0].geometry()
            _sell_win[0].withdraw()
        except Exception: pass
    _save_settings()

def toggle_sell(root):
    _sell_visible[0] = not _sell_visible[0]
    if _sell_visible[0]: show_sell(root)
    else: hide_sell(root)
    if _tray_icon[0]:                                  # ×で閉じた時もトレイのチェック表示を同期
        try: _tray_icon[0].update_menu()
        except Exception: pass


# ---- タスクトレイ --------------------------------------------------------
def tray_image():
    try:                                              # 同梱の新アイコンを優先（窓/タスクバーと統一）
        p = os.path.join(RES, "marketlens.png")
        if os.path.exists(p): return Image.open(p).convert("RGBA")
    except Exception: pass
    img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))   # フォールバック（同梱が無い時）
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
    def _toggle_sell(icon, item):
        _sell_visible[0] = not _sell_visible[0]
        PQ.put(("__sell__", None, None))
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
        pystray.MenuItem(lambda item: T("tray_sell"), _toggle_sell,
                         checked=lambda item: _sell_visible[0]),
        pystray.MenuItem(lambda item: T("tray_limit"), limit_menu),
        pystray.MenuItem(lambda item: T("tray_quit"), _quit),
    )
    icon = pystray.Icon("tbh_marketlens", tray_image(), "TBH MarketLens", menu)
    _tray_icon[0] = icon                                  # 出品解除の通知に使う
    icon.run()


# ---- main ----------------------------------------------------------------
_singleton = [None]
def _already_running():
    """既に起動中なら True（名前付きミューテックス）。配布版の多重起動（自動起動＋手動/二度押し）を防ぐ。
    ハンドルはプロセス終了まで保持＝解放しない（保持できなければ判定だけ行う）。"""
    try:
        import ctypes
        k = ctypes.windll.kernel32
        _singleton[0] = k.CreateMutexW(None, False, "TBH_MarketLens_singleton")
        return k.GetLastError() == 183             # ERROR_ALREADY_EXISTS
    except Exception:
        return False

def main():
    if _already_running():                                     # 二重起動はここで静かに終了（トレイ/ホットキー重複防止）
        return
    _load_hist()                                               # 保存済み履歴を復元
    _im = _icon_by_hash()                                       # 既存履歴のアイコンをハッシュから補完
    for _r in _hist:
        if not _r.get("icon") and _r.get("hash"): _r["icon"] = _im.get(_r["hash"], "")
    _load_settings()                                           # 保存済み設定を復元
    _autostart_refresh()                                       # 自動起動が有効なら現在のパスで貼り直す（移動/更新追従）
    _load_sell_state()                                         # 出品待ちの追跡状態（解除時刻/ロック履歴）を復元
    threading.Thread(target=_sell_poller, daemon=True).start() # 在庫を定期確認→解除を通知
    if _lang_mode[0] is None:                                  # 初回はPCの言語を既定に
        _lang_mode[0] = _detect_pc_lang()
    _apply_lang(_lang_mode[0])                                 # _ui_langへ反映
    _telemetry_send("launch")                                  # 匿名の起動テレメトリ（言語確定後＝正しいlangで送る）
    threading.Thread(target=_check_update, daemon=True).start()   # 新版チェック（非同期）
    threading.Thread(target=fetch_rate, daemon=True).start()      # 為替レート（概算フォールバック用）
    root = tk.Tk()
    root.withdraw()
    try:                                                       # 全Toplevel/タスクバーの既定アイコン
        _ico = os.path.join(RES, "marketlens.ico")
        if os.path.exists(_ico): root.iconbitmap(default=_ico)
    except Exception: pass
    threading.Thread(target=ocr_worker, daemon=True).start()    # OCR常駐ワーカー（初期化1回）
    _bind_trigger()                                             # 設定されたキー/ボタンで発動（既定:マウス戻る）
    threading.Thread(target=run_tray, args=(root,), daemon=True).start()
    if not _intro_seen[0]:                                     # 初回起動：使い方を表示
        _intro_seen[0] = True; _save_settings()
        root.after(700, lambda: show_help(root))
    if _ocr_lang_missing():                                    # 表示言語のOCRが無い→原因と直し方を案内
        root.after(1200, lambda: show_ocr_warn(root))
    if _hist_visible[0]:                                       # 前回開いていた履歴ウィンドウを復元
        root.after(300, lambda: show_history(root))
    if _sell_visible[0]:                                       # 前回開いていた出品待ちウィンドウを復元
        root.after(300, lambda: show_sell(root))
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
