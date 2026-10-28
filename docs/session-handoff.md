# ReelMilly 引き継ぎメモ(現在の状態と作業の勘所)

新しいセッション(Claude Code)が、これまでの経緯を踏まえて続きから作業するための文書です。
古い設計書 `CLAUDE_HANDOFF.md`(Telegram前提の初期計画)は、**現状とは別物**です。現状は、この文書と `README.md`・`TODO.md`・`docs/architecture.md` を正としてください。
最終更新: 2026-10-28

## 1. これは何か
Grok Imagine で作った画像・動画を管理し、Fanvue と X へ投稿するローカルアプリ。ユーザーは日本語で話し、画面・通知・コミットの説明も日本語。
- Flask + SQLite の Web UI(`reelmilly web`、既定 http://127.0.0.1:8420)と、常駐ワーカー(`reelmilly watch`)
- リポジトリ: `Mega-Takoyaki/ReelMilly`(main)。作業フォルダ `C:\Users\tando\_dev\ReelMilly_dev\src`、Pythonは `src/src/`、テストは `src/tests/`、仮想環境 `src/.venv`

## 2. 主な機能(実装済み)
- 取り込み・一覧・詳細・タグ・フォルダ・ごみ箱・重複検出・非表示・破綻画像、区分(sfw/suggestive/explicit)の承認
- AIの判定・説明文・タグ生成(ローカルVLM。CPUのみのPC)。**AI処理の負荷**: 設定の「AI処理」タブで、一時停止・CPUスレッド数・低優先度(`core/settings.py` の `get_ai_limits`、`worker.py` の `apply_limits`)
- 投稿・予約: Fanvue / X(`posting/`)。今すぐ投稿と予約投稿(ワーカーのスレッドが実行)。成功通知に投稿先URLのリンク
- **原本と加工版**: 取り込んだ元のファイル=「原本」、そこから作ったファイル=「加工版」(`core/versions.py`、`core/edits.py`)。加工版の区分は、承認するまで原本から引き継ぎ、承認すると独立
- 加工の種類(`asset_edits` テーブル、ワーカーの編集スレッドが処理): 切り出し(trim)、静止画の取り出し(frame)、ぼかし・モザイク(mask: `core/mask.py`)、テロップ・スタンプ(overlay: `core/overlay.py`、`core/fonts.py`、`core/stamps.py`)、透かし(`core/watermark.py`)
- 加工版の加工版が可能(`params.source` = `original`/`wm`/`edit:<ID>`、`params.media` = image/video)
- テロップ: 日本語フォント、縁・影・帯・斜体・太字、動画の流れる動き、フェード、スタイル保存、テンプレート保存と複数作品への一括適用(`state/overlay_presets.json`)
- 通知(ベル)、設定画面、ヘルプ(`web/templates/help.html`。機能を足したら、ここも更新する)

## 3. 動かし方・確認の手順(Windows)
- サービス: `scripts/run-service.bat` が web と watch を再起動ループで動かす。**コードやテンプレートを変えたら、該当プロセスを PID で止める**と10秒ほどで自動再起動する(テンプレートはFlaskが読み直さない。静的ファイルは、ブラウザの強制再読み込みが要ることがある)。コマンドライン文字列の正規表現での一括killは、自分のシェルを巻き込むので避ける
- ログ: `data/logs/web.log`、`data/logs/watch.log`
- テスト: `.venv/Scripts/python -m pytest -q`(全体で4〜6分。バックグラウンドで)。変更したら関連テストを回し、最後に全体を回す
- ブラウザでの確認: 内蔵ブラウザ(`mcp__Claude_Browser__*`)で実際に操作して確認する。作ったテスト用データは、確認後に削除する(実データを汚さない)
- シェル: Git Bash とPowerShell。複数行の文字列をbashのヒアドキュメントに入れるとクォートで失敗しやすい。長いスクリプトは、ファイルに書いてから実行する(終わったら削除。ゴミを残さない)
- DB: `data/state/reelmilly.db`(SQLite)。スキーマ変更は `core/schema.sql` と `core/db.py` の `_migrate`(冪等)

### スマホからの接続(Remote Control)
- デスクトップアプリのセッションは、アプリやOSの再起動のあと、**Remote Controlが自動では再接続されない**(「新しく始めるセッション」だけが、自動接続の対象)。スマホには「Remote controlセッションがオフラインです」と出る
- 対策として、ログオン時に `claude remote-control`(サーバーモード)を起動するスクリプトを用意した: `scripts/run-claude-remote.bat`。有効化は `powershell -ExecutionPolicy Bypass -File scripts\autostart.ps1 -EnableRemote`(無効化は `-DisableRemote`)。ログは `data/logs/claude-remote.log`
- 初回だけ、ユーザーが手で、`claude remote-control` を実行して、確認(y)に答える必要がある。組織設定で「信頼するデバイス」の登録を求められたら、`claude` で `/login` する
- CLIは `C:\Users\tando\.local\bin\claude.exe`(2.1.301に更新済み)。権限モードは `acceptEdits`

## 4. ユーザーの作業スタイル(重要)
- MVP段階。**区切りごとに、自分でコミット&プッシュして、動作確認してもらう**(許可は毎回取らない)。コミットの末尾に、指定の共同作成者の行を付ける
- 説明の長文は画面に出さず、ⓘのツールチップ(`info()` マクロ、`data-tip`)に集約する
- **余計な制限・確認機能を付けすぎない**。人が決められることは、人に任せる
- 実機で確認した範囲と、していない範囲を、正直に分けて報告する。失敗したら、そのまま伝える
- 課金・外部送信・デプロイ・実データの削除は、先に確認する

## 5. 方針・判断(覆さないこと)
- **AI生成の来歴(C2PA・EXIFの署名など)を消すための処理は作らない**。`made_with_ai` の申告は外さない。加工版で来歴が落ちるのは、画像・動画を保存し直す副作用で、目的ではない(経緯: Grok Imagineのファイルには、C2PAとAI生成の印が入っている)
- 成人向けの文脈を含む内容は、Claude(Anthropic)に送らない(タグ整理・返信文など)。ローカルLLM、または Grok API(未導入)を使う
- Xでは成人向けの投稿はしない(Fanvueで行う)。Xの投稿は、メディアごとにセンシティブ指定を付けられる(利用者が外せる。制限しない)
- Xのリフレッシュトークンは、使うたびに入れ替わる。**ReelMillyと、ほかのアプリで、同じトークンを共有しない**
- Xの料金(従量課金): 自分の投稿・メンションの読み取り $0.001/件、他人の投稿 $0.005/件、投稿 $0.015/回、**リンク付き投稿 $0.20/回**。投稿(create)は、リトライしない(二重投稿の防止)

## 6. 技術メモ(踏んだ点)
- Xの `/2/media/metadata` の `sensitive_media_warning` は、配列ではなく**オブジェクト**。OAuthには `offline.access` が必須
- Fanvueのスコープに `offline_access` が必須。価格は300セント以上
- ブラウザの `<dialog>` は最前面の層に出るので、ツールチップは、開いているダイアログの中に移して出す。開いた直後に、ⓘにフォーカスが当たらないよう、`showModal` を包んで外してある(`static/app.js`)
- 位置・大きさは、画面に対する割合で持つ(ぼかし範囲・テロップ・スタンプ)。解像度が違っても、同じ見た目になる
- Windowsで、ctypes の `SetPriorityClass` は、ハンドルの型指定が要る(`worker.py`)
- ffmpeg は winget の `Gyan.FFmpeg`(`core/ffmpeg.py` が場所を探す)

## 7. 次にやる候補(`TODO.md` も見ること)
- ユーザーが保留中: Xのセンシティブ指定が、他アカウントから見て効いていない件。タグ整理の実データへの適用(案の確認)
- 提案済み・未実装: センシティブ領域の自動検出→モザイク候補、SNS向けの縦横比クロップ(9:16・1:1・4:5)、静止画のAI高精細化、AI処理の時間帯スケジュール(夜だけ動かす等)
- **Xのレス(返信)管理は、別アプリ(別リポジトリ、AWSサーバレス)として作る**ことになった。引き継ぎの案は別途ユーザーが持っている。ReelMillyには、組み込まない
- **投稿先SNSの追加(Instagram・TikTok・Bluesky など)は、調査して保留中**。結果は `docs/sns-expansion-research.md`。再開するなら Bluesky から(Instagram・Threadsは画像の公開URLが必須、TikTokは公開に監査が要る)
- **区分の自動判定**は、sfw/suggestive/explicit の3段階の案(`docs/nsfw-rating.md`)。しきい値は設定「区分の判定」タブで調整し、同じタブから判定し直しをジョブキューに積める。**第2段階(承認済みの区分から学習する分類器)は、将来の予定で、いまはやらない**(同ドキュメントに方式を記載)。既存の判定済みの作品は旧形式(sfw/nsfw)のまま、利用者が判定し直しを実行する
