# 0015. 画像内容説明・Fanvue投稿文生成にAI(Claude API優先・OpenAI選択可)を採用する

- ステータス: 決定
- 決定日: 2026-09-26
- 決定者: プロジェクトオーナー

## コンテキストと課題

Fanvue投稿文（キャプション）は、これまで`caption`/`fanvue_text`（sidecarでの手動指定、または空欄）のみで構成されていた。プロジェクトオーナーより、以下の要望があった。

1. 投稿文は画像の内容（何が写っているか、服装、表情など）に合わせて、システムプロンプトの設定により生成したい
2. 内容説明はタグとは別に保管し、投稿時などに活用できるようにしたい。取得はNSFWチェッカーと合わせて定期バッチ処理（`ingest`/`analyze`）に組み込む
3. 内容説明の取得完了を含めて`ready`状態とする。未完了のアセットは投稿対象にしない
4. システムプロンプトは初期値を持ちつつ、本体UIの設定画面で調整できるようにする
5. 投稿時に指定したい「画像/動画の枚数・レーティング等のオプション」を、CLI・定期実行の両方で指定できるようにする

## 検討した選択肢（生成AIプロバイダー）

| 選択肢 | 特徴 |
|---|---|
| Claude API（Anthropic公式） | Vision対応、画像解析と文章生成を同一APIで完結。ANTHROPIC_API_KEYが必要、画像1枚ごとに課金 |
| OpenAI API（gpt-4o系） | 同等のVision対応。別ベンダーのAPIキー・SDKが必要 |
| AWS Bedrock（Claude経由） | AWSへのデプロイ時にAWS内で完結させたい場合の選択肢。今回は未実装 |

## 決定

### プロバイダー抽象化

`core/generation.py`に生成AIを抽象化する`Generator`（`ClaudeGenerator`/`OpenAiGenerator`）を実装し、`config`で選択可能にする。

- 既定（初期実装）: **Claude API**（`claude-opus-5`、Vision対応）。`ANTHROPIC_API_KEY`が必要
- オプション: **OpenAI API**（`gpt-4o`系）。`OPENAI_API_KEY`が必要。実API疎通は未検証（[TODO.md](../../TODO.md)参照、Fanvueクライアントと同様の位置づけ）
- 将来: **AWS Bedrock**（AnthropicモデルをBedrock経由で呼ぶ）。AWSへのデプロイを行う場合に追加する。今回は未実装、`provider`の選択肢として`bedrock`を予約するのみ

プロバイダー・モデル・システムプロンプト・キャプション生成モード（後述）は`config.yaml`ではなくSQLiteの`settings`テーブルに保持し、本体UIの設定画面（`/settings`）から再デプロイなしに変更できるようにする。初期値はコード上の定数として持ち、`settings`テーブルに値がなければそれを使う。APIキーは秘密情報のため引き続き`.env`のみに置く。

### READY状態の条件変更（重要な方針転換）

これまで`ingest`はNSFW自動仕分けの成否に関わらず即座に`status="ready"`としていた（NSFW機能はオプション、[ADR-0009](0009-nsfw-classifier-marqo.md)）。本決定により、**NSFW自動仕分け（`nsfw_auto_rating`）と内容説明（`content_description`）の両方が取得できて初めて`status="ready"`とする**。どちらか一方でも未取得・失敗の場合は`status="analyzing"`のまま残す。

この結果、`torch`/`timm`（NSFW）または生成AIのAPIキー（内容説明）のいずれかを設定していない環境では、投稿対象（`ready`）となるアセットが作られなくなる。README等で「本体機能だけでも動作する」としていた説明を、「一覧・タグ・フォルダ管理は`analyzing`状態のアセットに対しても可能だが、Fanvue自動投稿の対象にはならない」という形に更新する。

`analyzing`のまま残ったアセットは、`reelmilly analyze`コマンド（新規）で再分析を試みる。`reelmilly watch`（[ADR未採番、TODO.md「自動実行スケジューラ」参照]）のループにも組み込み、定期的に再試行する。

動画の内容説明は、`opencv-python`で代表フレーム（中間フレーム）を1枚抽出し、そのフレームを画像として解析する簡易実装とする。NSFW仕分けのような複数フレームサンプリングは行わない（画像解析APIの呼び出しコストを抑えるため）。精度は実データでの検証が必要（TODO.md）。

### Fanvue投稿文（キャプション）の生成とdraftモード

`run_fanvue_drop`実行時、アセットに`fanvue_text`（手動指定分）が未設定であれば、`content_description`とキャプション生成用システムプロンプトを使い、生成AIで投稿文を生成する。生成結果の扱いは`caption_mode`設定（既定`auto`）で切り替える。

- `auto`（既定）: 生成した投稿文をそのまま`fanvue_text`として使い、即座に投稿する
- `draft`: 生成した投稿文は`fanvue_caption_draft`列に保存するのみとし、そのアセットは今回の投稿対象から外す（`fanvue_text`は空のまま）。本体UIで内容を確認し、「採用する」操作で`fanvue_text`にコピーしてから次回の投稿で使う

`auto`が既定である点は、プロジェクトオーナーの意向（「基本は自動投稿だが、下書き保存オプションを設ける」）に基づく。生成AIの誤り・不適切な文面がそのまま投稿されるリスクは`draft`モードへの切り替えで軽減できる。

### 投稿オプションの指定方法

「画像/動画の枚数」「レーティング」等の投稿条件は、CLIオプションと`config.yaml`の`cadence`設定の両方で指定できるようにする。

```bash
reelmilly run drop --count 3 --kind image --rating sfw
```

```yaml
cadence:
  drop:
    time: "21:00"
    count: 3
    kind: image
    rating: sfw
```

`cadence`の値は後方互換のため文字列（時刻のみ）も許容する。`count`は1回の実行で投稿を試みる最大件数、`kind`は`image`/`video`、`rating`は`content_rating`（`sfw`/`suggestive`/`explicit`）でのフィルタ。いずれも省略時は現状どおり（1件・フィルタなし）。

## 結果

### 良い影響

- 投稿文が画像内容に即したものになり、手動でのキャプション作成の手間が減る
- 内容説明はタグ付け・検索性向上等、投稿以外の用途にも今後活用できる
- システムプロンプトを本体UIから調整でき、運用しながらチューニングできる

### 悪い影響・トレードオフ

- 画像1枚ごとに生成AIのAPI課金が発生する（Claude API/OpenAI APIいずれも従量課金）
- NSFW自動仕分けに加えて生成AIの設定も必須になったことで、「本体だけで完結する」という従来の位置づけが崩れる。両方未設定の環境では、取り込んだアセットが`ready`にならず投稿対象を作れない
- OpenAI APIプロバイダーは実API疎通を検証しておらず、Fanvueクライアントと同様に実行時に調整が必要になる可能性がある
- 動画の内容説明は代表フレーム1枚のみを見るため、動画全体の内容を反映しきれない場合がある
