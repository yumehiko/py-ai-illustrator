# py-ai-illustrator

Adobe Illustratorの起動を前提にせず、`.ai`とPythonの間でデザインデータを扱うためのプロジェクトです。

## 三つの層

1. **変換層**: `.ai`と低水準Python IRを相互変換・編集・検証する
2. **デザインモデル層**: 意図、規則、再利用可能な体裁を持つPythonモデルを作る
3. **エージェント層**: 自然言語と素材から第二層のモデルを生成・改訂する

現在は第一層のlegacy相互変換とmodern read-only semantic profile v2、第二層のcomponent authoring MVPが動作します。第三層は未着手です。現在地と次の作業は[ロードマップ](docs/roadmap.md)を参照してください。

## 現在の主な機能

- legacy AI7/AI8 subsetとPython IRの意味往復
- 元bytes、source span、未知operatorを保持するlossless reader
- path / text / image / containerのtyped local patch
- `inspect` / `plan` / `apply` / `validate` / semantic `diff` CLI
- modern AI PrivateDataのbounded抽出、exact-span CST、read-only IR投影
- layer / group / path / compound / clipping、RGB / CMYK、Bézier、partial AI11 text
- Table、text、group、Artboard、linked image等のPython component authoring
- Illustrator 30.7.0によるfixture適合試験とnative materialization

modern AIの書き戻し、PDF表示との同期、共通preview / visual diffは未実装です。読めたことを安全に再保存できることとして扱いません。

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

# legacy AIをJSON IRへ変換
uv run py-ai export input.ai --to json -o document.json

# JSON IRからAI7 subsetを生成
uv run py-ai export document.json --to ai7 -o output.ai

# 既存legacy AIの安全編集
uv run py-ai plan input.ai operations.json
uv run py-ai apply input.ai operations.json -o output.ai
uv run py-ai diff input.ai output.ai --semantic
```

operationは任意byte replacementではなく、`type + id` selectorを持つ検証可能なschemaです。入力と既存出力を既定で上書きせず、曖昧なselector、stale source、未対応領域との交差、想定外diffでは停止します。schemaの正本は[operation-schema.json](docs/operation-schema.json)です。

Pythonでの新規制作例は`examples/`にあります。

```bash
uv run python examples/styled_table.py
uv run python examples/campaign_variants.py
uv run python examples/product_catalog.py
```

## Modern AI

PDF-compatible AIではPDF表示表現とIllustratorのPrivateDataを分けて扱います。現行profileはPrivateDataを変更せず抽出・索引化し、証明できる構造だけをread-only IRへ投影します。

```python
from py_ai_illustrator import read_modern_ai

result = read_modern_ai("input.ai")
print(result.container_status)
print(result.private_data_status)
print(result.semantic_status)
print(result.semantic.coverage.to_dict() if result.semantic else None)
```

正確な対応範囲とresource limitは[modern read profile](docs/modern-ai-read-profile.md)を参照してください。

## ドキュメント

文書は重複とトークン消費を抑えるため、役割ごとの正本だけを維持します。

- [開発原則・ユースケース・文書方針](docs/development-principles.md)
- [ロードマップ](docs/roadmap.md)
- [アーキテクチャ](docs/architecture.md)
- [デザインモデル層](docs/authoring-model.md)
- [legacy feature profile](docs/legacy-feature-profile.md)
- [modern read profile](docs/modern-ai-read-profile.md)
- [Illustrator適合試験](docs/illustrator-testing.md)
- [ライセンス方針](docs/license-policy.md)
- [ADR 0001: modern semantic reader](docs/adr/0001-modern-semantic-reader-strategy.md)

調査出典は[Sources](docs/sources.md)へ集約しています。過去の進捗は別文書へ複製せずGit履歴を参照します。

## ライセンス

[MIT License](LICENSE)です。modern semantic readerはproject-owned実装とし、GPLの`inkai`は製品や開発依存へ含めず、任意の隔離comparison oracleに限定します。
