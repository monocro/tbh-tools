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
TIMEOUT = 180

PROMPT = """あなたは放置ハクスラ「Task Bar Hero (TBH)」のDPSビルドシミュレーター用のビルドを設計します。

DPS式:
DPS = 攻撃力 × 攻撃速度 × (1 + クリ率 × (クリダメ/100 − 1)) × (1 + ダメージ種別%/100) × バフ(力の祝福)
- クリダメはゲーム表示の「合計倍率%」。例: 242% はクリ時2.42倍。宝石/刻印の「クリダメ+X%」はこの合計%に加算（242→267 など）。
- 係数%（攻撃力係数/攻撃速度係数/クリ率係数/クリダメ係数）は基礎値への乗算。

各部位に積める盛りステータス（stat名: 単位）:
攻撃力=実数, 攻撃力係数=%, 攻撃速度=%, クリ率=%, クリ率係数=%, クリダメ=%, クリダメ係数=%, 物理ダメージ=%, 火炎ダメージ=%, 冷気ダメージ=%, 雷ダメージ=%, カオスダメージ=%

部位インデックス: 0=武器,1=オフハンド,2=頭,3=胴,4=手,5=足,6=首飾り,7=イヤリング,8=指輪,9=腕輪

必要なら ./tbh-data.json（装飾/宝石/彫刻/刻印/装備の実数値）を読んで、現実的な数値で裏付けてよい。

現在のビルド状態(JSON):
%%STATE%%

ユーザーの指示:
%%REQUEST%%

出力は説明文を一切付けず、次の形のJSONオブジェクトのみ:
{"base": {"ad":数値, "adc":数値, "as":数値, "asc":数値, "cc":数値, "ccc":数値, "cd":数値, "cdc":数値}, "slotStats": {"0":[{"stat":"攻撃速度","val":5,"cnt":2}]}, "note":"日本語で短い根拠"}
- base は変更したいフィールドだけ含めればよい（省略可）。
- slotStats は現在の部位ごと盛りを丸ごと置き換える。cnt 省略時は1。stat は上記リストの名前のみ使う。
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


def run_claude(body):
    req = (body.get("request") or "").strip() or "DPS最大化のビルドを提案して"
    prompt = PROMPT.replace("%%STATE%%", json.dumps(body, ensure_ascii=False)).replace("%%REQUEST%%", req)
    proc = subprocess.run(
        [CLAUDE, "-p", prompt,
         "--dangerously-skip-permissions",
         "--allowedTools", "Read,Grep,Glob",
         "--output-format", "json"],
        cwd=str(ROOT), capture_output=True, text=True, timeout=TIMEOUT,
    )
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or "claude failed")
    env = json.loads(proc.stdout)
    obj = extract_json(env.get("result", ""))
    if obj is None:
        raise RuntimeError("claudeの出力からJSONを取り出せませんでした")
    obj.setdefault("note", "")
    return obj


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
        if self.path.split("?")[0] != "/optimize":
            self.send_response(404); self._cors(); self.end_headers(); return
        try:
            length = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(length) or b"{}")
            self._json(run_claude(body))
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
