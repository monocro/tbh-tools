<!-- 言語 / Language -->
**日本語** · [English](README.en.md) · [中文](README.zh.md)

# TBH Tools — Task Bar Hero 非公式ツール集

**Task Bar Hero**（タスクバーヒーロー）を遊ぶ人のための無料ツール集です。
ブラウザですぐ使える **Webツール** と、ゲーム中に価格を瞬時に出す Windows アプリ **MarketLens** の2種類があります。

---

## 🌐 Webツール ＝ インストール不要・リンクをクリックするだけ

### ▶ まとめページ： **https://ghostsharkrobotics.github.io/tbh-tools/**

ダウンロードは要りません。ブックマークすれば PC でもスマホでも使えます。下のリンクから直接ひらけます：

| ツール | できること |
|---|---|
| [🏆 最強ビルドメーカー](https://ghostsharkrobotics.github.io/tbh-tools/tbh-best-build.html) | 各部位の装備・宝石・彫刻・刻印を選ぶと DPS を自動計算。「最強」ボタンで全クラス×全装備を総当たりして最高 DPS 構成を自動セット。 |
| [🔍 アイテム検索](https://ghostsharkrobotics.github.io/tbh-tools/tbh-gem-search.html) | 装備・宝石・彫刻・刻印・特殊ステータスを **効果や名前で検索**。市場価格つき・日英対応・並べ替え可。 |
| [💰 お買い得ファインダー](https://ghostsharkrobotics.github.io/tbh-tools/tbh-deals.html) | 市場の装備を「強さ ÷ 今の最安値」で並べ、相場より安い出品を発見。Steam マーケットへ直リンク。 |
| [🧱 クラフト素材](https://ghostsharkrobotics.github.io/tbh-tools/tbh-crafting.html) | クラフト（キューブ）のレシピと必要素材を tier・部位で検索。素材ごとに価格つき・直リンク。 |
| [📦 ステージ別ドロップ](https://ghostsharkrobotics.github.io/tbh-tools/tbh-stage-drops.html) | どのステージのどの箱から何が出るかを確率つきで一覧。「どこで掘れる？」の逆引きも。 |
| [⚡ 経験値効率](https://ghostsharkrobotics.github.io/tbh-tools/tbh-exp.html) | レベルからオーバーレベル補正済みの EXP でステージを順位付け。クリアタイムを入れると毎時 EXP で並べ替え。 |
| [🛠 ビルドシミュレーター](https://ghostsharkrobotics.github.io/tbh-tools/tbh-build-simulator.html) | 装備・宝石・バフから DPS を計算。 |
| [📊 DPS計算機](https://ghostsharkrobotics.github.io/tbh-tools/tbh-dps.html) | シンプルな DPS 計算。 |
| [📖 仕様メモ](https://ghostsharkrobotics.github.io/tbh-tools/tbh-info.html) | 合成・クラフトの排出種類と確率、DLC 条件、箱ドロップ率。 |

価格データは GitHub Actions で毎日自動更新されます。

---

## 🖥 TBH MarketLens（Windows アプリ）

ゲーム中、**アイテムにカーソルを合わせてキーを押すだけ**で、その Steam 市場価格（最安・中央値）を小さなカードで表示します。タイピングも Alt+Tab も不要。日本語 / English / 中文 対応。

### ⬇ ダウンロード手順（GitHub に詳しくない方向け）

1. ダウンロードページを開く → **[📥 Releases ページ](https://github.com/GhostSharkRobotics/tbh-marketlens/releases)**
2. 一番上（最新版）の **「Assets」** を開き、**`TBH-MarketLens` で始まる `.zip` ファイル**をクリックしてダウンロード
3. ダウンロードした zip を **右クリック → すべて展開**
4. 展開フォルダの中の **`TBH MarketLens.exe`** をダブルクリックで起動

> 💡 起動時に「**WindowsによってPCが保護されました**」と出たら、**詳細情報 → 実行** を押してください（署名なしアプリのための表示で、問題ありません）。

起動するとタスクトレイ（時計の近く）に常駐します。初回に使い方が表示されます。発動キーや表示言語は **トレイ → 設定** で変えられます（既定の発動キーはマウスの「戻る」ボタン）。

### 安全？（チート対策）

はい。MarketLens は**完全に別プログラム**として動き、①自分の画面を撮って文字を読む、②あなたが決めたキーを待つ、③Steam の公開価格 API に問い合わせる、だけです。**ゲームのメモリを読み書きせず、何も注入せず、ゲームプロセスに一切触れません。** 速度・時間の操作もありません。詳しくは [dist-README.md](dist-README.md)。

---

## 開発者向けメモ

- Web ツールは各 HTML がデータを内蔵した**自己完結ファイル**（オフラインでも動作）。`tbh-data.json` 等からビルドスクリプトで生成。
- MarketLens 本体は `tbh-price-ocr.py`、配布版は別リポジトリ [tbh-marketlens](https://github.com/GhostSharkRobotics/tbh-marketlens) の Releases。
- 価格は日次の GitHub Actions で自動更新。

---

*Task Bar Hero のファンが作った非公式ツールです。 by **Ghost Shark Robotics** — [☕ Ko-fi](https://ko-fi.com/ghostsharkrobotics)*
