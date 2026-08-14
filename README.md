# py-ai-illustrator

Adobe Illustrator の起動を前提にせず、Illustrator ファイルを Python オブジェクトとして読み取り・編集・書き出すプロジェクトです。

現在は Phase 0（技術スパイク）です。次の小さな縦切りが動作します。

- 内容に基づく legacy AI / PDF-compatible AI / PDF / EPS の形式判定
- 基本的な document / layer / path / Bézier handle / RGB・CMYK process color の Python IR
- 公開仕様に沿った Illustrator 7 互換サブセットと JSON IR の往復変換
- `inspect` / `export` / `validate` CLI
- 対応範囲の意味的 round-trip テスト

現代版 AI の意味解析と書き戻しはまだ実装していません。ファイル拡張子を変えただけの PDF を「AI writer」と呼ばず、対応範囲を明示して段階的に広げます。

## セットアップ

Python 3.11 以降と [uv](https://docs.astral.sh/uv/) を使います。

```bash
uv sync --extra dev
uv run pytest
uv run ruff check .
```

コアパッケージの実行時依存は現在ゼロです。GPL-2.0-or-later の `inkai` は先行実装の比較・検証対象ですが、コア依存には含めていません。

## 最初の往復変換

```bash
# JSON IR から Illustrator 7 互換ファイルを生成
uv run py-ai export examples/rectangle.json --to ai7 -o rectangle.ai

# コンテナと Illustrator marker を検査
uv run py-ai inspect rectangle.ai --json

# 対応サブセットを JSON IR へ戻す
uv run py-ai export rectangle.ai --to json -o rectangle.roundtrip.json

# 構造検証
uv run py-ai validate rectangle.ai
```

同じコマンドで生成した [examples/rectangle.ai](examples/rectangle.ai) も同梱しています。現行Illustratorでの適合試験にそのまま使えます。

Bézier曲線とCMYK線の入力例は [examples/cmyk-curve.json](examples/cmyk-curve.json) です。

初期 reader は、直線・3次Bézierからなる基本 path を対象にしています。compound pathやclippingを含む任意のlegacy AIを完全に読める段階ではありません。入力は上書きせず、出力先を明示してください。

## ドキュメント

- [実現可能性調査](docs/feasibility.md)
- [推奨アーキテクチャ](docs/architecture.md)
- [開発ロードマップ](docs/roadmap.md)
- [調査ソース](docs/sources.md)
- [Phase 0 の実装状況](docs/phase0-status.md)

## 暫定結論

限定した機能セットなら実現可能です。

- `.ai` の読み取りは、現代形式の Illustrator Private Data まで扱う先行 OSS があり、十分に着手可能です。
- Illustrator で開けるファイルの新規生成は、公開仕様のあるレガシー AI/EPS、または PDF/SVG を入口にすれば実現できます。
- 現代版 `.ai` のネイティブ編集情報を完全に相互変換するには、非公開の内部形式、PDF 表現との二重管理、フォント・効果・カラーマネジメントへの対応が必要です。これは長期的な互換性プロジェクトです。

推奨する最初の製品は「対応範囲を明示した Python IR + 読み取り + AI8/PDF/SVG 書き出し + 検証 CLI」です。その後、現代版 AI Private Data の書き戻しを実験機能として追加します。

## ライセンス

[MIT License](LICENSE) で公開しています。GPLコードをコアへコピーせず、公開仕様と自作fixtureに基づく実装を維持します。GPL依存を将来追加する場合も、任意の隔離adapterとして扱います。
