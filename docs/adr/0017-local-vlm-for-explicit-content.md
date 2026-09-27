# 0017. explicit判定コンテンツの内容説明・投稿文生成は自前ホスト型VLMで行う

- ステータス: 決定
- 決定日: 2026-09-27
- 決定者: プロジェクトオーナー

## コンテキストと課題

[ADR-0015](0015-ai-content-description-and-caption-generation.md)でClaude API（既定）/OpenAI API（選択可）による画像内容説明・Fanvue投稿文の自動生成を実装した。プロジェクトオーナーより「NSFW画像をClaude APIに渡してよいのか」という懸念が示され、調査の結果、以下が判明した。

- Anthropicの利用ポリシーには「性的に露骨なコンテンツ」の生成を禁止する条項があり、無条件・アダルト向けのオプトアウトもない
- 条文の文言自体は「生成（generate）」を対象にしているが、Claude公式のコンテンツモデレーション用途ガイドには「成人向けサイト運営者がプロンプトで“モデレートしないで”と指定しても、Claudeは組み込みの安全動作により性的に露骨なコンテンツを独自に検知・拒否することがある」と明記されている。つまり画像の「説明」を求める用途でも、プロンプトでは解除できない拒否が起こりうる
- 未成年に見える人物が写る画像は、上記とは別枠でCSAM検出・報告の対象になる（絶対的な制約、例外なし）
- xAI（Grok）のAPIも、画像生成機能とは別に開発者向けAPIには性的コンテンツ・ヌードへの「hard limits」があり、開発者側でのフィルタリング構築を前提とした設計になっている。Vision対応のGrok APIも一般公開されていない
- AWS Bedrock経由でClaudeを使っても解決しない。Bedrock上のClaudeは同じモデル・同じ利用ポリシーが適用される上、**AWS自身のService Terms（sexually explicit / adult servicesの送信を禁止）が別途重なる**。Bedrockには自動不正利用検知の仕組みも組み込まれている

つまり、Claude API・OpenAI API・xAI Grok API・AWS Bedrock経由のいずれも、`content_rating=explicit`相当の画像を安定して処理できる保証がない。

## 決定

`content_rating=explicit`（またはNSFW自動判定でnsfw）のアセットについては、外部ホスト型APIではなく**自前ホスト型VLM（Vision-Language Model）**をローカルで推論して内容説明・投稿文を生成する。

### 実装方式

- `core/generation.py`に`LocalVlmGenerator`を追加し、`generation_provider`設定の選択肢に`local`を追加する（既存の`claude`/`openai`と同列、[ADR-0015](0015-ai-content-description-and-caption-generation.md)の設定画面から切り替え可能）
- 既定モデル: `prithivMLmods/Qwen2-VL-2B-Abliterated-Caption-it`（Hugging Face上の公開モデル、SFW/NSFWを区別せず学習された画像キャプション用ファインチューン）。モデル名は設定画面から変更可能
- 実装は`transformers`の`AutoProcessor`/`AutoModelForImageTextToText`を使う一般的なVLMチャットテンプレートのパターンに基づく。`torch.cuda.is_available()`でGPUの有無を実行時に自動判定し、GPUがあれば自動的にそちらを使う（同じコード・同じ設定のまま、CUDA対応PCに移行すれば自動的に高速化される）
- GPUが無い環境（CPU推論）では1枚あたり数秒〜数十秒程度かかる見込み（実機未検証）。`reelmilly analyze`は元々バックグラウンドのバッチ処理として設計されているため、投稿ペースに対しては許容範囲と判断（プロジェクトオーナーの意向：「今日作った作品を今日投稿する必要はない」）
- 依存関係は`pyproject.toml`の`vlm` extra（`torch`, `transformers`）として追加。重量級のため、`nsfw` extra同様デフォルトではインストールしない

### 適用範囲

- `sfw`/`suggestive`判定のアセットは、引き続きClaude API等のホスト型APIを使ってよい（速く、セットアップが簡単なため）
- `explicit`判定のアセットは`local`プロバイダーの使用を推奨する。ただし現時点では`generation_provider`はプロバイダー単位の設定であり、`content_rating`ごとに自動でプロバイダーを切り替える仕組みは実装していない（TODO.md参照、将来の拡張候補）

## 結果

### 良い影響

- 外部サービスの利用ポリシーに縛られず、explicit判定のコンテンツも内容説明・投稿文の自動生成対象にできる
- API課金が発生しない（電気代のみ）
- NSFW自動仕分け（Marqoモデル、ADR-0009）と同じ「ローカル推論・オプトイン依存関係」というアーキテクチャパターンを踏襲でき、一貫性がある

### 悪い影響・トレードオフ

- モデルの重み（数GB）のダウンロード・保存が必要。オフライン運用時は事前キャッシュが必要（NSFW自動仕分けと同様の制約）
- CPU推論の場合、実用速度は実機での検証が必要（未検証、TODO.md参照）
- `prithivMLmods/Qwen2-VL-2B-Abliterated-Caption-it`は個人配布のファインチューンモデルであり、Marqoモデルほど広く実績が確認されているわけではない。品質・安定性は実データでの検証が必要
- `content_rating`に応じたプロバイダーの自動切り替えは未実装。運用者が手動で設定画面を切り替える必要がある
- transformersのモデルロード・生成APIの詳細（`apply_chat_template`の挙動等）はtransformers・モデルのバージョンに依存するため、実機での動作検証が必要（Fanvueクライアント・OpenAIプロバイダーと同様、一次情報未検証の実装であることを明記する）
