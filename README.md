# py-ai-illustrator

Adobe Illustrator の起動を前提にせず、Illustrator ファイルを Python オブジェクトとして読み取り・編集・書き出すプロジェクトです。

Gate AのTrusted Legacy Conversion、Gate B / C1の安全編集CLI最小縦切り、A2のmodern AI read-only抽出縦切りが動作します。

- 内容に基づく legacy AI / PDF-compatible AI / PDF / EPS の形式判定
- bounded PDF object readerによる`PieceInfo / Illustrator / PrivateData`参照解決
- modern AI PrivateDataのsegment順序・raw span・filter・raw/decoded SHA-256保持
- Flate / Illustrator zstd streamの上限制御付き展開とlossless token/section索引
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
- PNG/JPEGを`Links/`へ安全にpackageし、Illustratorのlinked `PlacedItem`へ復元する画像IR
- AI7のlegacy textを現代Illustratorの編集可能なTextFrameへ変換するnative materialization
- 未知行や非UTF-8 byteを変更せず、物理行のbyte spanを索引化するlossless source prototype
- `document + source + coverage + diagnostics`を返すlegacy reader resultとoperator/resource互換性レポート
- 未対応source featureを含むIR再serializeを既定で拒否する明示的loss policy
- 公開仕様に沿った Illustrator 7 互換サブセットと JSON IR の往復変換
- `inspect` / `plan` / `apply` / `validate` / semantic `diff` CLI
- `type + id` selector、request schema、dry-run impact report、semantic impact検証
- 対応範囲の意味的 round-trip テスト
- Illustrator 30.7.0との双方向fixture実機適合試験
- Python生成AIをIllustratorで再保存してPython IRへ戻す完全往復試験

現代版 AI はPrivateDataのread-only抽出・索引化まで対応し、Document IRへの意味投影と書き戻しはまだ実装していません。ファイル拡張子を変えただけの PDF を「AI writer」と呼ばず、対応範囲を明示して段階的に広げます。

## オーサリング方針

JSONはIllustratorファイルを作るための唯一の記述言語ではありません。複雑な制作物では、Pythonのcomponentやtemplateが意味・規則・再利用可能な体裁を表現し、JSON化可能なグラフィックIRへrenderします。JSON IRはfixture、debug、semantic diff、言語間交換のための中間表現です。

代表的な利用シナリオ、UX上の原則、pre-1.0ではpublic APIの後方互換を保証しない方針は[開発原則と想定ユースケース](docs/development-principles.md)に定義します。

legacy reader/writer/patchの正確な対応範囲、保証、変換policy、検証済みIllustrator versionは[Trusted Legacy Conversion feature profile](docs/legacy-feature-profile.md)に定義します。

パラメトリック制作ではPython sourceと入力データ、既存ファイル編集では元の`.ai`をsource of truthとします。また、geometry等を保つ「グラフィック往復」と、表や商品カードの役割まで保つ「意味の往復」を区別します。詳細は[オーサリングモデル](docs/authoring-model.md)を参照してください。

スタイル付き表の実装例は [examples/styled_table.py](examples/styled_table.py) です。JSONを手書きせず、行の`kind`、金額formatter、列幅、Illustratorのネイティブ段落揃え、header/body/variant配色、余白、罫線、書体要求をPythonで定義します。

日本語と長文折り返しの例は [examples/japanese_table.py](examples/japanese_table.py) です。列ごとの`wrap`、東アジア文字幅、複数行の自動行高を扱います。`FontSpec`はAI7 bridge用のCP932/RKSJ resource名と、native化後に設定するIllustrator PostScript名を分離します。

表以外の例として、[examples/conference_badges.py](examples/conference_badges.py) は参加者・役割variantから4枚の名札を合成し、[examples/event_poster.py](examples/event_poster.py) は日本語の文字階層、折り返し、装飾図形から告知ポスターを組み立てます。[examples/retail_price_tags.py](examples/retail_price_tags.py) は商品データと販売状態から6枚の棚札を作り、各棚札と価格欄を再配置可能な入れ子groupとして保持します。これらは`Table`を使わず、同じ汎用IRとcomponent境界へrenderします。

[examples/quarterly_kpi_report.py](examples/quarterly_kpi_report.py) は月次値と目標値からKPIレポートを生成します。実績polyline、目標破線、grid、data point、KPI cardを編集可能な要素として保持し、dash・round cap/joinもIllustratorのnative stroke属性へ出力します。

[examples/packaging_labels.py](examples/packaging_labels.py) は商品variantから3種のパッケージラベルを生成します。90度のside code、-12度のbadge、badge内のpathとtextを同じrigid transformで配置し、回転後も個々の要素とgroupを編集可能に保ちます。

[examples/editorial_brochure.py](examples/editorial_brochure.py) は日本語の導入文・2段本文・引用を、枠幅の変更で再流し込みできるnative AreaTextとして保持する誌面作例です。font size、色、行送り、LEFT/CENTER揃え、文章枠寸法も明示します。

[examples/campaign_variants.py](examples/campaign_variants.py) は共通キャンペーンをSquare・Portrait・Bannerの3サイズへ展開します。各variantを独立group、各出力領域を名前付きArtboardとして保持し、native AIのPDF-compatible部分も3ページになります。

[examples/product_catalog.py](examples/product_catalog.py) は、リンク画像、ポイントテキスト、再流し込み可能なエリアテキスト、ベクター図形を一枚の商品カードへ合成します。画像は生成AIと同じ場所の`Links/`に置かれ、native AIでも埋め込まず`PlacedItem`として保持されます。

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
uv run python examples/product_catalog.py

# Illustratorを使い、legacy textを編集可能なnative TextFrameへ変換
uv run py-ai illustrator-fonts --query "小塚ゴシック" \
  --require KozGoPr6N-Regular
uv run py-ai materialize-native examples/styled-table.ai \
  -o examples/styled-table.native.ai
uv run py-ai materialize-native examples/product-catalog.ai \
  -o examples/product-catalog.native.ai
```

## セットアップ

Python 3.11 以降と [uv](https://docs.astral.sh/uv/) を使います。

```bash
uv sync --extra dev
uv run pytest
uv run ruff check .
```

modern AIのzstd展開にはBSDライセンスの`zstandard` packageを使用します。GPL-2.0-or-later の `inkai` は先行実装の比較・検証対象ですが、コア依存、fixture、テストには含めていません。

## Modern AIのread-only診断

PDF-compatible modern AIでは、PDF表示内容とIllustratorの編集用PrivateDataを別物として扱います。`inspect`と`validate`は、container読取、PrivateData抽出、semantic対応を独立したJSON fieldで返します。

```bash
uv run py-ai inspect input.ai --json
uv run py-ai validate input.ai
```

```python
from py_ai_illustrator import read_modern_ai

result = read_modern_ai("input.ai")
print(result.container_status)       # parsed
print(result.private_data_status)    # extracted / absent / partial / failed
print(result.semantic_status)        # unsupported

for segment in result.segments:
    print(segment.key, segment.filters)
    print(segment.raw_start, segment.raw_end, segment.raw_sha256)
    print(segment.decoded_sha256, len(segment.tokens), len(segment.sections))
```

抽出成功時も`safe_to_reserialize`は`false`です。通常PDFは`ordinary_pdf`、PrivateDataまで読めたAIは`read_only_private_data`、参照・filter・展開の失敗は`unconvertible`として区別します。対応PDF構造、filter、resource limit、fixture manifestの詳細は[Modern AI read-only feature profile](docs/modern-ai-read-profile.md)を参照してください。

## 既存legacy AIの安全編集CLI

公開operation schemaは [docs/operation-schema.json](docs/operation-schema.json) です。現在のselector保証は`type + id`だけで、0件・複数件、またはoperationとtarget typeが一致しない場合は停止します。Path全点、現在色、text本文、container member集合などの内部preconditionはJSONへ手書きせず、plannerが現在のIRから導出します。

```json
{
  "schema_version": 1,
  "operations": [
    {
      "op": "replace_text",
      "selector": {"type": "text", "id": "headline"},
      "text": "New heading"
    },
    {
      "op": "set_fill",
      "selector": {"type": "path", "id": "logo-mark"},
      "color": {"red": 1.0, "green": 0.3, "blue": 0.0}
    },
    {
      "op": "translate",
      "selector": {"type": "group", "id": "cta"},
      "dx": 12,
      "dy": 0
    },
    {
      "op": "replace_linked_image_source",
      "selector": {"type": "linked_image", "id": "hero"},
      "source": "Links/hero-new.png"
    }
  ]
}
```

```bash
# exact selector候補とcompatibilityを確認
uv run py-ai inspect input.ai --json

# sourceは変更せず、解決target、before/after、precondition、span、想定diffをJSON出力
uv run py-ai plan input.ai operations.json

# 入力と既存出力は上書きしない。全操作をatomicに適用して再読込・意味検証
uv run py-ai apply input.ai operations.json -o output.ai
uv run py-ai validate output.ai
uv run py-ai diff input.ai output.ai --semantic
```

`operations.json`には任意byte replacementを指定できません。必要ならplanが返す`source_sha256`をmanifestの同名fieldへ入れ、後続applyでstale inputを拒否できます。applyはreplacement span外のbyte一致と、操作ごとに許可したfieldだけがsemantic diffへ現れることを検証してから成功を返します。preview / visual diffと、name・bounds・hierarchy selectorはGate Bの残項目です。

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

既存legacy AIを意味IRへ読む場合は、元sourceと解析coverageを分離せずに扱えます。

```python
from py_ai_illustrator.legacy import read_ai7, reserialize_ai7

result = read_ai7("input.ai")
print(result.compatibility_report())

# 未対応operator/resourceがあれば例外。破棄を許す場合だけloss policyを明示する
output = reserialize_ai7(result)
# output = reserialize_ai7(result, loss_policy="discard")
```

`py-ai export input.ai --to json`も既定では部分解析結果を拒否します。診断を確認したうえで
意味IRに含まれないfeatureの破棄を許す場合のみ、`--allow-partial`を指定します。

既存のfill / stroke operatorを一つのpathだけが使用している場合、path geometryを既知statement
として列挙できる場合、`TextFrame`の本文が単一の`Tx` statementで表現されている場合、
linked imageのprivate metadataを保持している場合、またはcontainer内のleaf fieldをすべて
排他的spanへ解決できる場合は、
typed operationから局所patchを生成できます。IDが0件・
複数件、意味/source precondition不一致、または編集対象を排他的なspanに限定できない場合は停止します。

```python
from pathlib import Path

from py_ai_illustrator import (
    Color,
    ReplaceLinkedImageSource,
    ReplaceText,
    SetPathFill,
    SetPathStroke,
    TranslatePath,
    patch_linked_image_source,
    patch_path_fill,
    patch_path_stroke,
    patch_path_translate,
    patch_text,
    read_ai7,
)

result = read_ai7("input.ai")
patched = patch_path_fill(
    result,
    SetPathFill(
        path_id="logo-shape",
        expected_fill=Color(1, 0, 0),
        fill=Color(0, 0.4, 1),
    ),
)
Path("output.ai").write_bytes(patched.to_bytes())

stroke_patched = patch_path_stroke(
    result,
    SetPathStroke(
        path_id="rule",
        expected_stroke=Color(0, 0, 0),
        stroke=Color(1, 0.2, 0),
    ),
)
Path("stroke-output.ai").write_bytes(stroke_patched.to_bytes())

path = next(
    path
    for layer in result.document.layers
    for path in layer.paths
    if path.id == "logo-shape"
)
translated = patch_path_translate(
    result,
    TranslatePath(
        path_id="logo-shape",
        dx=12,
        dy=0,
        expected_points=tuple(path.points),
    ),
)
Path("translated-output.ai").write_bytes(translated.to_bytes())

text_patched = patch_text(
    result,
    ReplaceText(
        text_id="headline",
        expected_text="Old title",
        text="New title",
    ),
)
Path("text-output.ai").write_bytes(text_patched.to_bytes())

image_patched = patch_linked_image_source(
    result,
    ReplaceLinkedImageSource(
        image_id="hero-photo",
        expected_source="Links/hero.png",
        source="Links/replacement.png",
    ),
)
Path("image-output.ai").write_bytes(image_patched.to_bytes())
```

複数操作は`plan_legacy_patch()`で一つのatomic planにでき、元source全体のSHA-256とoperation間のspan競合をapply前に検証します。これらのpatchはfill、stroke、geometry、textまたはimage metadata field spanだけを差し替え、対象spanの前後にある改行、未知
operator、非UTF-8 byteをそのまま保持します。textは既存fontのASCII / CP932 profileで再encode
します。画像source差し替えはリンク先の存在確認やassetコピーを行いません。現時点ではfill / stroke追加、共有color stateの分離、styled/複数`Tx`本文patchは未実装です。container一括移動はLayer / Group / CompoundPath / ClippingGroupに対応します。

既定では入力64 MiB、1行8 MiB、200万行を上限とし、超過時は`SourceLimitExceeded`を返します。`patched()`は範囲外・重複spanを拒否しますが、operatorの意味検証を行わない低レベルprimitiveです。IR編集では`SetPathFill` / `SetPathStroke` / `TranslatePath` / `ReplaceText` / `ReplaceLinkedImageSource`の高レベルpatch APIを使用します。

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
uv run py-ai test-illustrator examples/product-catalog.native.ai
```

同梱fixtureをIllustratorで開く方向に加え、Illustrator自身が作成・AI8保存したfixtureをPython IRへ読む方向も確認済みです。layer/path/anchor、開閉、塗り・線、Bézier方向点、RGB/CMYK属性、point textを照合します。これは現在の限定subsetに対する結果で、任意のAIファイルの完全互換を意味しません。

完全往復ではIllustratorによるdocument原点の移動を正規化し、RGBの8-bit量子化を許容して意味属性を比較します。pathの安定IDと名前は標準の`%AI3_Note` path属性へ埋め込み、Illustrator 30.7.0でのAI8再保存後も照合します。layer/containerのID・名前とdocument metadataはまだ比較対象外です。

legacy point textはASCIIに加え、`RKSJ-H` / `RKSJ-V` fontを明示した日本語CP932の読み書きに対応します。writerは`Ta` operator、text matrix、揃え基準のanchorを出力します。ただし現行IllustratorでAI7 textを直接開いた状態はlegacy textであり、現代のTextFrameへ変換するまで再編集できません。`materialize-native`は一時コピーだけを開いて全legacy textをnativeへ変換し、PDF-compatible AIとして保存します。変換後の各TextFrameには`py-ai-text:` noteとして安定IDと役割名を設定し、指定されたPostScript名のfont、size、fill、tracking、rotation、leading、paragraph justificationを明示的に再設定します。fontが導入されていない場合は黙って代替せず、検証を失敗させて不足名を報告します。

`AreaTextBlock`は文章枠のwidth / heightとleadingをIRへ保持します。AI7 bridgeでは互換用point textとprivate metadataとして運び、native materialization時にIllustrator DOM上で本物のAreaTextへ再構成します。現行`TextBlock`は決定的な行分割が必要な用途向けに、引き続き複数のpoint textを出力します。

`LinkedImage`はPNG/JPEGの外部参照と配置寸法を保持します。`dump_ai7()`は成果物の隣に`Links/`を作り、同一内容の同名ファイルはSHA-256照合後に再利用します。同名でも内容が異なる場合は既存ファイルを上書きせず、内容hash付きの名前でコピーします。`materialize-native`はAI7上の非表示placeholderを同じ階層・描画順のlinked `PlacedItem`へ置換し、`embedLinkedFiles = false`で保存します。

回転textも、Illustrator 30.7.0でlegacy AIをAI8互換再保存すると一部がoutlineへ変換される場合があります。rotationを含む再編集可能な成果物は`materialize-native`経路を使い、font・tracking・rotation・identityの一致を検証します。

初期 reader は、直線・3次Bézierからなる基本 path を対象にしています。compound pathやclippingを含む任意のlegacy AIを完全に読める段階ではありません。入力は上書きせず、出力先を明示してください。

## ドキュメント

- [開発原則と想定ユースケース](docs/development-principles.md)
- [実現可能性調査](docs/feasibility.md)
- [推奨アーキテクチャ](docs/architecture.md)
- [オーサリングモデル](docs/authoring-model.md)
- [開発ロードマップ](docs/roadmap.md)
- [調査ソース](docs/sources.md)
- [Phase 0 の実装状況](docs/phase0-status.md)
- [Illustrator 適合試験](docs/illustrator-testing.md)
- [Modern AI read-only feature profile](docs/modern-ai-read-profile.md)

## 暫定結論

限定した機能セットなら実現可能です。

- `.ai` の読み取りは、現代形式の Illustrator Private Data まで扱う先行 OSS があり、十分に着手可能です。
- Illustrator で開けるファイルの新規生成は、公開仕様のあるレガシー AI/EPS、または PDF/SVG を入口にすれば実現できます。
- 現代版 `.ai` のネイティブ編集情報を完全に相互変換するには、非公開の内部形式、PDF 表現との二重管理、フォント・効果・カラーマネジメントへの対応が必要です。これは長期的な互換性プロジェクトです。

推奨する最初の製品は「対応範囲を明示した Python IR + 読み取り + AI8/PDF/SVG 書き出し + 検証 CLI」です。その後、現代版 AI Private Data の書き戻しを実験機能として追加します。

## ライセンス

[MIT License](LICENSE) で公開しています。GPLコードをコアへコピーせず、公開仕様と自作fixtureに基づく実装を維持します。GPL依存を将来追加する場合も、任意の隔離adapterとして扱います。
