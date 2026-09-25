# 0009. NSFW自動仕分けにMarqo/nsfw-image-detection-384を採用する

- ステータス: 決定
- 決定日: 2026-09-25
- 決定者: プロジェクトオーナー

## コンテキストと課題

[ADR-0008](0008-nsfw-auto-triage-with-human-approval.md)でNSFW自動仕分け機能の導入を決定したが、具体的な分類モデル・実装方式は未定義だった。別セッションでの技術検討（オープン学習済みモデルの比較）を踏まえ、モデルと実装方式を確定する。

## 検討した選択肢

| モデル | ベース | 精度（提供元公表値） | 特徴 |
|---|---|---|---|
| Marqo/nsfw-image-detection-384 | ViT（timm） | 98.56%（自社データ） | 軽量（他モデルの1/18〜1/20サイズ）。実写・イラスト・AI生成画像を含む22万枚で学習 |
| AdamCodd/vit-base-nsfw-detector | google/vit-base-patch16-384 | 96.5% / AUC 0.995 | 判定基準が厳しめに調整済み（肌の露出等で過検知しやすい可能性） |
| prithivMLmods/Guard-Against-Unsafe-Content-Siglip2 | google/siglip2-base | 詳細値未公開 | 比較的新しい設計 |
| prithivMLmods/ImageShield-MMCF-2B-vl | Qwen3-VL-2B-Instruct | 実験的リリース | VLMベース。理由付き判定が可能だが推論コストが重い |

CLIP/SigLIPによるゼロショット分類、VLMに直接判定させる方式も検討したが、専用分類器に比べて精度・実装コストの面で劣ると判断した。

なお、いずれのモデルも精度は各提供元の自社データセットにおける数値であり、「NSFW」の定義はデータセット依存・主観的である点に注意が必要（下記「悪い影響」参照）。

## 決定

Marqo/nsfw-image-detection-384を採用する。AI生成画像・イラストを含むデータセットで学習されており、Grok Imagine出力（AI生成の画像・動画）との相性が良いと判断したため。ただし精度は提供元の自社データでの数値であり、ReelMilly実データでの誤判定率は別途検証する（[TODO.md](../../TODO.md)）。

### 実装方式

- ライブラリ: `timm`（`hf_hub:Marqo/nsfw-image-detection-384`）。依存: `torch`, `pillow`, `opencv-python`
- 静止画: 画像を直接モデルに通し、NSFWクラスの確率を`nsfw_auto_confidence`として取得する
- 動画: `opencv-python`で一定間隔（初期値2秒）ごとにフレームを抽出し、各フレームを分類。フレームごとのスコアの**最大値**を動画全体の`nsfw_auto_confidence`として採用する（安全側に倒す）
- 閾値: 初期値0.5。ReelMilly実データでの誤判定を見ながら`config.yaml`で調整可能にする
- モデルは初回実行時にHugging Face Hubから自動ダウンロードされる。オフライン運用が必要な場合は事前キャッシュが必要

### プラットフォーム別の自動投稿対象区分（ADR-0008の詳細化）

[ADR-0008](0008-nsfw-auto-triage-with-human-approval.md)で定義した`content_rating_confirmed`（人間承認）に加え、プラットフォームごとに「どの区分なら自動投稿してよいか」を`config.yaml`の`platform_auto_post_ratings`で定義する。これは[ADR-0006](0006-platform-content-rules-from-cream.md)の`platform_content_rules`（投稿を許可するかどうかの制約）とは別軸で、「許可されてはいるが自動化のリスクを抑えたい」場合の運用ポリシーである。

```yaml
platform_auto_post_ratings:
  fanvue: [sfw, suggestive, explicit]   # 判定結果に関わらず自動投稿対象（NSFW前提のプラットフォームのため）
  x: [sfw]                               # sfw以外は自動対象から除外し、Telegramで個別に「要確認」通知する
```

`x`で自動対象から除外されたアセットも投稿禁止にはしない。既存の手動コマンド（`/teaser`等）で人間が個別に判断して投稿することは引き続き可能。

## 結果

### 良い影響

- ローカルCPUでも実用速度で推論可能な軽量モデルのため、常時起動PC運用（[ADR-0005](0005-deployment-environment.md)、検討中）と相性が良い
- AI生成画像を含む学習データのため、Grok Imagine出力に対する精度が期待できる（要実データ検証）

### 悪い影響・トレードオフ

- 「NSFW」の判定基準はモデルの学習データセットの定義に依存する主観的なものであり、閾値調整・誤判定の検証が運用開始後も継続的に必要
- 動画のフレームサンプリングは処理コストが増える。間隔を短くするほど精度は上がるがコストも増加するため、閾値とあわせてチューニングが必要
- オフライン環境での初回起動時にモデルダウンロードが必要（要事前キャッシュ）
