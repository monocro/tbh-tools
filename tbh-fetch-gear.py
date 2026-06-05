#!/usr/bin/env python3
"""TBH 装備データ生成スクリプト（probonk.com のゲーム内部データから）
全装備(武器/防具/アクセ/オフハンド)の名前・グレード・Lv・固有ステータスを抽出し、
tbh-data.json の equipment を作り直す。市場(tbh-market)だけだと未掲載装備が抜けるためこちらが正。

ステータスのスケーリング（実機表示に合わせる。Steam/wiki実値で検証済み）:
  - %系(攻撃速度/クリ率/クリダメ/CD短縮/詠唱/効果範囲/回避/ブロック/軽減/吸命/各耐性 等)
    および MODTYPE が ADDITIVE/MULTIPLICATIVE のもの → 生値 ÷10、整数%表示
  - 毎秒HP回復(HpRegenPerSec) → 生値 ÷100（例 149→1.5）
  - 実数系(防御/HP/移動速度/攻撃実数/カウント系) → そのまま
  検証: Ethereal Amulet(Arcana) 攻撃速度197→20% / クリ率388→39% / 通常攻撃必要数1→+1,
        Knight Boots(Arcana) base87→防御87 / 移動41 / HP34 / HP回復149→1.5。
注意: probonk の gear キーは EARING(R一つ)。価格/アイコン/市場リンクは base名×グレードで market に解決。
使い方: python3 tbh-fetch-gear.py   （その後 python3 tbh-fetch-prices.py で価格・アイコン同期）
"""
import re, json, hashlib, os, urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
PAGES = ["weapons", "armor-shields", "accessories"]
BASE = "https://probonk.com/tbh-task-bar-hero/"

GT = {"COMMON":"Common","UNCOMMON":"Uncommon","RARE":"Rare","LEGENDARY":"Legendary","IMMORTAL":"Immortal","ARCANA":"Arcana","CELESTIAL":"Celestial","COSMIC":"Cosmic","DIVINE":"Divine","BEYOND":"Beyond"}
GJA = {"SWORD":"剣","BOW":"弓","STAFF":"杖","SCEPTER":"セプター","CROSSBOW":"クロスボウ","AXE":"斧","HATCHET":"手斧","SHIELD":"盾","ARROW":"矢","ORB":"オーブ","TOME":"本","BOLT":"ボルト","HELMET":"頭","ARMOR":"胴","GLOVES":"手","BOOTS":"足","AMULET":"首飾り","EARING":"イヤリング","RING":"指輪","BRACER":"腕輪"}
CAT = {**dict.fromkeys(["SWORD","BOW","STAFF","SCEPTER","CROSSBOW","AXE","HATCHET"],"weapon"), **dict.fromkeys(["SHIELD","ARROW","ORB","TOME","BOLT"],"offhand"), **dict.fromkeys(["HELMET","ARMOR","GLOVES","BOOTS"],"armor"), **dict.fromkeys(["AMULET","EARING","RING","BRACER"],"accessory")}
SLAB = {"AttackDamage":"攻撃力","AttackSpeed":"攻撃速度","CriticalChance":"クリ率係数","CriticalDamage":"クリダメ","MaxHp":"最大HP","Armor":"防御力","MovementSpeed":"移動速度","CastSpeed":"詠唱速度","CooldownReduction":"クールダウン短縮","AreaOfEffect":"効果範囲","HpRegenPerSec":"毎秒HP回復","AddHpPerHit":"命中毎HP回復","AddHpPerKill":"撃破毎HP回復","HpLeech":"ライフスティール","DodgeChance":"回避率","BlockChance":"ブロック率","DamageReduction":"ダメージ軽減","DamageAbsorption":"ダメージ吸収","AllElementalResistance":"全属性耐性","Multistrike":"マルチストライク","ProjectileCount":"投射物数","BaseAttackCountReduction":"通常攻撃必要数減少","SkillRangeExpansion":"スキル範囲拡大","SkillDurationIncrease":"スキル持続増加","SkillHealIncrease":"スキル回復増加","IncreaseProjectileDamage":"投射ダメージ増加","IncreaseExpAmount":"EXP増加量","AddAllSkillLevel":"全スキルLv+","FireResistance":"火炎耐性","ColdResistance":"冷気耐性","LightningResistance":"雷耐性","ChaosResistance":"カオス耐性","PhysicalDamage":"物理ダメージ","FireDamage":"火炎ダメージ","ColdDamage":"冷気ダメージ","LightningDamage":"雷ダメージ"}
PERCENT = {"AttackSpeed","CriticalChance","CriticalDamage","CooldownReduction","CastSpeed","AreaOfEffect","DodgeChance","BlockChance","DamageReduction","HpLeech","AllElementalResistance","FireResistance","ColdResistance","LightningResistance","ChaosResistance","IncreaseProjectileDamage","SkillRangeExpansion","SkillDurationIncrease","SkillHealIncrease","IncreaseExpAmount"}

meta_re = re.compile(r'\{\\"id\\":(\d+),\\"name\\":\{(.*?)\},\\"grade\\":\\"([A-Z]+)\\",\\"type\\":\\"GEAR\\",\\"gear\\":\\"([A-Z]+)\\",\\"level\\":(\d+)')
en_re = re.compile(r'\\"en-US\\":\\"(.*?)\\"'); ja_re = re.compile(r'\\"ja-JP\\":\\"(.*?)\\"')
blk_re = re.compile(r'\\"GearKey\\":(\d+),(.*?)\\"UniqueModKey\\"')
b1_re = re.compile(r'BaseStat1_Value\\":([\-\d.]+)')

def inh(block, n):
    st = re.search(r'InherentStat%d_STATTYPE\\":\\"(\w+)\\"' % n, block)
    if not st or st.group(1) == "NONE": return None
    mt = re.search(r'InherentStat%d_MODTYPE\\":\\"(\w+)\\"' % n, block)
    vl = re.search(r'InherentStat%d_Value\\":([\-\d.]+)' % n, block)
    return {"stat": st.group(1), "mod": mt.group(1) if mt else "FLAT", "val": float(vl.group(1)) if vl else 0}

def scale(stat, mod, val):
    if stat == "HpRegenPerSec": return round(val/100, 1), ""
    if stat in PERCENT or mod in ("ADDITIVE", "MULTIPLICATIVE"): return int(round(val/10)), "%"
    v = round(val, 2); return (int(v) if float(v).is_integer() else v), ""

def num(v): return int(v) if float(v).is_integer() else round(v, 2)

def main():
    META, STATS = {}, {}
    for slug in PAGES:
        req = urllib.request.Request(BASE + slug, headers={"User-Agent": "Mozilla/5.0"})
        h = urllib.request.urlopen(req, timeout=40).read().decode("utf-8", "replace")
        for m in meta_re.finditer(h):
            i = m.group(1); nm = m.group(2); en = en_re.search(nm); ja = ja_re.search(nm)
            META.setdefault(i, {"nameEn": en.group(1) if en else None, "nameJa": ja.group(1) if ja else None, "grade": m.group(3), "gear": m.group(4), "level": int(m.group(5))})
        for m in blk_re.finditer(h):
            i = m.group(1); block = m.group(2)
            if i in STATS: continue
            b1 = b1_re.search(block)
            STATS[i] = {"base": float(b1.group(1)) if b1 else 0, "inherent": [z for z in (inh(block,1),inh(block,2),inh(block,3)) if z]}
    print("meta:", len(META), "stats:", len(STATS))

    # market icons by base|grade
    mkt_path = "/tmp/tbhm_full.json"
    icon_by_bg = {}
    if os.path.exists(mkt_path):
        for it in json.load(open(mkt_path, encoding="utf-8")):
            mm = re.match(r"^(.*) \(([^)]+)\)", it.get("hash_name",""));
            if mm and it.get("icon_url"): icon_by_bg.setdefault(mm.group(1)+"|"+mm.group(2), it["icon_url"])

    dj = os.path.join(HERE, "tbh-data.json"); d = json.load(open(dj, encoding="utf-8")); icons = d.get("icons", {})
    def sid(u): return hashlib.sha1(u.encode()).hexdigest()[:16]
    seen, equip = {}, []
    for k, me in META.items():
        en = me.get("nameEn")
        if not en: continue
        gradeT = GT.get(me["grade"], me["grade"]); gear = me["gear"]; cat = CAT.get(gear, "weapon")
        if gradeT in ("Common", "Uncommon", "Rare"): continue  # レジェンダリー未満はアップデートでSteam市場から削除済み
        key = (en, gradeT, gear, me["level"]); s = STATS.get(k, {}); ih = s.get("inherent", [])
        if key in seen and not (ih and not seen[key]["stats"]): continue
        stl = []; b0 = s.get("base") or 0
        if b0 and cat != "accessory":
            stl.append({"ja": "防御力" if cat in ("armor","offhand") else "攻撃力", "en": "Armor" if cat in ("armor","offhand") else "AttackDamage", "val": num(b0), "unit": "", "base": True})
        for x in ih:
            sv, unit = scale(x["stat"], x["mod"], x["val"]); stl.append({"ja": SLAB.get(x["stat"], x["stat"]), "en": x["stat"], "val": sv, "unit": unit})
        url = icon_by_bg.get(en+"|"+gradeT); ic = sid(url) if url else None
        if url: icons[ic] = url
        ent = {"name": me.get("nameJa") or en, "nameEn": en, "gear": gear, "gearJa": GJA.get(gear, gear), "cat": cat, "lvl": me["level"], "rarity": gradeT, "stats": stl, "icon": ic}
        seen[key] = ent; equip.append(ent)
    d["equipment"] = equip; d["icons"] = icons
    json.dump(d, open(dj, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    print("equipment written:", len(equip))

if __name__ == "__main__":
    main()
