# 0021. Fanvue API認証をOAuth 2.0(authorization code + PKCE)に作り直す

- ステータス: 決定
- 決定日: 2026-09-28
- 決定者: プロジェクトオーナー

## コンテキストと課題

[ADR-0003](0003-fanvue-official-api.md)でFanvue公式APIの採用を決め、`src/posting/fanvue.py`(`FanvueClient`)を実装した。当初の実装はCLAUDE_HANDOFF.md 7章の記述（一次情報未検証）に基づき、`.env`の`FANVUE_API_TOKEN`を固定のBearerトークンとしてヘッダに載せ続けるだけの単純な方式を前提にしていた。

プロジェクトオーナーの依頼を受け、Fanvue公式OpenAPI仕様（`https://api.fanvue.com/docs/openapi.json`、2026-09-28に一次情報として取得・照合）で実際の仕様を確認した結果、以下が判明した。

- Fanvue APIの認証は**OAuth 2.0（authorization codeフロー、PKCE）専用**であり、`securitySchemes`に静的APIキー方式は存在しない
- 認可エンドポイント: `https://auth.fanvue.com/oauth2/auth`
- トークンエンドポイント（リフレッシュも同じ）: `https://auth.fanvue.com/oauth2/token`
- アクセストークンは短命で、リフレッシュトークンによる更新が必要
- Fanvue Developer領域でOAuthアプリを作成しClient ID/Secretを取得する必要があり、これにはクリエイター登録・KYC（本人確認）完了が前提条件
- 一方、ベースURL・主要エンドポイントのパス構造（`/users/me`・`/media/uploads`・`/posts`等、`/v1`プレフィックスなしの現行既定バージョン）・リクエストボディのフィールド名は、既存実装とほぼ一致していた（詳細はTODO.md参照）。認証方式のみが根本的に異なっていた

「`.env`にトークンを貼り付けるだけ」という従来の運用は成立しないため、OAuth 2.0のフロー全体（認可URL生成、コールバック受信、コード⇔トークン交換、トークンの永続化と自動更新）を実装する必要がある。

## 検討した選択肢

- 認可コードフローの受け皿をどこに置くか: 本体Web UI（既にローカルで動いているFlaskアプリ）にコールバックルートを追加する / 別途CLIコマンドでローカルサーバーを一時起動する
- トークンの保存場所: `.env`（秘密情報はここに置く方針、ADR-0016） / DBの`settings`テーブル / `state_dir`配下の専用ファイル

## 決定

### 1. 認可コードの受け皿は本体Web UIに追加する

`reelmilly web`は既にローカルWebアプリとして常時起動できる想定のため、`/settings/fanvue/oauth/start`（認可URLへリダイレクト）・`/settings/fanvue/oauth/callback`（コード⇔トークン交換）・`/settings/fanvue/disconnect`（連携解除）を追加する。設定画面に「Fanvueと連携する」ボタンを設置し、ユーザーはブラウザ操作だけで連携できる。

PKCEのcode_verifier・stateはブラウザのリダイレクトを挟むため、DBの`settings`テーブルに一時保存し（`_fanvue_oauth_pending_*`キー、コールバック受信時に消費して削除）、Flaskのセッション機構（secret_key設定が別途必要になる）は導入しない。

### 2. トークンの保存場所は`state_dir`配下の専用JSONファイル

`.env`はユーザーが手動編集する設定用ファイルという位置づけ（ADR-0016）のため、自動的に更新され続けるアクセストークン・リフレッシュトークンはここに置かず、`data/state/fanvue_oauth_tokens.json`に保存する（`events.jsonl`と同じ置き場所）。Client ID/Secretは（ユーザーが一度設定するアプリ登録情報のため）引き続き`.env`で管理し、`FANVUE_API_TOKEN`は廃止する。

### 3. `FanvueClient`はトークン取得方法に関知しない設計にする

`FanvueClient.__init__`の`api_token`引数を、固定文字列に加えて「呼び出すたびに有効なトークンを返す関数」も受け付けるようにする(`Callable[[], str]`)。`FanvueTokenStore.get_access_token(client_id, client_secret)`を渡すことで、リクエストのたびに期限切れをチェックし、必要な場合のみ自動的にリフレッシュする。CLIエントリポイント（`cmd_doctor`・`cmd_run_drop`）は`_try_create_fanvue_client`ヘルパーで、OAuth連携済みかどうかを判定してから`FanvueClient`を組み立てる。

### 4. 認証以外に見つかった2件のバグも合わせて修正する

OpenAPI仕様との照合で見つかった、認証方式とは独立した実装ミスも本ADRの実装と合わせて修正した。

- `GET /media/uploads/{uploadId}/parts/{partNumber}/url`のレスポンスはオブジェクトではなく署名URLそのものを表す文字列だった（`part_url_info["url"]`ではなく`part_url_info`自体を使うよう修正）
- `POST /media/uploads`のレスポンスに含まれる`partSize`（サーバーが決定するパートサイズ）を使わず、固定5MBでチャンク分割していた（レスポンスの`partSize`を優先し、無い場合のみ5MBにフォールバックするよう修正）
- （軽微）`wait_for_media_ready`が存在しない`status="finalised"`を許容値に含めていた点、`status="error"`を検知せずタイムアウトまで待ち続けていた点も合わせて修正した

## 結果

### 良い影響

- Fanvue公式APIの実際の認証方式に合致した実装になった
- トークンの取得・更新がユーザーの手作業（`.env`への貼り付け）に依存しなくなり、リフレッシュも自動化される
- 副次的に見つかった2件のアップロード処理のバグも解消された

### 悪い影響・トレードオフ

- OAuth 2.0フロー全体（PKCE生成・認可URL・コールバック処理・トークン永続化・自動リフレッシュ）を自前実装する必要があり、実装量・保守対象が増えた
- 初回の連携（`/settings/fanvue/oauth/start`〜`callback`）には本体Web UI（`reelmilly web`）が起動している必要がある。CLIのみの運用（`reelmilly run drop`等をcron等で動かす）では、最初の連携だけは別途Web UIを起動して行う必要がある（連携後のトークン自動更新はWeb UI不要）
- トークンエンドポイント（`auth.fanvue.com`側）のレスポンス形式自体は一次情報で確認できておらず、OAuth 2.0標準(RFC 6749)のレスポンス形式（`access_token`/`refresh_token`/`expires_in`）を前提にした実装である。実際に接続する際に調整が必要になる可能性が残る（TODO.md参照）
- Fanvue Developer領域でのOAuthアプリ登録自体、現状は申請制・ウェイティングリストの可能性がある（TODO.md参照）ため、実際に連携できるかは別途確認が必要
