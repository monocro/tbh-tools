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

    prices = {}
    latest = 0
    for it in items:
        hn = it.get("hash_name")
        if not hn: continue
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

    # rebuild equipment roster (武器/防具/アクセ/オフハンド) from market data, keep tbh-data.json in sync
    GEAR2CAT = {**dict.fromkeys(["Sword","Bow","Staff","Scepter","Crossbow","Axe","Hatchet"], "weapon"),
                **dict.fromkeys(["Shield","Arrow","Orb","Tome","Bolt"], "offhand"),
                **dict.fromkeys(["Helmet","Armor","Gloves","Boots"], "armor"),
                **dict.fromkeys(["Amulet","Earing","Ring","Bracer"], "accessory")}
    GEAR_JA = {"Sword":"剣","Bow":"弓","Staff":"杖","Scepter":"セプター","Crossbow":"クロスボウ","Axe":"斧","Hatchet":"手斧",
               "Shield":"盾","Arrow":"矢","Orb":"オーブ","Tome":"本","Bolt":"ボルト","Helmet":"頭","Armor":"胴","Gloves":"手",
               "Boots":"足","Amulet":"首飾り","Earing":"イヤリング","Ring":"指輪","Bracer":"腕輪"}
    equip = []
    for it in items:
        g = it.get("gear")
        if g not in GEAR2CAT: continue
        m = re.search(r"\(([^)]+)\)", it.get("name") or "")
        equip.append({"name": it.get("name_ja") or it["hash_name"], "nameEn": it["hash_name"],
                      "gear": g, "gearJa": GEAR_JA.get(g, g), "cat": GEAR2CAT[g],
                      "lvl": it.get("level"), "rarity": m.group(1).strip() if m else None})
    dj = os.path.join(HERE, "tbh-data.json")
    d = json.load(open(dj, encoding="utf-8"))
    d["equipment"] = equip

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
    for g in d["gems"]: _attach(g, icon_by_hash.get(g.get("nameEn")))
    for e in d["engravings"]: _attach(e, icon_by_hash.get(e.get("nameEn")))
    for s in d["inscriptions"]["scrolls"]: _attach(s, icon_by_hash.get(s.get("nameEn")))
    for e in equip: _attach(e, icon_by_hash.get(e.get("nameEn")))
    if d.get("uniqueMods"):
        for it2 in d["uniqueMods"]["items"]:
            _attach(it2, icon_by_bg.get(it2.get("nameEn")+"|"+GT2.get(it2.get("gradeEn"), it2.get("grade"))))
    d["icons"] = icons
    d.setdefault("_meta", {})["iconCdn"] = "https://community.akamai.steamstatic.com/economy/image/"

    json.dump(d, open(dj, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    print("rebuilt equipment:", len(equip), "| icons:", len(icons))

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
