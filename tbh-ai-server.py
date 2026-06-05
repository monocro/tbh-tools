#!/usr/bin/env python3
"""
TBH ローカルAIサーバ（このデバイス専用）
------------------------------------------------
公開している tbh-build-simulator.html から呼ばれ、`claude -p` を使って
自然言語の指示からビルド（盛りステータス）を提案する。

- localhost:8765 のみで待ち受け（外部公開しない）
- `claude -p` はこのMacのClaude契約で動く＝API従量課金なし
- 起動していない他デバイス/一般訪問者からは到達しないので、AI機能は出ない

使い方:
    python3 tbh-ai-server.py
そのうえで:
    - このデバイスで http://localhost:8765/ を開く（確実に同一オリジンで動く）
    - もしくは公開ページ(github.io)を開けば、自動でローカルサーバを検出してAIパネルが出る
      （Chrome系。Safariはmixed-content制限で出ない場合あり→localhost直開きで確実）
"""
import json
import subprocess
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PORT = 8765
CLAUDE = next((p for p in [str(Path.home() / ".local/bin/claude"),
                           "/opt/homebrew/bin/claude", "/usr/local/bin/claude"]
               if Path(p).exists()), "claude")
TIMEOUT = 300

PROMPT = """あなたは放置ハクスラ「Task Bar Hero (TBH)」のDPSビルドシミュレーター用のビルドを設計します。

DPS式:
DPS = 攻撃力 × 攻撃速度 × (1 + クリ率 × (クリダメ/100 − 1)) × (1 + ダメージ種別%/100) × バフ(力の祝福)
- クリダメはゲーム表示の「合計倍率%」。例: 242% はクリ時2.42倍。宝石/刻印の「クリダメ+X%」はこの合計%に加算（242→267 など）。
- 係数%（攻撃力係数/攻撃速度係数/クリ率係数/クリダメ係数）は基礎値への乗算。

各部位に積める盛りステータス（stat名: 単位）:
攻撃力=実数, 攻撃力係数=%, 攻撃速度=%, クリ率=%, クリ率係数=%, クリダメ=%, クリダメ係数=%, 物理ダメージ=%, 火炎ダメージ=%, 冷気ダメージ=%, 雷ダメージ=%, カオスダメージ=%

部位インデックス: 0=武器,1=オフハンド,2=頭,3=胴,4=手,5=足,6=首飾り,7=イヤリング,8=指輪,9=腕輪

必ず ./tbh-data.json（装飾/宝石/彫刻/刻印の実数値・色×部位で効果が変わる）と ./tbh-prices.json（Steam中央値）を Grep/Read で参照し、実在するアイテムで裏付けること（巨大なので検索推奨・全文読みしない）。slotStats の数値は、推薦した実アイテムの効果量と整合させる。
特定ステージ攻略向けの指示（例「Act2のHELLで死ぬ」）なら、tbh-data.json の stages[]（{act,no,difficulty,boss,enemies}）と enemies[]（{nameJa,element,atk,hp,...}）を引き、敵の element に対する耐性や防御も考慮して提案・picks に反映する。DB に無い情報は WebSearch で補ってよいが、DB の数値が優先。

現在のビルド状態(JSON):
%%STATE%%

ユーザーの指示:
%%REQUEST%%

出力は説明文を一切付けず、次の形のJSONオブジェクトのみ:
{"base": {"ad":数値, "adc":数値, "as":数値, "asc":数値, "cc":数値, "ccc":数値, "cd":数値, "cdc":数値}, "slotStats": {"0":[{"stat":"攻撃速度","val":5,"cnt":2}]}, "picks":"Markdown文字列", "note":"日本語で短い総括"}
- base は変更したいフィールドだけ含めればよい（省略可）。
- slotStats は現在の部位ごと盛りを丸ごと置き換える。cnt 省略時は1。stat は上記リストの名前のみ使う。装飾枠は装備レアリティで1〜2、加えて彫刻・刻印が現実的な範囲。
- picks は「具体的なおすすめ装飾」を部位別にMarkdownで。各アイテムは 日本語名(英語名)・レアリティ・付与stat+数値・分かれば中央値$ を明記。なぜそれかを一言。uncertain:true のデータは断定せず注記。表(Markdown table)推奨。
"""


def extract_json(text):
    start = text.find("{")
    while start != -1:
        depth = 0
        for i in range(start, len(text)):
            c = text[i]
            if c == "{":
                depth += 1
            elif c == "}":
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(text[start:i + 1])
                    except json.JSONDecodeError:
                        break
        start = text.find("{", start + 1)
    return None


CHAT_PROMPT = """あなたは放置ハクスラ「Task Bar Hero (TBH)」のアイテム/ビルド/攻略に詳しいアシスタントです。
このフォルダの ./tbh-data.json と ./tbh-prices.json（Steam相場）を必要に応じて Grep/Read で参照し、根拠のある回答をしてください（巨大なので全文読みせず検索推奨）。
tbh-data.json の主なキー:
- gems / engravings / inscriptions / equipment / uniqueMods: 装備・装飾の実数値（装飾は「色×部位(武器/防具/アクセ)」で効果が変わる）
- stages[]: {key, act(章1-3), no, level, difficulty(NORMAL/NIGHTMARE/HELL/TORMENT), boss(敵key), enemies[](敵key配列), expectedGold, expectedExp}
- enemies[]: {key, nameJa, nameEn, type, atk, atkSpeed, hp, moveSpeed, element(physical/fire/cold/lightning/chaos), gold, exp}
「このステージで死ぬ、どんな装備？」のような攻略質問では、該当 stage を特定し、その boss/enemies の key を enemies から引いて、敵の element（→対応する耐性を優先）・atk・hp を見て、防御/属性耐性/早期撃破(火力)の観点で具体的に助言する。stage は act・no・difficulty で指定されることが多い。
データに無い最新情報や一般的な攻略は WebSearch/WebFetch で調べてよいが、tbh-data.json に数値があるものは必ずDB優先（ネット情報より実データを信頼）。情報源がネットの場合はその旨を明記。
DPS式: DPS = 攻撃力 × 攻撃速度 × (1 + クリ率 × (クリダメ/100 − 1)) × (1 + ダメージ種別%/100) × バフ。クリダメはゲーム表示の「合計倍率%」。
回答ルール:
- 質問と同じ言語で、簡潔に（前置き不要、結論から）。
- アイテム名は 日本語(英語) 併記。価格に触れるなら中央値 $ を添える。
- 装飾(宝石)は「色×部位(武器/防具/アクセ)」で効果が変わる点に注意。
- uncertain:true など不確実なデータは断定しない。

質問:
%%Q%%
"""


def _run(prompt):
    proc = subprocess.run(
        [CLAUDE, "-p", prompt,
         "--dangerously-skip-permissions",
         "--allowedTools", "Read,Grep,Glob,WebSearch,WebFetch",
         "--output-format", "json"],
        cwd=str(ROOT), capture_output=True, text=True, timeout=TIMEOUT,
    )
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or "claude failed")
    return json.loads(proc.stdout).get("result", "")


def run_claude(body):
    req = (body.get("request") or "").strip() or "DPS最大化のビルドを提案して"
    prompt = PROMPT.replace("%%STATE%%", json.dumps(body, ensure_ascii=False)).replace("%%REQUEST%%", req)
    obj = extract_json(_run(prompt))
    if obj is None:
        raise RuntimeError("claudeの出力からJSONを取り出せませんでした")
    obj.setdefault("note", "")
    obj.setdefault("picks", "")
    return obj


def run_chat(body):
    q = (body.get("question") or "").strip()
    if not q:
        raise RuntimeError("質問が空です")
    return {"answer": _run(CHAT_PROMPT.replace("%%Q%%", q)).strip()}


class Handler(BaseHTTPRequestHandler):
    def _cors(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET,POST,OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Access-Control-Allow-Private-Network", "true")

    def do_OPTIONS(self):
        self.send_response(204)
        self._cors()
        self.end_headers()

    def do_GET(self):
        if self.path.split("?")[0] == "/health":
            return self._json({"ok": True})
        self._serve_static()

    def do_POST(self):
        route = self.path.split("?")[0]
        if route not in ("/optimize", "/chat"):
            self.send_response(404); self._cors(); self.end_headers(); return
        try:
            length = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(length) or b"{}")
            self._json(run_chat(body) if route == "/chat" else run_claude(body))
        except Exception as e:  # noqa: BLE001
            self._json({"error": str(e)}, code=500)

    def _json(self, obj, code=200):
        data = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self._cors()
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _serve_static(self):
        rel = self.path.split("?")[0].lstrip("/") or "tbh-build-simulator.html"
        f = (ROOT / rel).resolve()
        if not str(f).startswith(str(ROOT)) or not f.is_file():
            self.send_response(404); self._cors(); self.end_headers(); return
        ctype = {".html": "text/html; charset=utf-8", ".json": "application/json; charset=utf-8",
                 ".js": "text/javascript", ".css": "text/css"}.get(f.suffix, "application/octet-stream")
        data = f.read_bytes()
        self.send_response(200)
        self._cors()
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *args):  # 静かに
        pass


if __name__ == "__main__":
    print(f"TBH AI server → http://localhost:{PORT}/  (claude: {CLAUDE})")
    print("停止: Ctrl+C")
    ThreadingHTTPServer(("127.0.0.1", PORT), Handler).serve_forever()
