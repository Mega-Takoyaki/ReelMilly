# 投稿先SNSの追加: 調査メモ(保留中)

調査日: 2026-10-21。**状態: 保留**(ユーザーの判断で、いまは実装しない)。再開するときの出発点として残す。
Fanvue・X と同等の「投稿先」を、Instagram などへ広げる場合の、公式ドキュメントの確認結果と、判断材料。

## 結論

| SNS | 難しさ | 主な壁 | 成人向け |
|---|---|---|---|
| **Bluesky** | 易しい | アプリパスワードで認証。審査なし。画像・動画はローカルから直接アップロードできる | 自己ラベルで投稿できる |
| **Instagram** | 難しい | プロアカウントとMetaアプリが要る。**画像は公開URLが必須** | 不可 |
| **TikTok** | 難しい | 審査前のアプリの投稿は**非公開のみ**。公開には監査の通過が要る | 不可 |
| **Pinterest** | 難しい | 試用の間は、ピンが自分にしか見えない。公開にはデモ動画つきの審査が要る | 実質不可(未調査) |
| **Threads** | やや難しい | 画像・動画は**公開URLが必須**。他人のアカウントを扱うには審査が要る | 不可(未調査) |

**再開するなら、Bluesky から**(審査・公開URLの用意が要らず、成人向けもラベル付きで投稿でき、Fanvue・X の運用にいちばん近い)。

## Instagram(Instagram Graph API / Content Publishing)
公式: https://developers.facebook.com/docs/instagram-platform/content-publishing

- 対象は**プロアカウント**(ビジネス/クリエイター)のみ。個人アカウントは不可。Metaの開発者アプリの作成が要る
- 認証は2経路: Instagramログイン(`instagram_business_content_publish`、`graph.instagram.com`)と、Facebookログイン(`instagram_content_publish`、`graph.facebook.com`)。どちらも Standard/Advanced Access が要る。**自分のアカウントだけに投稿する場合に、アプリ審査が要るかは、ドキュメントに書かれていない**(実際に試さないと分からない)
- **画像は公開URL(`image_url`)が必須。JPEGのみ**(MPO・JPSは不可)。ローカルのファイルを直接送れない
- 動画・リール・ストーリーズは、公開URL(`video_url`)、または再開可能アップロード(ローカルから送れる。**Facebookログイン経路のみ**)
- 上限: 1日100件(カルーセルは1件と数える)。ただし、同じ文書の別の箇所には「50件」とあり、食い違っている。`/content_publishing_limit` で使用量を確認できる
- カルーセルは最大20枚(動画との混在も可)。すべて、最初の画像の縦横比にそろえて切り取られる。未公開のコンテナは24時間で失効
- AI生成の申告: コンテナ作成時に `is_ai_generated=true`(カルーセルは、親のコンテナだけに付ける。子に付けるとエラー)。「AI生成の申告は外さない」方針と一致
- 規約上、成人向けは不可 → 承認済みのsfwだけを対象にする想定
- 「公開URL」の出し方の案(要決定):
  - A. Tailscale Funnel で、投稿の間だけ、推測できないURLで公開(追加費用なし。Tailscale管理画面でFunnelの有効化が要る)
  - B. AWS S3などへ一時アップロードして、投稿後に削除(安定するが、AWSの設定と費用が要り、画像が外部に置かれる)
  - C. 動画だけ対応(ローカルアップロードを使う)。画像は後回し
- PNGなどは、JPEGへの変換が要る(保存し直すので、AI生成の来歴情報は落ちる。目的ではなく、副作用)

## TikTok(Content Posting API)
公式: https://developers.tiktok.com/doc/content-posting-api-get-started

- 審査前(unaudited)のクライアントが投稿した内容は、**非公開に限られる**。公開にするには、TikTokの規約への準拠を見る**監査(audit)**に通る必要がある → 個人利用でも、実用になるまでの壁が高い
- Direct Post(アカウントに直接公開)と、下書きへのアップロード(inbox)がある。`video.publish` スコープをTikTokに承認してもらう必要がある
- 動画: ローカルからのアップロード(`FILE_UPLOAD`。分割して送る)ができる。写真: 別のエンドポイント。URLから取り込む(`PULL_FROM_URL`)場合は、**所有を確認したドメイン/URLの接頭辞**から出す必要がある
- 投稿の前に、クリエイター情報(公開範囲の選択肢など)を問い合わせる決まりがある
- ほぼ動画向け。1日の上限・AI生成の申告の項目は、取得できたページには書かれていなかった(要確認)

## Pinterest(API v5)
公式: https://developers.pinterest.com/docs/key-concepts/access-tiers/

- 新しく承認されたアプリは**Trial**アクセス。Trialでは、ピンを作れても、**作成者にしか見えない**(公式の表)。公開するには、**Standard**アクセスへの申請が要る
- Standardの申請には、デモ動画(OAuthの流れと、ピンがボードに出るところ)による手動審査があるとされる(コミュニティの情報。公式には、基準が公開されていない。未確認)
- Trialで本番のピンを作ると403になる、という報告もある(公式の表と食い違い。要確認)
- 画像アップロードの条件(形式・サイズ・URLかアップロードか)は、確認できていない

## Threads(Threads API)
公式: https://developers.facebook.com/documentation/threads/posts

- 2段階(メディアコンテナを作る → `threads_publish`)。**画像・動画とも、公開URLから取得**される
- 必要な権限は `threads_basic` と `threads_content_publish`。**他人のアカウントを扱うには、アプリ審査**が要る。テスターに登録した自分のアカウントなら、審査なしで権限を付けられる、という情報がある(第三者の情報。要確認)
- 画像・動画のサイズ・長さの上限は、情報源によって食い違っている(動画は最大5分、という記述が多い)。リンクが5つを超える投稿は失敗する、という情報がある(2025-12-22以降。第三者の情報)

## Bluesky(AT Protocol)
公式: https://atproto.com/guides/labels / https://atproto.com/guides/blob-lifecycle

- 認証は、ハンドルと**アプリパスワード**(OAuthは、必須ではない)。審査はない
- メディアは `com.atproto.repo.uploadBlob` で、ローカルのファイルをそのまま送れる
- 画像の上限: 情報源により「1枚1MB程度(超える場合は、再圧縮が要る)」「最大4枚」とある。**動画**は、MP4・最大100MB程度、長さは60秒〜3分と、情報源で食い違っている → 実装時に、実機で確かめる
- **成人向け**: 自己ラベル(`porn`、`sexual`、`nudity`、`gore`)を投稿に付ける。`porn` は、画像に警告が出て、アダルト表示をオンにした18歳以上だけが開ける。アダルト表示がオフの人には、完全に隠れる
- 決めること: アカウントの有無、成人向けも `porn` ラベルつきで投稿するか(X と同じく、sfwだけにするか)

## 未調査(推測。使うなら、先に調べる)
- **Mastodon**: トークンで認証。ローカルから直接アップロードでき、「センシティブ」の指定がある。ただし、成人向けが許されるかは、サーバーごとに違う
- **Telegramのチャンネル**: ボットで、画像・動画をアップロードできる。審査なし。ただし、フォロワーを集める場所としては弱い
- **Tumblr・Reddit**: 成人向けの扱いやAPIの制限が変わっているため、調べ直しが必要

## 再開するときの実装の見取り図(Xと同等の想定)
- 連携(認証・トークンの管理。トークンの更新が要るSNSは、更新の処理)
- 投稿クライアント(`posting/` に、Fanvue・X と並べる)
- 投稿ダイアログに投稿先を追加(今すぐ・予約。予約は、既存の仕組みに載せる)
- 投稿文の生成・英訳(既存のものを流用。SNSごとの文字数・形式の検査を足す)
- 設定タブ、テスト、ヘルプの更新
- 成人向けの扱い(`content_rating` が `sfw` 以外は、投稿先ごとに、投稿できるか決める)

## 参考
- [Instagram Platform: Content Publishing](https://developers.facebook.com/docs/instagram-platform/content-publishing)
- [TikTok Content Posting API: Get Started](https://developers.tiktok.com/doc/content-posting-api-get-started)
- [AT Protocol: Labels](https://atproto.com/guides/labels)
- [AT Protocol: Uploading blobs](https://atproto.com/guides/blob-lifecycle)
- [Pinterest: Access tiers](https://developers.pinterest.com/docs/key-concepts/access-tiers/)
- [Threads Posts](https://developers.facebook.com/documentation/threads/posts)
