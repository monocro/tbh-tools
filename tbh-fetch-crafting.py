#!/usr/bin/env python3
"""TBH クラフト(キューブ)レシピDB生成スクリプト（probonk.com の /cube 実機データから）

equipment データは完成品ステータスのみで製作レシピを持たないため、本スクリプトで補完する。
キューブ system は3つ:
  - crafting   … 装備クラフト(素材→そのtier相当レベルのランダム装備)。56件 = 8 tier × 7 装備種。
                  「Lv80オフハンドに何の素材が何個」を断定できるのはこの配列。
  - synthesis  … 同グレードのギア/素材を materialAmount 個合成して上位を狙う(グレード昇格)。533件。
  - extraction … 装備を分解して素材(装飾/彫刻/刻印)を抽出(gold cost)。90件。
tbh-data.json に crafting{recipes, synthesis, extraction, tiers} を作る。
使い方: python3 tbh-fetch-crafting.py
"""
import json, os, urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
BASE = "https://probonk.com/tbh-task-bar-hero/"
# crafting.type -> (日本語ラベル, equipment側 category)。category は tbh-data.json の categories と整合
TYPE_JA = {
    "MainWeapon": ("メイン武器", "weapon"),
    "SubWeapon": ("オフハンド", "offhand"),
    "Helmet": ("頭", "armor"),
    "Armor": ("胴", "armor"),
    "Gloves": ("手", "armor"),
    "Boots": ("足", "armor"),
    "Accessory": ("アクセサリ", "accessory"),
}


def fetch(slug):
    req = urllib.request.Request(BASE + slug, headers={"User-Agent": "Mozilla/5.0"})
    return urllib.request.urlopen(req, timeout=40).read().decode("utf-8", "replace")


def unescape(s):
    return s.replace('\\"', '"').replace("\\\\", "\\")


def extract_array(h, key):
    """エスケープ済みHTMLから "<key>":[ ... ] の配列を取り出して json.loads する。"""
    mark = '\\"%s\\":[' % key
    i = h.find(mark)
    if i < 0:
        raise RuntimeError("marker not found: " + key)
    start = i + len(mark) - 1
    depth, j, instr, esc = 0, start, False, False
    while j < len(h):
        c = h[j]
        if esc:
            esc = False
        elif c == "\\":
            esc = True
        elif instr:
            if c == '"':
                instr = False
        elif c == '"':
            instr = True
        elif c == "[":
            depth += 1
        elif c == "]":
            depth -= 1
            if depth == 0:
                return json.loads(unescape(h[start:j + 1]))
        j += 1
    raise RuntimeError("array not closed: " + key)


def main():
    h = fetch("cube")
    raw_craft = extract_array(h, "crafting")
    raw_syn = extract_array(h, "synthesis")
    raw_ext = extract_array(h, "extraction")
    raw_grades = extract_array(h, "gradesData")
    raw_levels = extract_array(h, "cubeLevels")

    # グレード別の基礎キューブexp/アルケミーgold（cube に1個投入したときの値）
    gradeExp = {g["GRADE"]: {"cubeExp": g.get("BaseCubeExp"), "alchemyGold": g.get("BaseAlchemyGold")} for g in raw_grades}
    # キューブレベル別の必要累計exp
    cubeLevels = [{"level": x["level"], "exp": x["exp"]} for x in raw_levels]

    def craft_exp(mats):
        # クラフト1回のキューブexp = 投入素材のグレード基礎exp × 個数 の合計
        return sum((gradeExp.get(m["grade"], {}).get("cubeExp") or 0) * (m.get("count") or 1) for m in mats)

    recipes = []
    for c in raw_craft:
        r = c.get("result", {})
        ja, cat = TYPE_JA.get(c["type"], (None, None))
        mats = []
        for m in c.get("materials", []):
            nm = m.get("name", {})
            mats.append({
                "id": m.get("id"),
                "nameEn": nm.get("en-US"),
                "nameJa": nm.get("ja-JP"),
                "grade": m.get("grade"),
                "count": m.get("count"),   # 実データ上は全て1。多段の保険として保持
                "slug": m.get("slug"),
            })
        recipes.append({
            "key": c["key"],
            "type": c["type"],
            "typeJa": ja,
            "category": cat,
            "tier": c["tier"],
            "levelMin": r.get("levelMin"),
            "levelMax": r.get("levelMax"),
            "materials": mats,                         # 必要素材(種類・個数)。グレード/IDも保持
            "craftExp": craft_exp(mats),               # クラフト1回で得るキューブexp(投入素材の基礎exp合計)
            "gradeOdds": r.get("gradeOdds", []),       # 完成品グレードの確率(%)
            "resultDistinct": r.get("distinct"),       # 産出されうる装備の種類数
            # result.itemsByGrade(産出装備の数値ID)は equipment レコードに id が無く紐付かないため非収録
        })

    # 実機補正: probonk は tier5 を Lv40-40 と返すが、tier4(30-40)とtier6(50-65)の間で
    # Lv41-49 が欠落する。実機ではtier5は Lv40~50 帯なので levelMax を 50 に補正（実機の値を優先）。
    LEVELMAX_FIX = {5: 50}
    for rc in recipes:
        if rc["tier"] in LEVELMAX_FIX:
            rc["levelMax"] = LEVELMAX_FIX[rc["tier"]]

    # tier -> level範囲(全装備種で共通。検証して1つに畳む)
    tier_lv = {}
    for rc in recipes:
        tier_lv.setdefault(rc["tier"], (rc["levelMin"], rc["levelMax"]))
    tiers = [{"tier": t, "levelMin": lv[0], "levelMax": lv[1]} for t, lv in sorted(tier_lv.items())]

    synthesis = [{
        "key": s["key"], "tier": s["tier"], "type": s["type"], "grade": s["grade"],
        "materialAmount": s["materialAmount"], "minMaterialTier": s["minMaterialTier"],
        "avgLevel": s["avgLevel"], "resultLevel": s["resultLevel"],
    } for s in raw_syn]

    extraction = [{
        "key": e["key"], "gearGroup": e["gearGroup"], "materialType": e["materialType"],
        "tier": e["tier"], "cost": e["cost"],
    } for e in raw_ext]

    path = os.path.join(HERE, "tbh-data.json")
    data = json.load(open(path, encoding="utf-8"))

    # 既存データの素材アイコン(Steam由来、tbh-fetch-prices.py が付与)を nameEn で引き継ぐ。
    # これをしないと再生成のたびに素材アイコンが消える（prices側の再実行が必要になる）。
    old_icon = {m.get("nameEn"): m.get("icon")
                for r in data.get("crafting", {}).get("recipes", [])
                for m in r.get("materials", []) if m.get("icon")}
    for r in recipes:
        for m in r["materials"]:
            ic = old_icon.get(m["nameEn"])
            if ic:
                m["icon"] = ic

    data["crafting"] = {
        "recipes": recipes,
        "tiers": tiers,
        "synthesis": synthesis,
        "extraction": extraction,
        "gradeExp": gradeExp,        # グレード→{cubeExp, alchemyGold}
        "cubeLevels": cubeLevels,    # キューブlevel→必要累計exp
    }
    data.setdefault("_meta", {})["craftingNote"] = (
        "crafting は probonk /cube(実機データマイン)由来。equipment(完成品ステータスのみ)を補完する製作レシピ。"
        "recipes(56=8tier×7装備種): type(MainWeapon/SubWeapon=オフハンド/Helmet/Armor/Gloves/Boots/Accessory), "
        "tier(1-8), levelMin/levelMax=産出装備のレベル範囲, materials=必要素材[{id,nameEn,nameJa,grade,count,slug}] "
        "(実データ上 count は全て1, 素材種類は1-3), gradeOdds=完成品グレード確率(%), resultDistinct=産出装備の種類数。"
        "tier↔Lv: 1=1-10,2=10-20,3=20-30,4=30-40,5=40,6=50-65,7=65-80,8=80(Lv41-49/66-79境界は重複/欠落あり=実データ通り)。"
        "クラフトは素材を入れると当該tierのレベル帯のランダム装備が gradeOdds の確率で出る方式(特定装備の指名製作ではない)。"
        "synthesis=同グレード materialAmount 個を合成して上位グレードを狙う系(533件)。"
        "extraction=装備を分解して素材(DECORATION装飾/ENGRAVING彫刻/INSCRIPTION刻印)を gold cost で抽出(90件)。"
    )
    json.dump(data, open(path, "w", encoding="utf-8"), ensure_ascii=False, indent=2)

    print("crafting recipes:", len(recipes), "| synthesis:", len(synthesis), "| extraction:", len(extraction))
    for t in tiers:
        print("  tier %d  Lv%d-%d" % (t["tier"], t["levelMin"], t["levelMax"]))


if __name__ == "__main__":
    main()
