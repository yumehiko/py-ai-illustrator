# py-ai-illustrator

Adobe Illustratorの起動を前提にせず、`.ai`と低水準Python IRの間を安全に変換・編集・検証する第1層のリポジトリです。

デザインcomponentとエージェントworkflowは、兄弟リポジトリ`illustrator-agent`へ分離しました。このリポジトリへの機能追加は、上位層の具体的な要求、再現fixture、保証条件が揃った場合に行います。

## 責務

- legacy AI7/AI8 subsetとPython IRの意味往復
- 元bytes、source span、未知operatorを保持するlossless reader
- path / text / image / containerのtyped local patch
- `inspect` / `plan` / `apply` / `validate` / semantic `diff` CLI
- modern AI PrivateDataのbounded抽出、exact-span CST、read-only IR投影
- modern AIの証拠付きfill / stroke / rectangle移動 / 一意text同期patch
- PDF表示証拠、preview、pixel visual diff
- Illustrator実機によるfixture適合試験とnative materialization

第一層v1はIllustrator 30.7.0の最終matrixを含めて完了しています。任意のmodern AI再保存やIllustratorの全機能の往復は保証しません。対応範囲はprofile単位で明示し、未対応情報を成功扱いで捨てないことを優先します。

`examples/`の生成済み`.ai`と`.native.ai`は、第2層の実装ではなく第1層の互換性回帰fixtureとして残しています。

## セットアップ

Python 3.11以降と[uv](https://docs.astral.sh/uv/)を使います。

```bash
uv sync --extra dev
uv run pytest
uv run ruff check .
```

## 基本操作

```bash
# 形式・互換性・semantic coverageを確認
uv run py-ai inspect input.ai --json
uv run py-ai validate input.ai

# legacy AIとJSON IRを相互変換
uv run py-ai export input.ai --to json -o document.json
uv run py-ai export document.json --to ai7 -o output.ai

# 既存AIの安全編集
uv run py-ai plan input.ai operations.json
uv run py-ai apply input.ai operations.json -o output.ai
uv run py-ai diff input.ai output.ai --semantic

# previewとvisual diff
uv run py-ai preview input.ai -o preview.png
uv run py-ai diff before.ai after.ai --visual -o visual-diff.png
```

operationはtypeとid / name / bounds / hierarchyを組み合わせた検証可能なselectorを使います。入力を既定で上書きせず、曖昧なselector、stale source、未対応領域との交差、想定外diffでは停止します。schemaの正本は[operation-schema.json](docs/operation-schema.json)です。

## ドキュメント

- [開発原則](docs/development-principles.md)
- [ロードマップ](docs/roadmap.md)
- [アーキテクチャ](docs/architecture.md)
- [legacy feature profile](docs/legacy-feature-profile.md)
- [modern read profile](docs/modern-ai-read-profile.md)
- [modern synchronized patch profile](docs/modern-ai-write-profile.md)
- [Illustrator適合試験](docs/illustrator-testing.md)
- [ライセンス方針](docs/license-policy.md)
- [ADR 0001: modern semantic reader](docs/adr/0001-modern-semantic-reader-strategy.md)

## ライセンス

[MIT License](LICENSE)です。
