# TBH MarketLens — 仕様書（スレッドをまたぐ唯一の真実）

> 新しいスレッドで作業する前に必ずこれを読む。コードの推測で動かない。
> 本体: `tbh-price-ocr.py`（pythonw常駐・タスクトレイ・tkinterポップ）。配信名 **Ghost Shark Robotics**。現行 v1.1（未公開）。

## 1. 概要
ゲーム「TBH: Task Bar Hero」(`taskbarhero.exe`, Steam appid `3678970`)用の常駐ツール。
アイテムにカーソルを合わせ**発動キー**（既定=マウスのサイドボタン「戻る」。設定で自由割当）を押すと、
画面の名前領域をOCR→既知アイテムへ曖昧マッチ→**Steam市場価格をカード型ポップで表示**。タイピング不要。
ゲームには一切干渉しない（別プロセスで画面OCR＋ホットキーのみ＝チート非検出。[[tbh-tools-no-cheat-detection]] 厳守）。

## 2. 配備手順（最重要・これを外すと「何も変わらない」が再発）
Windows実機(Tailscale `ssh tbhwin`, 鍵`~/.ssh/tbh_win`, 配備先`C:\Users\monoq\tbh-price-ocr\`)へ。
**必ずこの順序**：
1. `ssh tbhwin "taskkill /f /im pythonw.exe"`（**先に殺す**。稼働中はファイルロックでscpが黙って失敗→旧版が動き続ける＝過去最大の消耗源）
2. `scp -i ~/.ssh/tbh_win tbh-price-ocr.py tbhwin:tbh-price-ocr/tbh-price-ocr.py`
3. **ハッシュ照合**：`md5 -q`(ローカル) と `certutil -hashfile ... MD5`(リモート) が一致するまで確認。`>/dev/null`でscpのエラーを握り潰さない
4. schtasks(`/it`対話デスクトップ)で起動 → `tasklist|findstr pythonw` が**単一PID**・`error.log`無し(NO_ERROR)を確認
変更したら**毎回pushして公開URLにも反映**（[[tbh-deploy-to-live]]）。デプロイ前に `python3 i18n_lint.py` 必須。

## 3. ファイル構成（repo root。tools/はgitignoreのためroot配置）
- `tbh-price-ocr.py` … 本体
- `tbh-price-lookup.json` … 全アイテムの名前索引＋バンドル価格（USD, `cur:1`）。`tbh-build-price-lookup.py`が生成（`tbh-data.json`＋`tbh-prices.json`＋`localization.json`の中国語名＋`market-icons.json`から）
- `tbh_price_match.py` … OCR文字→既知名の曖昧スナップ（stdlibのみ。`open`は`encoding=utf-8`必須＝Win cp932対策）
- `i18n_lint.py` … TR(文言カタログ)完全性＋UIに日本語直書きが無いかをAST検査
- `start-tbh-price.bat` / `tbh-price-ocr.ps1`(irm|iex導入) / `dist-README.md`(配布用)
- 価格は日次GitHub Actionsで全自動更新（[[tbh-price-autoupdate-actions]]）。手動取得不要

## 4. 価格取得の仕様（最も誤解されやすい・実機検証済み 2026-06）
**結論：普段は search/render の単品USD。現地¥は今Steamが出さない（=BANではない）。**

- **priceoverview**（`/market/priceoverview/`, 現地通貨¥を返す）は**全IPで429**。クリーンなMac(Steam未アクセス)でも429＝
  **IP-BANではなくエンドポイント自体が匿名に出していないだけ**。「BANされた」と誤認しない。復活したら自動で正確¥に格上げ。
- **search/render**（`/market/search/render/`）が**本線**。`_render_price()`が**品名+レア度クエリ**で叩き、その変種の現在USDを**1リクエストで取得**。429になりにくい＝**BANされない**。
  - クエリは**記号除去必須**：`-`はSteam検索の除外演算子で誤爆。`"War Bow (Legendary) A"→"War Bow Legendary"`、`"Soulstone - Torment"→"Soulstone Torment"`
  - **USD固定**（currency/country パラメータ無視）。**10件/ページ固定**（全624種の一括取得は63req必要＝非現実的。だから単品クエリ方式）
  - 市場未出品の品は**0件→バンドル価格にフォールバック**（「該当なし」ではなく既存USD価格を保持）
- **通貨**：`_CURRENCY = {en:1=USD, ja:8=JPY, zh:23=CNY}`（UI言語に連動）。
  - en(USD)＝search/renderの値そのまま＝**Steamと完全一致の正確値**
  - ja/zh(¥)＝Steamが¥を出さないので**USDを為替換算**（`JPY_RATE/CNY_RATE`, `fetch_rate()`がopen.er-api.comで起動時更新）。数円ズレ得る
- **表示は印を付けない**：以前 `≈`/`🕓`/グレーを付けたら「壊れ・オフライン・BAN」に見えると却下された。
  **価格は普通の色で堂々と「¥29」「$0.19」**と出す（`price(c, src)`は印なし）。
- **キャッシュ**：render=5分(`_render_cache`)、native=5分(`_price_cache`)。バックオフは**render用`_render_blocked`とnative用`_rl_blocked`を分離**（native429がrenderを止めないため）。
- 主要関数：`_render_price`(単品USD) / `_native_price`(priceoverview¥) / `live_price(...)→(low,med,vol,src)` / `apply_live(ent,...)`(ent書換＝sell/median/volume/cur/_live)

## 5. UI仕様
**レンズ・ポップ**（`show_popup`）:
- overrideredirect + Win11角丸。**NOACTIVATE+keep_on_topでフルスクリーンゲームの前面に常駐**（透過/フォーカス奪取は消える原因なので不可）。外側クリック/ホバーアウト/無操作6秒(`POPUP_SECONDS`)で閉じる。✕は右上角。
- **読み取り中**＝スピナー回転（静止文字にしない）。
- **マッチ時**＝アイテム名／レア度ドロップダウン（選び直しで再マッチ）／価格（安値・中央）／種別・出品数／🛒Steam市場／🕘履歴。
- **該当なし時**＝**「該当なし」＋🕘履歴＋✕ だけ**。文字化けの生OCR・レア度・価格・🛒は**一切出さない**（開く先の無いボタンを出さない）。

**履歴ウィンドウ**（`show_history`, トレイでオン/オフ, `tbh-price-history.json`永続化, 位置/サイズ記憶）:
- 行：アイコン（CDN`/96x96`をmd5名でローカルキャッシュ。無い品はレア度色タイル）／名前・レア度／価格／種別／時刻。右クリックで お気に入り・名前変更・レア度変更・削除。上限設定(既定50, お気に入りは対象外)。
- **全部更新ボタン**：押すと**ボタンにスピナー＋「12/50」件数**、ヘッダー下に**進捗バー**（緑=取得済、render待機中はアンバーの流れる帯＋⏳残り秒）、**各行は更新時に一瞬フラッシュ**、完了で✓。連打は無視(`_hist_updating`)、言語切替/再押下で世代`_hist_gen`が古い取得を中断。
- レンズ中は最新1件だけ増分反映(`_hist_sync_top`)＝全消ししない（チラつき防止）。
- **状態は文章でなくUIで見せる**のが鉄則（ユーザー強い要望。[[tbh-ux-principles]] [[tbh-compact-display-principle]]）。

**設定**（トレイ→設定）: 表示言語(ja/en/zh, 起動時PC言語自動取得)／発動キー割当(欄クリック→任意キー/組合せを押す、実況表示)。`tbh-price-settings.json`永続化。初回起動で使い方画面。

**配信系**: フッターに `v1.1 · by Ghost Shark Robotics`、Ko-fi寄付(`KOFI_URL`)、アプリ内フィードバック(`FEEDBACK_URL`→Cloudflare Worker→Slack, 匿名・返信先任意)。起動時にGitHub最新リリースを確認し新版を控えめ告知。

**利用統計（匿名テレメトリ）** [[marketlens-telemetry]]: `STATS_URL`(=Worker `/ml`)へ `_telemetry_send(ev,item,rarity,err)` が送信。`ev=launch`(main・言語確定後)／`lookup`(found確定時・アイテム英名+rarity_en)／`error`(`log_fatal`本文・`_scrub()`でユーザー名/パス伏字化)。匿名ID `_cid`(uuid4・設定に永続・IP由来でない)。**IP・Steam在庫・個人情報は送らない**(国はWorkerがエッジで付与・IP非保存)。設定→「利用統計」トグル(`_telemetry`, 既定オン)でオフ可。閲覧: `…/mlstats?pw=<DASH_PW>`。dist-READMEに開示済み。

## 6. i18n
全UI文字列は `TR[lang][key]` ＋ `T(key, **fmt)` 経由。`LANGS=("ja","en","zh")`。
日本語直書き禁止。**デプロイ前に `python3 i18n_lint.py`（カタログ完全＋UI日本語漏れ検査）必須**（[[marketlens-i18n]]）。

## 7. 既知の制約・落とし穴
- Steamの¥(priceoverview)は現在取得不可＝¥は為替換算の概算。正確値が要るなら英語(USD)。これはSteam側都合でアプリの不具合でない。
- search/renderは10件/ページ・USD固定。全件一括は非現実的（単品クエリで運用）。
- 「直した」と言う前に実機の挙動/配備物を必ず確認（[[verify-before-claiming-fixed]]）。pythonwが残る/二重起動を疑う。
- **v1.1の公開（GitHub Releases）はユーザーの明示確認がない限り絶対にしない**。

## 8. 状態（2026-06時点）
価格本線=search/render切替・印なし表示・該当なし最小表示まで実装＆実機反映済み。
匿名テレメトリ(`/ml`+`/mlstats`)をコード実装済み＝ローカル検証(py compile/i18n_lint/node --check)通過。
未了：**Worker再デプロイ**（`/feedback`+`/ml`の両方。`cd worker && npx wrangler deploy`＝wrangler未ログイン→Cloudflare認証待ち）／Win機へ scp 反映／Ko-fi最終確認／v1.1ビルド＆公開（**ユーザー確認後のみ**）／.icoアイコン・告知文・中国語レア度/種別訳。

関連メモリ: [[tbh-price-ocr-tool]] [[marketlens-i18n]] [[tbh-tools-no-cheat-detection]] [[tbh-price-ocr-tool]] [[verify-before-claiming-fixed]] [[tbh-deploy-to-live]]
