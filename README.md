# py-ai-illustrator

Adobe Illustrator の起動を前提にせず、Illustrator ファイルを Python オブジェクトとして読み取り・編集・書き出すプロジェクトです。

現在は Phase 0（技術スパイク）です。次の小さな縦切りが動作します。

- 内容に基づく legacy AI / PDF-compatible AI / PDF / EPS の形式判定
- 基本的な document / layer / path / Bézier handle / RGB・CMYK process color の Python IR
- 複数subpathとpolarityを保持するcompound path IR
- mask pathとcontent pathsを保持するclipping group IR
- editable point textの内容・位置・サイズ・色・整列要求を持つtext IR
- 通常path・compound・clippingの混在した描画順を保持するlayer `item_order`
- Pythonの行データ・列・formatter・variant・共有styleから表をrenderする`Table`
- 未知行や非UTF-8 byteを変更せず、物理行のbyte spanを索引化するlossless source prototype
- 公開仕様に沿った Illustrator 7 互換サブセットと JSON IR の往復変換
- `inspect` / `export` / `validate` / `test-illustrator` CLI
- 対応範囲の意味的 round-trip テスト
- Illustrator 30.7.0との双方向fixture実機適合試験
- Python生成AIをIllustratorで再保存してPython IRへ戻す完全往復試験

現代版 AI の意味解析と書き戻しはまだ実装していません。ファイル拡張子を変えただけの PDF を「AI writer」と呼ばず、対応範囲を明示して段階的に広げます。

## オーサリング方針

JSONはIllustratorファイルを作るための唯一の記述言語ではありません。複雑な制作物では、Pythonのcomponentやtemplateが意味・規則・再利用可能な体裁を表現し、JSON化可能なグラフィックIRへrenderします。JSON IRはfixture、debug、semantic diff、言語間交換のための中間表現です。

パラメトリック制作ではPython sourceと入力データ、既存ファイル編集では元の`.ai`をsource of truthとします。また、geometry等を保つ「グラフィック往復」と、表や商品カードの役割まで保つ「意味の往復」を区別します。詳細は[オーサリングモデル](docs/authoring-model.md)を参照してください。

スタイル付き表の実装例は [examples/styled_table.py](examples/styled_table.py) です。JSONを手書きせず、行の`kind`、金額formatter、列幅、整列、header/body/variant配色、余白、罫線、書体要求をPythonで定義します。

日本語と長文折り返しの例は [examples/japanese_table.py](examples/japanese_table.py) です。列ごとの`wrap`、東アジア文字幅、複数行の自動行高、CP932/RKSJ font resourceを使います。

```bash
uv run python examples/styled_table.py
uv run py-ai test-illustrator examples/styled-table.ai
uv run py-ai test-illustrator-roundtrip examples/styled-table.ai
uv run python examples/japanese_table.py
uv run py-ai test-illustrator-roundtrip examples/japanese-table.ai
```

## セットアップ

Python 3.11 以降と [uv](https://docs.astral.sh/uv/) を使います。

```bash
uv sync --extra dev
uv run pytest
uv run ruff check .
```

コアパッケージの実行時依存は現在ゼロです。GPL-2.0-or-later の `inkai` は先行実装の比較・検証対象ですが、コア依存には含めていません。

未知operatorを保持する次段階の基盤として、元bytesと改行をそのまま持つ読み取り専用source mapを公開しています。

```python
from pathlib import Path

from py_ai_illustrator import SourceReplacement, tokenize_legacy

data = Path("input.ai").read_bytes()
source = tokenize_legacy(data)
assert source.to_bytes() == data

for line in source.lines:
    print(line.line_number, line.kind, source.operator(line))

# 既知operatorのspanだけを置換し、その他のbytesと改行は保持する低レベル例
line = next(line for line in source.lines if source.operator(line) == b"m")
assert line.operator_start is not None and line.operator_end is not None
patched = source.patched(
    [SourceReplacement(line.operator_start, line.operator_end, b"L")]
)
```

既定では入力64 MiB、1行8 MiB、200万行を上限とし、超過時は`SourceLimitExceeded`を返します。`patched()`は範囲外・重複spanを拒否しますが、operatorの意味検証を行わない低レベルprimitiveです。IR編集を安全なspan変更へ変換する高レベルpatch writerは未実装です。

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

Creative CloudへのログインとIllustratorの初回起動が完了したmacOSでは、実アプリによる構造検査も実行できます。

```bash
uv run py-ai test-illustrator examples/rectangle.ai
uv run py-ai test-illustrator examples/cmyk-curve.ai
uv run py-ai test-illustrator-export --fixture rgb-rectangle
uv run py-ai test-illustrator-export --fixture cmyk-curve
uv run py-ai test-illustrator-roundtrip examples/rectangle.ai
uv run py-ai test-illustrator-roundtrip examples/cmyk-curve.ai
uv run py-ai test-illustrator examples/compound-path.ai
uv run py-ai test-illustrator examples/clipping-group.ai
uv run py-ai test-illustrator examples/mixed-stack.ai
uv run py-ai test-illustrator-roundtrip examples/mixed-stack.ai
uv run py-ai test-illustrator-export --fixture point-text
uv run py-ai test-illustrator-export --fixture unicode-text
uv run py-ai test-illustrator examples/styled-table.ai
uv run py-ai test-illustrator-roundtrip examples/styled-table.ai
uv run py-ai test-illustrator examples/japanese-table.ai
uv run py-ai test-illustrator-roundtrip examples/japanese-table.ai
```

同梱fixtureをIllustratorで開く方向に加え、Illustrator自身が作成・AI8保存したfixtureをPython IRへ読む方向も確認済みです。layer/path/anchor、開閉、塗り・線、Bézier方向点、RGB/CMYK属性、point textを照合します。これは現在の限定subsetに対する結果で、任意のAIファイルの完全互換を意味しません。

完全往復ではIllustratorによるdocument原点の移動を正規化し、RGBの8-bit量子化を許容して意味属性を比較します。pathの安定IDと名前は標準の`%AI3_Note` path属性へ埋め込み、Illustrator 30.7.0でのAI8再保存後も照合します。layer/containerのID・名前とdocument metadataはまだ比較対象外です。

legacy point textはASCIIに加え、`RKSJ-H` / `RKSJ-V` fontを明示した日本語CP932の読み書きに対応します。内容・サイズ・色・配置はIllustrator再保存後も保持されます。font環境による置換と、point textのparagraph alignmentがleftへ正規化される場合があるため、この2項目はadvisoryです。表rendererは中央・右揃えを文字原点へ展開し、見た目の配置を保持します。

初期 reader は、直線・3次Bézierからなる基本 path を対象にしています。compound pathやclippingを含む任意のlegacy AIを完全に読める段階ではありません。入力は上書きせず、出力先を明示してください。

## ドキュメント

- [実現可能性調査](docs/feasibility.md)
- [推奨アーキテクチャ](docs/architecture.md)
- [オーサリングモデル](docs/authoring-model.md)
- [開発ロードマップ](docs/roadmap.md)
- [調査ソース](docs/sources.md)
- [Phase 0 の実装状況](docs/phase0-status.md)
- [Illustrator 適合試験](docs/illustrator-testing.md)

## 暫定結論

限定した機能セットなら実現可能です。

- `.ai` の読み取りは、現代形式の Illustrator Private Data まで扱う先行 OSS があり、十分に着手可能です。
- Illustrator で開けるファイルの新規生成は、公開仕様のあるレガシー AI/EPS、または PDF/SVG を入口にすれば実現できます。
- 現代版 `.ai` のネイティブ編集情報を完全に相互変換するには、非公開の内部形式、PDF 表現との二重管理、フォント・効果・カラーマネジメントへの対応が必要です。これは長期的な互換性プロジェクトです。

推奨する最初の製品は「対応範囲を明示した Python IR + 読み取り + AI8/PDF/SVG 書き出し + 検証 CLI」です。その後、現代版 AI Private Data の書き戻しを実験機能として追加します。

## ライセンス

[MIT License](LICENSE) で公開しています。GPLコードをコアへコピーせず、公開仕様と自作fixtureに基づく実装を維持します。GPL依存を将来追加する場合も、任意の隔離adapterとして扱います。
