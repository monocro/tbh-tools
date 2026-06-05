#!/usr/bin/env python3
"""TBH 価格取得スクリプト
tbh-market.com の公開API(/api/items)からSteam相場を取得し、tbh-prices.json に保存、
さらに tbh-gem-search.html / tbh-build-simulator.html にデータと価格を埋め込み直す。

価格出典: tbh-market.com (Steam Community Market 相場を集計しているコミュニティサイト)。
礼儀として低頻度(1日1回程度)の利用にとどめ、出典を表示すること。sell_price は USD セント。
使い方:  python3 tbh-fetch-prices.py
"""
import urllib.request, json, re, time, math, os
from datetime import datetime, timezone

API = "https://tbh-market.com/api/items?page={p}&pageSize=200"
HERE = os.path.dirname(os.path.abspath(__file__))

def fetch_page(p):
    req = urllib.request.Request(API.format(p=p), headers={"User-Agent": "Mozilla/5.0 (personal price cache)"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)

def main():
    first = fetch_page(1)
    total = first["total"]; size = first.get("pageSize") or len(first["items"])
    items = list(first["items"])
    pages = math.ceil(total / size)
    print(f"total={total} pageSize={size} pages={pages}")
    for p in range(2, pages + 1):
        items += fetch_page(p)["items"]
        time.sleep(1.0)  # be polite
    print("collected", len(items))

    # アップデートでSteam市場の負荷軽減のため (1)レジェンダリー未満の装備 (2)「B」ロール全種 は削除済み。
    # tbh-market.com の集計は追従が遅く古い(削除済み)リスティングを残すため、ここで除外する。
    SUBLEG = ("(Common)", "(Uncommon)", "(Rare)")
    def is_dead_listing(hn):
        if any(s in hn for s in SUBLEG): return True          # レジェンダリー未満 = 削除済み
        if re.search(r" \([^)]+\) B$", hn): return True       # 「B」ロール = 削除済み(Aに一本化)
        return False

    prices = {}
    latest = 0
    for it in items:
        hn = it.get("hash_name")
        if not hn: continue
        if is_dead_listing(hn): continue
        prices[hn] = {
            "sell": it.get("sell_price"), "median": it.get("median_price"),
            "listings": it.get("sell_listings"), "volume": it.get("volume"),
            "name_ja": it.get("name_ja"), "type": it.get("type"),
        }
        if it.get("updated_at"): latest = max(latest, it["updated_at"])

    out = {
        "source": "tbh-market.com (Steam Community Market 相場集計)",
        "sourceUrl": "https://tbh-market.com/",
        "currency": "USD", "unit": "cents",
        "marketUpdated": datetime.fromtimestamp(latest, timezone.utc).isoformat() if latest else None,
        "fetchedAt": datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M"),
        "prices": prices,
    }
    pj = os.path.join(HERE, "tbh-prices.json")
    json.dump(out, open(pj, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print("wrote", pj, "items:", len(prices))

    # NOTE: 装備ロスター(equipment)とその stats は probonk由来（tbh-fetch-gear.py で生成）。
    # ここでは上書きしない。価格はツールが PRICES から base|grade で都度計算する。
    dj = os.path.join(HERE, "tbh-data.json")
    d = json.load(open(dj, encoding="utf-8"))

    # アイテム画像: Steam公式アイコン(icon_url)を「アイコンID(sha)→url」表に集約し、各itemにiconID付与（ダウンロード不要・CDN参照）
    import hashlib
    icon_by_hash = {it["hash_name"]: it["icon_url"] for it in items if it.get("icon_url")}
    GT2 = {"COMMON":"Common","UNCOMMON":"Uncommon","RARE":"Rare","LEGENDARY":"Legendary","IMMORTAL":"Immortal","ARCANA":"Arcana","CELESTIAL":"Celestial","COSMIC":"Cosmic","DIVINE":"Divine","BEYOND":"Beyond"}
    icon_by_bg = {}
    for it in items:
        mm = re.match(r"^(.*) \(([^)]+)\)", it["hash_name"])
        if mm and it.get("icon_url"): icon_by_bg.setdefault(mm.group(1)+"|"+mm.group(2), it["icon_url"])
    icons = {}
    def _sid(u): return hashlib.sha1(u.encode()).hexdigest()[:16]
    def _attach(x, url):
        if not url: return
        s = _sid(url); x["icon"] = s; icons[s] = url
    def _bg(x): return icon_by_bg.get((x.get("nameEn") or "")+"|"+GT2.get(x.get("gradeEn"), x.get("rarity") or x.get("grade")))
    for g in d["gems"]: _attach(g, icon_by_hash.get(g.get("nameEn")))
    for e in d["engravings"]: _attach(e, icon_by_hash.get(e.get("nameEn")))
    for s in d["inscriptions"]["scrolls"]: _attach(s, icon_by_hash.get(s.get("nameEn")))
    for e in d.get("equipment", []): _attach(e, _bg(e))            # 装備は base|grade でアイコン解決
    if d.get("uniqueMods"):
        for it2 in d["uniqueMods"]["items"]: _attach(it2, _bg(it2))
    d["icons"] = icons
    d.setdefault("_meta", {})["iconCdn"] = "https://community.akamai.steamstatic.com/economy/image/"

    json.dump(d, open(dj, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    print("equipment(kept):", len(d.get("equipment", [])), "| icons:", len(icons))

    # inject DATA + PRICES into HTML tools
    dcompact = json.dumps(d, ensure_ascii=False, separators=(",", ":"))
    pcompact = json.dumps(out, ensure_ascii=False, separators=(",", ":"))
    for fn in ("tbh-gem-search.html", "tbh-build-simulator.html"):
        fp = os.path.join(HERE, fn)
        if not os.path.exists(fp): continue
        h = open(fp, encoding="utf-8").read()
        h = re.sub(r"/\*DATA_START\*/.*?/\*DATA_END\*/", "/*DATA_START*/" + dcompact + "/*DATA_END*/", h, flags=re.S)
        if "/*PRICES_START*/" in h:
            h = re.sub(r"/\*PRICES_START\*/.*?/\*PRICES_END\*/", "/*PRICES_START*/" + pcompact + "/*PRICES_END*/", h, flags=re.S)
        open(fp, "w", encoding="utf-8").write(h)
        print("injected ->", fn)

if __name__ == "__main__":
    main()
