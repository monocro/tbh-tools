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


def fetch(slug):
    req = urllib.request.Request(BASE + slug, headers={"User-Agent": "Mozilla/5.0"})
    return urllib.request.urlopen(req, timeout=40).read().decode("utf-8", "replace")


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
            "element": skill_elem.get(sk),  # 不明は null（推測しない）
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

    stages = sorted(stage_map.values(), key=lambda s: s["key"])
    enemies.sort(key=lambda e: e["key"])

    path = os.path.join(HERE, "tbh-data.json")
    data = json.load(open(path, encoding="utf-8"))
    data["enemies"] = enemies
    data["stages"] = stages
    data.setdefault("_meta", {})["enemyStageNote"] = (
        "enemies/stages は probonk(実機データマイン)由来。敵の atk/hp は基準値で各ステージの実値は "
        "level に応じてスケールする。element はスキルの DamageType から導出した分のみ(残りは null=未確認)。"
        "difficulty: NORMAL/NIGHTMARE/HELL/TORMENT。type ACTBOSS=章ボスステージ。"
    )
    json.dump(data, open(path, "w", encoding="utf-8"), ensure_ascii=False, indent=2)

    elemok = sum(1 for e in enemies if e["element"])
    print("enemies:", len(enemies), "(element resolved:", elemok, ")")
    print("stages:", len(stages))
    diffs = {}
    for s in stages:
        diffs[s["difficulty"]] = diffs.get(s["difficulty"], 0) + 1
    print("difficulties:", diffs)


if __name__ == "__main__":
    main()
