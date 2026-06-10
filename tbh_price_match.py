"""OCRテキスト -> 価格エントリ(変種まとめ)。閉じた辞書への曖昧スナップ。stdlibのみ。"""
import json, re, unicodedata, os, difflib

_SMALL = str.maketrans("ぁぃぅぇぉっゃゅょゎァィゥェォッャュョヮ",
                       "あいうえおつやゆよわアイウエオツヤユヨワ")
# 漢字↔カナの定番OCR誤読を寄せる（力→カ, 口→ロ 等）
_LOOK = str.maketrans({"力": "カ", "口": "ロ", "工": "エ", "二": "ニ", "八": "ハ",
                       "夕": "タ", "卜": "ト", "0": "o", "1": "l", "|": "l"})

def _h2k(s):   # ひらがな→カタカナ統一
    return "".join(chr(ord(c) + 0x60) if "ぁ" <= c <= "ゖ" else c for c in s)

def norm(s: str) -> str:
    if not s: return ""
    s = unicodedata.normalize("NFKC", s).lower()
    s = _h2k(s)                       # ひら→カタ統一
    s = s.translate(_SMALL)           # 小書き→大書き
    s = unicodedata.normalize("NFD", s).replace("゙", "").replace("゚", "")  # 濁点/半濁点除去
    s = s.translate(_LOOK)            # 漢字/類似字→カナ
    s = re.sub(r"[\s　ー\-ｰ~一'’!?;()\[\]（）【】・,._/:：]+", "", s)   # 記号・長音・空白除去（'も）
    return s

RARITIES = [("Common", "コモン"), ("Uncommon", "アンコモン"), ("Rare", "レア"),
            ("Legendary", "レジェンダリー"), ("Immortal", "イモータル"), ("Arcana", "アルカナ"),
            ("Beyond", "ビヨンド"), ("Celestial", "セレスティアル"), ("Divine", "ディバイン"),
            ("Cosmic", "コズミック")]

# 中国語の等級名（簡体, 繁体）。出典: localization.json Grade_*。ツールチップ行は「{0}级/級」。
# これが無いと中国語では等級が常に読めず、価格付き最高値の変種へ誤フォールバックする。
RARITY_ZH = {"Common": ("普通",), "Uncommon": ("罕见", "罕見"), "Rare": ("稀有",),
             "Legendary": ("传奇", "傳奇"), "Immortal": ("不朽",), "Arcana": ("至宝", "至寶"),
             "Beyond": ("超凡",), "Celestial": ("天界",), "Divine": ("神圣", "神聖"),
             "Cosmic": ("宇宙",)}

def extract_rarity(text):
    """等級行から等級(en)を抽出。最長一致のカバー率で判定（Immortal→lmmorta等の誤読も拾う）。
    中国語は2字語なので実質完全一致（1字誤読は枠色の救済に回る）。"""
    nt = norm(text)
    if not nt: return None
    best = (None, 0.0)
    for en, ja in RARITIES:
        for w in (en, ja) + RARITY_ZH.get(en, ()):
            nw = norm(w)
            if not nw: continue
            mm = difflib.SequenceMatcher(None, nw, nt).find_longest_match(0, len(nw), 0, len(nt))
            cov = mm.size / len(nw)
            if cov > best[1]: best = (en, cov)
    return best[0] if best[1] >= 0.7 else None


class Matcher:
    RMAP = {en: ja for en, ja in RARITIES}

    def __init__(self, path):
        d = json.load(open(path, encoding="utf-8"))
        self.entries = d["entries"]; self.index = d["index"]
        # 索引キーを長い順に（最長一致＝最も具体的＝名前＋等級 を優先）
        self.keys = sorted(self.index.keys(), key=len, reverse=True)
        self.marketUpdated = d.get("marketUpdated")

    def _collect(self, key, score):
        # その索引キーのエントリを返す。価格がある方を優先（A/B等）。
        out = [dict(self.entries[i]) for i in self.index[key]]
        out.sort(key=lambda e: (e.get("sell") is None, -(e.get("sell") or 0)))
        for e in out:
            e["score"] = round(score, 3)
        return out

    def match(self, ocr_text: str, min_score=0.7):
        # OCRの各行＋全体を候補に。部分一致(カバー率)と曖昧一致を総合スコアで比較し最良を採る。
        # これで「サンダーストーン」が少し崩れても短い「ストーン」に化けない（長い正式名を優先）。
        cands = []
        parts = [p for p in re.split(r"[\r\n]+", ocr_text) if norm(p)]
        # 各行＋隣接行ペア（名前と等級が別行でも結合して判定）＋全体
        probes = parts + [parts[i] + parts[i + 1] for i in range(len(parts) - 1)] + [ocr_text]
        for probe in probes:
            q = norm(probe)
            if len(q) < 2: continue
            if q in self.index:
                cands.append((1.0, q, len(q))); continue
            for k in self.keys:
                if len(k) >= 2 and k in q:            # 既知名が読取に含まれる
                    cov = len(k) / max(len(k), len(q))   # 読取のうち名前が占める割合
                    cands.append((0.6 + 0.4 * cov, k, len(k)))
            for k in difflib.get_close_matches(q, self.keys, n=3, cutoff=min_score):
                cands.append((difflib.SequenceMatcher(None, q, k).ratio(), k, len(k)))
        if not cands: return []
        score, key, _ = max(cands, key=lambda c: (round(c[0], 3), c[2]))   # 同点なら長い名前
        if score < min_score: return []
        return self._collect(key, score)

    def candidates(self, ocr_text, n=8, min_score=0.45):
        """OCR文字に近い候補を上位n件返す（マウスで選び直す用）。キー重複は最良スコアでまとめる。"""
        parts = [p for p in re.split(r"[\r\n]+", ocr_text) if norm(p)]
        probes = parts + [parts[i] + parts[i + 1] for i in range(len(parts) - 1)] + [ocr_text]
        best = {}   # index -> score
        for probe in probes:
            q = norm(probe)
            if len(q) < 2: continue
            for k in self.keys:
                if len(k) >= 2 and k in q:
                    sc = 0.6 + 0.4 * (len(k) / max(len(k), len(q)))
                    for i in self.index[k]: best[i] = max(best.get(i, 0), sc)
            for k in difflib.get_close_matches(q, self.keys, n=5, cutoff=min_score):
                sc = difflib.SequenceMatcher(None, q, k).ratio()
                for i in self.index[k]: best[i] = max(best.get(i, 0), sc)
        ranked = sorted(best.items(), key=lambda kv: -kv[1])
        out, seen = [], set()
        for i, sc in ranked:
            e = self.entries[i]
            dk = (e.get("ja"), e.get("rarity_ja"))
            if dk in seen: continue
            seen.add(dk)
            ee = dict(e); ee["score"] = round(sc, 3); out.append(ee)
            if len(out) >= n: break
        return out

    def match_item(self, name_text, rank_text=""):
        """名前で照合してアイテムを特定し、等級行から等級を補って正しい等級のエントリを返す。"""
        if len(norm(name_text)) < 2:
            return []   # 名前OCRが空＝照合しない。等級語だけだと最短の「名前+等級」索引キーに
                        # 化ける（実機で確定: 級[レジェンダリー]だけでPearl s=0.857の誤ポップ）
        r = self.match(name_text)
        if not r:
            r = self.match((name_text + " " + rank_text).strip())
            if not r: return []
        base = r[0]
        rar = extract_rarity(rank_text)
        if rar:                                  # 名前×抽出した等級 の具体エントリを引く
            for key in (norm((base.get("en") or "") + rar),
                        norm((base.get("ja") or "") + self.RMAP.get(rar, ""))):
                if key and key in self.index:
                    return self._collect(key, base.get("score", 1.0))
        return r

if __name__ == "__main__":
    ROOT = os.path.dirname(os.path.abspath(__file__))
    m = Matcher(os.path.join(ROOT, "tbh-price-lookup.json"))
    tests = ["War Bow (Legendary) A", "ウォーボウ（レジェンダリー）", "Wood",
             "癒しの薬草", "War  Bow  (Legendary)  Lv.20", "lron lngot",
             "スパイダ-シルク", "ゴブリンの皮   x12"]
    for t in tests:
        r = m.match(t)
        print(f"\nIN : {t!r}")
        for e in r:
            v = f" [{e['variant']}]" if e["variant"] else ""
            print(f"  -> {e['base_en']}{v} / {e['ja']}  売値={e['sell']} 中央={e['median']}  ({e['type']})  s={e['score']}")
        if not r: print("  (no match)")
