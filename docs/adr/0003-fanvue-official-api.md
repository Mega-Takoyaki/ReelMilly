# 0003. Fanvue投稿は公式REST APIを採用する

- ステータス: 決定
- 決定日: 2026-09-15
- 決定者: プロジェクトオーナー

## コンテキストと課題

Fanvueへの投稿手段として、公式APIとPlaywrightによるダッシュボード操作のどちらを使うか。

## 検討した選択肢

- Fanvue公式REST API（`https://api.fanvue.com`）
- Playwrightによるダッシュボード操作

## 決定

公式REST APIを採用する。`write:media` `write:post` `read:post` スコープを利用し、multipart uploadとpost作成を行う。

## 結果

### 良い影響

- 公式APIのため安定性が高く、DOM変更の影響を受けない

### 悪い影響・トレードオフ

- 公開URLがAPIから安定して返らないため、`.env`のテンプレート（`FANVUE_POST_URL_TEMPLATE`）を手動で組み立てる必要がある
- トークンの長期運用・refreshの仕組みは別途検討が必要（当面は手動更新）
