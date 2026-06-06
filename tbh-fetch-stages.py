#!/usr/bin/env python3
"""TBH 敵・ステージDB生成スクリプト（probonk.com のゲーム内部データから）
全61体の敵(攻撃力/攻撃速度/HP/移動速度/報酬)と、全ステージ(act/Lv/難易度/敵構成/ボス)を抽出し、
tbh-data.json に enemies / stages を作り直す。属性(element)はスキルの DamageType から導出可能な分のみ
付与し、不明分は null（推測しない＝メモリ tbh-research-before-build 方針）。

データ源: probonk.com の Next.js RSC ペイロード（実機データマイン。equipment と同じ正の情報源）
  - /stages  … 敵オブジェクト(基礎ステータス + 各敵の stages[] 出現マッピング)
  - /skills  … SkillKey → DamageType（属性）
数値はゲーム内部の生値。敵の atk/hp は基準値(act1)で、実際の各ステージ値はそのレベルにスケールする。
使い方: python3 tbh-fetch-stages.py
"""
import re, json, os, urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
BASE = "https://probonk.com/tbh-task-bar-hero/"
ELEM = {"Physical": "physical", "Fire": "fire", "Cold": "cold", "Lightning": "lightning", "Chaos": "chaos"}
# 難易度: NORMAL以外は属性耐性デバフを受ける(ペナルティ昇順)。値はprobonk/buffs由来、難易度名の対応は構造から確定
# (tier3段=NORMAL以外の難易度3つ、ペナルティ単調増加、レベル範囲一致)。
DIFF_BY_PENALTY = {0: "NORMAL", 20: "NIGHTMARE", 40: "HELL", 60: "TORMENT"}
RES_STAT = {"FireResistance": "fire", "ColdResistance": "cold", "LightningResistance": "lightning", "ChaosResistance": "chaos"}


def fetch(slug):
    req = urllib.request.Request(BASE + slug, headers={"User-Agent": "Mozilla/5.0"})
    return urllib.request.urlopen(req, timeout=40).read().decode("utf-8", "replace")


def fetch_city(slug):
    # tbh.city はステージのクリア期待EXP/Goldを持つ（probonkに無い）
    req = urllib.request.Request("https://tbh.city/" + slug, headers={"User-Agent": "Mozilla/5.0"})
    return urllib.request.urlopen(req, timeout=40).read().decode("utf-8", "replace").replace('\\"', '"')


def unescape(s):
    # RSC ペイロードは \" → " , \\ → \ の二重エスケープ
    return s.replace('\\"', '"').replace("\\\\", "\\")


def extract_array(h, key):
    """エスケープ済みHTMLから "<key>":[ ... ] の配列を取り出して json.loads する。"""
    mark = '\\"%s\\":[' % key
    i = h.find(mark)
    if i < 0:
        raise RuntimeError("marker not found: " + key)
    start = i + len(mark) - 1  # '[' の位置
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


def name_pair(i18n):
    return i18n.get("en-US"), i18n.get("ja-JP")


def main():
    skills_html = fetch("skills")
    stages_html = fetch("stages")

    # SkillKey -> 属性
    skill_elem = {}
    for sk in extract_array(skills_html, "activeSkills"):
        dt = sk.get("DamageType")
        if dt:
            skill_elem[sk["SkillKey"]] = ELEM.get(dt, dt.lower())

    monsters = extract_array(stages_html, "monsters")

    enemies = []
    stage_map = {}  # stageKey -> stage dict
    for m in monsters:
        en, ja = name_pair(m.get("MonsterNameStringKey_i18n", {}))
        sk = m.get("SkillKey")
        # SkillKey は単一ID(int)か、ボスのみ "id1 id2 ..."(複数フェーズ)。常にリスト化
        skill_keys = [int(x) for x in str(sk).split()] if sk is not None else []
        a = m.get("attack") or {}            # 基本攻撃の詳細(act1の16体のみ。残り45体は未公開でnull)
        val = a.get("value")                  # スキル倍率(‰)。1000 = ×1.0
        element = ELEM.get(a.get("damageType")) or skill_elem.get(sk)
        enemy = {
            "key": m["MonsterKey"],
            "nameEn": en,
            "nameJa": ja,
            "type": m.get("MONSTERTYPE"),
            "atk": m.get("AttackDamage"),
            "atkSpeed": m.get("AttackSpeed"),
            "hp": m.get("MaxLife"),
            "moveSpeed": m.get("MovementSpeed"),
            "gold": m.get("RewardGold"),
            "exp": m.get("RewardExp"),
            "skillKey": sk,
            # 属性: 敵自身の attack.damageType を最優先、無ければスキルDBから。どちらも無ければ null（推測しない）
            "element": element,
            # 敵の1発(基本攻撃)のダメージ詳細。damage は基準値で、各ステージの実値は level に応じてスケールする。
            # multiplier=スキル倍率(value/1000)、range=射程、activation=発動種別、skillKeys=参照スキルID。
            # source が attack を持たない45体は range/multiplier/activation を null（推測しない）。
            "attack": {
                "damage": m.get("AttackDamage"),                 # 1発の基礎ダメージ(基準/レベルスケール)
                "element": element,                              # 属性(=element)
                "range": a.get("range"),                         # 射程(未公開=null)
                "speed": m.get("AttackSpeed"),                   # 攻撃速度(=atkSpeed)
                "multiplier": (val / 1000) if val is not None else None,  # スキル倍率 × (未公開=null)
                "activation": a.get("activation"),               # 発動種別 BASEATTACK(未公開=null)
                "skillKeys": skill_keys,                         # 参照スキルID(ボスは複数フェーズ)
                "detailed": bool(a),                             # source が完全な攻撃詳細を持つか
            },
        }
        appear = m.get("stages", [])
        enemy["stageCount"] = len(appear)
        enemies.append(enemy)

        for s in appear:
            k = s["key"]
            st = stage_map.setdefault(k, {
                "key": k, "act": s["act"], "no": s["no"], "level": s["level"],
                "difficulty": s["difficulty"], "type": s["type"],
                "boss": None, "enemies": [],
            })
            if s.get("boss"):
                st["boss"] = m["MonsterKey"]
            else:
                st["enemies"].append(m["MonsterKey"])

    # ステージEXP/Gold(クリア期待値)を tbh.city からマージ（id == probonk key）
    try:
        city = fetch_city("stages")
        for part in re.split(r'(?=\{"id":\d+,"act":)', city):
            mid = re.match(r'\{"id":(\d+),"act":', part)
            mg = re.search(r'"expected_gold":(\d+)', part)
            me = re.search(r'"expected_exp":(\d+)', part)
            if mid and mg and me:
                k = int(mid.group(1))
                if k in stage_map:
                    stage_map[k]["expectedGold"] = int(mg.group(1))
                    stage_map[k]["expectedExp"] = int(me.group(1))
        got = sum(1 for s in stage_map.values() if "expectedExp" in s)
        print("stage exp/gold merged (tbh.city):", got)
    except Exception as e:
        print("tbh.city merge skip:", e)

    stages = sorted(stage_map.values(), key=lambda s: s["key"])
    enemies.sort(key=lambda e: e["key"])

    # 難易度の属性耐性デバフ(probonk/buffs由来) + 各難易度のレベル範囲(自stagesから導出)。
    # 9100011〜9100034 = Fire/Cold/Lightning/Chaos Resistance を -20/-40/-60(FLAT Debuff)する3段階。
    # tier3段はちょうど NORMAL以外の3難易度に1:1対応(ペナルティ昇順、レベル範囲一致)。
    buffs_html = fetch("buffs")
    res_debuffs = [b for b in extract_array(buffs_html, "buffs")
                   if 9100000 <= b["BuffKey"] < 9200000 and b.get("STATTYPE") in RES_STAT]
    pen_keys = {}   # penalty(20/40/60) -> {element: buffKey}
    for b in res_debuffs:
        pen_keys.setdefault(b["Value"], {})[RES_STAT[b["STATTYPE"]]] = b["BuffKey"]
    lv_range = {}   # difficulty -> [min,max]
    for s in stages:
        r = lv_range.setdefault(s["difficulty"], [s["level"], s["level"]])
        r[0] = min(r[0], s["level"]); r[1] = max(r[1], s["level"])
    difficulties = []
    for pen, name in DIFF_BY_PENALTY.items():
        keys = pen_keys.get(pen, {})
        rng = lv_range.get(name) or [None, None]
        difficulties.append({
            "name": name,
            "tier": pen // 20,                                   # 0=NORMAL,1=NIGHTMARE,2=HELL,3=TORMENT
            "levelMin": rng[0], "levelMax": rng[1],
            "stageCount": sum(1 for s in stages if s["difficulty"] == name),
            # プレイヤーが受ける属性耐性デバフ(FLAT減算)。NORMALは無し。全属性同値。
            "resistancePenalty": pen,
            "resistanceDebuff": {el: -pen for el in ("fire", "cold", "lightning", "chaos")} if pen else {},
            "resBuffKeys": keys,                                 # probonk buff の参照ID(属性→ID)
        })

    path = os.path.join(HERE, "tbh-data.json")
    data = json.load(open(path, encoding="utf-8"))
    data["enemies"] = enemies
    data["stages"] = stages
    data["difficulties"] = difficulties
    data.setdefault("_meta", {})["enemyStageNote"] = (
        "enemies/stages は probonk(実機データマイン)由来。敵の atk/hp は基準値で各ステージの実値は "
        "level に応じてスケールする。element は敵attack/スキルの DamageType から導出した分のみ(残りは null=未確認)。"
        "各敵の attack は1発(基本攻撃)の詳細: damage=1発の基礎ダメージ(基準/レベルスケール), element=属性, "
        "range=射程, multiplier=スキル倍率(value/1000, 通常×1.0), speed=攻撃速度, activation=発動種別, "
        "skillKeys=参照スキルID(ボスは複数フェーズ), detailed=完全な攻撃詳細の有無。"
        "source が attack 詳細を持つのはact1の16体のみで、残り45体は range/multiplier/activation が未公開のため null(推測しない)。"
        "stages の expectedExp/expectedGold はクリア期待値(tbh.city由来)。"
        "difficulty: NORMAL/NIGHTMARE/HELL/TORMENT。type ACTBOSS=章ボスステージ。"
        "difficulties[] は各難易度の属性耐性デバフとレベル範囲: resistancePenalty=プレイヤーの全属性耐性が受けるFLAT減算 "
        "(NORMAL 0 / NIGHTMARE -20 / HELL -40 / TORMENT -60), resistanceDebuff=属性別の減算値, "
        "resBuffKeys=probonk buff の参照ID。ペナルティ値はprobonk/buffs由来(事実)、tier↔難易度名の対応は "
        "tier3段=NORMAL以外の3難易度の1:1対応(ペナルティ昇順+レベル範囲一致)から確定した構造的対応。"
    )
    json.dump(data, open(path, "w", encoding="utf-8"), ensure_ascii=False, indent=2)

    elemok = sum(1 for e in enemies if e["element"])
    print("enemies:", len(enemies), "(element resolved:", elemok, ")")
    print("stages:", len(stages))
    diffs = {}
    for s in stages:
        diffs[s["difficulty"]] = diffs.get(s["difficulty"], 0) + 1
    print("difficulties:", diffs)
    for d in difficulties:
        print("  %-9s Lv%s-%s  res%+d (keys:%d)" % (
            d["name"], d["levelMin"], d["levelMax"], -d["resistancePenalty"], len(d["resBuffKeys"])))


if __name__ == "__main__":
    main()
