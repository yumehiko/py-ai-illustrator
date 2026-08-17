# py-ai-illustrator

Adobe Illustrator の起動を前提にせず、Illustrator ファイルを Python オブジェクトとして読み取り・編集・書き出すプロジェクトです。

現在は Phase 0（技術スパイク）です。次の小さな縦切りが動作します。

- 内容に基づく legacy AI / PDF-compatible AI / PDF / EPS の形式判定
- 基本的な document / named artboard / layer / path / Bézier handle / RGB・CMYK process color の Python IR
- dash pattern・offset・cap・join・miter limitを持つnative stroke style
- 複数subpathとpolarityを保持するcompound path IR
- mask pathとcontent pathsを保持するclipping group IR
- point / area textの内容・位置・枠寸法・サイズ・色・段落揃え・tracking・行送りを持つtext IR
- AI7用font resourceとネイティブPostScript名を分離する`FontSpec`、Illustrator書体検証
- 通常path・compound・clippingの混在した描画順を保持するlayer `item_order`
- heterogeneousな要素と子groupを持つ通常group IR、入れ子の描画順
- Pythonの行データ・列・formatter・variant・共有styleから表をrenderする`Table`
- 表・名札・ポスター・棚札を同じ境界で合成する`RenderedComponent` / `LayerBuilder`
- 意味的な文字ブロック、再利用可能な文字style、矩形・Bézier楕円・polylineのauthoring primitives
- rigid affine transformによるpath・Bézier handle・text・nested groupの再配置とtext rotation
- AI7のlegacy textを現代Illustratorの編集可能なTextFrameへ変換するnative materialization
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

スタイル付き表の実装例は [examples/styled_table.py](examples/styled_table.py) です。JSONを手書きせず、行の`kind`、金額formatter、列幅、Illustratorのネイティブ段落揃え、header/body/variant配色、余白、罫線、書体要求をPythonで定義します。

日本語と長文折り返しの例は [examples/japanese_table.py](examples/japanese_table.py) です。列ごとの`wrap`、東アジア文字幅、複数行の自動行高を扱います。`FontSpec`はAI7 bridge用のCP932/RKSJ resource名と、native化後に設定するIllustrator PostScript名を分離します。

表以外の例として、[examples/conference_badges.py](examples/conference_badges.py) は参加者・役割variantから4枚の名札を合成し、[examples/event_poster.py](examples/event_poster.py) は日本語の文字階層、折り返し、装飾図形から告知ポスターを組み立てます。[examples/retail_price_tags.py](examples/retail_price_tags.py) は商品データと販売状態から6枚の棚札を作り、各棚札と価格欄を再配置可能な入れ子groupとして保持します。これらは`Table`を使わず、同じ汎用IRとcomponent境界へrenderします。

[examples/quarterly_kpi_report.py](examples/quarterly_kpi_report.py) は月次値と目標値からKPIレポートを生成します。実績polyline、目標破線、grid、data point、KPI cardを編集可能な要素として保持し、dash・round cap/joinもIllustratorのnative stroke属性へ出力します。

[examples/packaging_labels.py](examples/packaging_labels.py) は商品variantから3種のパッケージラベルを生成します。90度のside code、-12度のbadge、badge内のpathとtextを同じrigid transformで配置し、回転後も個々の要素とgroupを編集可能に保ちます。

[examples/editorial_brochure.py](examples/editorial_brochure.py) は日本語の導入文・2段本文・引用を、枠幅の変更で再流し込みできるnative AreaTextとして保持する誌面作例です。font size、色、行送り、LEFT/CENTER揃え、文章枠寸法も明示します。

[examples/campaign_variants.py](examples/campaign_variants.py) は共通キャンペーンをSquare・Portrait・Bannerの3サイズへ展開します。各variantを独立group、各出力領域を名前付きArtboardとして保持し、native AIのPDF-compatible部分も3ページになります。

```bash
uv run python examples/styled_table.py
uv run py-ai test-illustrator examples/styled-table.ai
uv run py-ai test-illustrator-roundtrip examples/styled-table.ai
uv run python examples/japanese_table.py
uv run py-ai test-illustrator-roundtrip examples/japanese-table.ai
uv run python examples/conference_badges.py
uv run python examples/event_poster.py
uv run python examples/retail_price_tags.py
uv run python examples/quarterly_kpi_report.py
uv run python examples/packaging_labels.py
uv run python examples/editorial_brochure.py
uv run python examples/campaign_variants.py

# Illustratorを使い、legacy textを編集可能なnative TextFrameへ変換
uv run py-ai illustrator-fonts --query "小塚ゴシック" \
  --require KozGoPr6N-Regular
uv run py-ai materialize-native examples/styled-table.ai \
  -o examples/styled-table.native.ai
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
patched = source.patched([SourceReplacement(line.operator_start, line.operator_end, b"L")])
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
uv run py-ai test-illustrator examples/styled-table.native.ai
uv run py-ai test-illustrator examples/conference-badges.native.ai
uv run py-ai test-illustrator examples/event-poster.native.ai
uv run py-ai test-illustrator examples/retail-price-tags.ai
uv run py-ai test-illustrator examples/retail-price-tags.native.ai
uv run py-ai test-illustrator examples/quarterly-kpi-report.ai
uv run py-ai test-illustrator examples/quarterly-kpi-report.native.ai
uv run py-ai test-illustrator examples/packaging-labels.native.ai
uv run py-ai test-illustrator examples/editorial-brochure.native.ai
uv run py-ai test-illustrator examples/campaign-variants.native.ai
```

同梱fixtureをIllustratorで開く方向に加え、Illustrator自身が作成・AI8保存したfixtureをPython IRへ読む方向も確認済みです。layer/path/anchor、開閉、塗り・線、Bézier方向点、RGB/CMYK属性、point textを照合します。これは現在の限定subsetに対する結果で、任意のAIファイルの完全互換を意味しません。

完全往復ではIllustratorによるdocument原点の移動を正規化し、RGBの8-bit量子化を許容して意味属性を比較します。pathの安定IDと名前は標準の`%AI3_Note` path属性へ埋め込み、Illustrator 30.7.0でのAI8再保存後も照合します。layer/containerのID・名前とdocument metadataはまだ比較対象外です。

legacy point textはASCIIに加え、`RKSJ-H` / `RKSJ-V` fontを明示した日本語CP932の読み書きに対応します。writerは`Ta` operator、text matrix、揃え基準のanchorを出力します。ただし現行IllustratorでAI7 textを直接開いた状態はlegacy textであり、現代のTextFrameへ変換するまで再編集できません。`materialize-native`は一時コピーだけを開いて全legacy textをnativeへ変換し、PDF-compatible AIとして保存します。変換後の各TextFrameには`py-ai-text:` noteとして安定IDと役割名を設定し、指定されたPostScript名のfont、size、fill、tracking、rotation、leading、paragraph justificationを明示的に再設定します。fontが導入されていない場合は黙って代替せず、検証を失敗させて不足名を報告します。

`AreaTextBlock`は文章枠のwidth / heightとleadingをIRへ保持します。AI7 bridgeでは互換用point textとprivate metadataとして運び、native materialization時にIllustrator DOM上で本物のAreaTextへ再構成します。現行`TextBlock`は決定的な行分割が必要な用途向けに、引き続き複数のpoint textを出力します。

回転textも、Illustrator 30.7.0でlegacy AIをAI8互換再保存すると一部がoutlineへ変換される場合があります。rotationを含む再編集可能な成果物は`materialize-native`経路を使い、font・tracking・rotation・identityの一致を検証します。

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
