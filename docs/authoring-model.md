# オーサリングモデル

## 結論

JSONをIllustratorデザインの唯一の記述言語にはしません。

このプロジェクトでは、Pythonを文脈を持つデザインのオーサリング層、JSON化可能なグラフィックIRを交換・検査層、`.ai`をIllustrator向け成果物または既存編集ソースとして扱います。

```text
意味・文脈
  Pythonのデザインモデル / component / template
  + JSON・CSV・database等の入力データ
        ↓ deterministic render
汎用グラフィックIR
  document / layer / group / text / path / image
        ↓ serialize / patch
Illustrator source
  legacy AI/EPS または modern AI PrivateData + PDF
```

JSON IRは重要ですが、人間が複雑な制作物をすべて手書きするための形式ではありません。

## なぜ意味モデルが必要か

Illustrator上では、表は最終的にtext、path、fill、stroke、group等へ展開されます。しかし制作上の「表」は一種類ではありません。

- 価格表
- 時刻表
- 比較表
- 注文表
- スペック表

これらは罫線や見出し等の基本体裁を共有できても、列の意味、値の整形、強調規則、ページ分割、注釈、欠損値の扱いが異なります。低レベルの図形配列だけでは、この文脈を十分に表現・再利用できません。

Pythonでは、制作物固有の意味を持つcomponentとして表現できます。

```python
from py_ai_illustrator import Color, Table, TableColumn, TableStyle

price_table = Table(
    id="price-list",
    columns=[
        TableColumn("name", "Product", 180),
        TableColumn(
            "price",
            "Price",
            90,
            alignment="right",
            formatter=lambda value: f"${value:,.0f}",
        ),
    ],
    rows=products,
    variant_key="kind",
    style=TableStyle(
        header_fill=Color(0.08, 0.16, 0.28),
        variant_fills={"featured": Color(0.9, 0.95, 1.0)},
    ),
)

layer = price_table.render_layer(x=40, top=300)
```

現在の`Table.render()`は列幅、header/body/variantの配色、formatter、Illustratorのネイティブ中央・右揃え、余白、罫線に加え、列単位の折り返し、明示改行、東アジア文字幅、複数行に応じた行高を解決します。結果は`RenderedComponent`となり、汎用グラフィックIRの`TextFrame`、`Path`へ決定的に展開されます。単体layerが必要な場合は互換APIの`render_layer()`を使えます。コアIRが`PriceTable`等のあらゆる業務概念を直接知る必要はありません。ページ分割と複合cell componentは今後の拡張です。

`formatter`や`accessor`にはPython関数を渡せます。したがって、単なるJSON schemaでは表しにくい制作物固有の計算や文脈依存の表示も、表コンポーネントの入力境界で扱えます。実行可能な例は[`examples/styled_table.py`](../examples/styled_table.py)と、日本語・自動行高を扱う[`examples/japanese_table.py`](../examples/japanese_table.py)です。

## 表以外のcomponent

表は最初のstress testであり、authoring modelの中心ではありません。現在は次の共通境界を使います。

- `RenderedComponent`: componentが生成したpath、text、linked image、描画順、寸法
- `LayerBuilder`: 複数componentを安定ID付きで一つのlayerへ合成
- `Group`: path、text、compound、clipping、子groupの描画順を保つ編集単位
- `TextBlock` / `AreaTextBlock` / `TextStyle`: 決定的な行分割、native再流し込み枠、文字階層、段落揃え、tracking、行送り
- `FontSpec`: AI7 bridge名とIllustratorのPostScript名を分離した書体指定
- `rectangle_path` / `ellipse_path` / `polyline_path`: domainに依存しない編集可能な図形primitive
- `AffineTransform` / `RenderedComponent.transformed()`: path、handle、text、nested groupをまとめて配置するrigid transform
- `LinkedImage`: 同一出力先の`Links/`へ収集されるPNG/JPEGの外部参照と配置寸法

[`examples/conference_badges.py`](../examples/conference_badges.py)では、`Attendee`とrole variantから4枚の`ConferenceBadge`を生成します。[`examples/event_poster.py`](../examples/event_poster.py)では、同じprimitiveから日本語の告知ポスターを生成します。前者は反復・variant・識別番号、後者は文字階層・折り返し・装飾図形が主題です。いずれも表のrow/column modelへ押し込めていません。

[`examples/retail_price_tags.py`](../examples/retail_price_tags.py)は、実務寄りの反復制作例です。商品、価格、販売状態を`Product`として持ち、`PriceTag`が共通体裁とvariantを決定します。`LayerBuilder.add_grouped()`により各棚札は一括移動でき、価格欄も子groupとして独立編集できます。金額はIllustratorのRIGHT段落揃えで保持されるため、桁数を変更しても座標の手調整を前提にしません。

[`examples/quarterly_kpi_report.py`](../examples/quarterly_kpi_report.py)では、`LineChart`が月次値を座標へscaleし、actual series、target、grid、labelへrenderします。線は単なる見た目ではなく、solid/dashed、cap、join、offsetを持つnative strokeとして保持されます。表のcell modelを流用せず、グラフ固有の入力検証とscale規則をPython componentへ置いています。

[`examples/packaging_labels.py`](../examples/packaging_labels.py)では、3つの商品variantからラベルを生成し、縦向きside codeと斜めbadgeを配置します。`AffineTransform.rotation()`はpath anchorとBézier handle、text anchorとrotation、nested groupを同じmatrixで変換します。現時点でtextを含むcomponentは、font sizeやstroke widthを曖昧にscaleしないrigid transform（平行移動・回転）に限定し、非一様scaleは明示的に拒否します。

[`examples/editorial_brochure.py`](../examples/editorial_brochure.py)では、導入文、2段本文、引用を4つの`AreaTextBlock`として組み立てます。文章枠は単一のTextFrame identityを保ち、Illustrator上で枠幅を変更すると標準の再流し込みが働きます。

[`examples/campaign_variants.py`](../examples/campaign_variants.py)では、同じキャンペーン内容をSquare・Portrait・Bannerへ展開します。`Artboard`はdocument内の名前付き出力矩形、各`CampaignVariant`は独立groupです。legacy bridgeでは全variantを一つのcomposite canvasへ置き、modern materializationで3つのnative Artboardへ再構成します。

[`examples/product_catalog.py`](../examples/product_catalog.py)では、写真を`LinkedImage`、見出しとラベルをpoint text、説明をAreaText、背景・badge・CTAをvector pathとして合成します。出力時はAIの隣に`Links/`を作り、同一内容だけを再利用します。Illustrator成果物では埋め込み画像へ変えず、外部差し替え可能なlinked `PlacedItem`を保持します。

この形なら、今後の商品カード、値札、名刺、図解、カタログページ等も、それぞれの文脈を持つPython componentとして追加できます。低水準IRとAI writerは特定componentを知りません。

## legacy AIと再編集可能なnative AI

AI7は公開仕様に基づく往復・検査形式として有用ですが、現行IllustratorではAI7 textがlegacy textとして読み込まれます。writerは`Ta` operatorへLEFT/CENTER/RIGHTを出力しますが、現代の編集可能なTextFrameにするには変換が必要です。

`FontSpec`は`postscript_name`を主たる書体IDとし、表示用の`family` / `style`と、必要な場合だけ`legacy_name`を持ちます。たとえば日本語では、AI7 streamのCP932/RKSJ resource名と、native TextFrameへ設定する`KozGoPr6N-Regular`を同じ指定にまとめます。family名や見た目の近い代替書体で曖昧に解決はしません。`py-ai illustrator-fonts`で現在のIllustratorが持つ正確な名前を検索・検証できます。

`py-ai materialize-native`は入力を一時コピーし、Illustratorの`legacyTextItems.convertToNative()`を使ってnative TextFrameへ変換し、PDF-compatible AIとして別名保存します。変換直後、IRのDOM順と対応する各TextFrameの`note`へ`py-ai-text:` identityを設定し、font、size、fill、tracking、rotation、leading、paragraph justificationを割り当てます。AreaText指定はpoint textのbaseline anchorから文章枠を再構成し、複数Artboard指定はcomposite canvas基準の矩形と名前からnative Artboard collectionを再構成します。linked image指定はAI7上のplaceholderを同じ描画位置の`PlacedItem`へ置換し、`Links/`への外部参照を維持します。属性不一致は`mismatch`として報告されます。

`TextBlock`の折り返しは各行を独立したpoint textとしてrenderし、出力を決定的にします。幅変更へ追従させたい文章は`AreaTextBlock`を使います。Illustrator 30.7.0はarea textをAI8互換保存するとoutline化するため、再編集可能なarea textの保証はmodern materialization経路に限定します。

## 三つの層の責務

### 1. Python意味モデル

制作物の文脈、規則、再利用可能な体裁を表現します。

- 独自component、template、variant
- 条件分岐、反復、計算、制約
- データ検証、単位変換、文字列整形
- theme、style、resourceの共有
- グラフィックIRへのrender

renderは可能な限り決定的にし、フォント、用紙、色空間、入力データ等を明示します。暗黙のglobal stateや実行時刻に依存する出力は避けます。

### 2. JSON化可能なグラフィックIR

Illustratorに共通する編集可能な構造を表現します。

- document、artboard、layer、group
- text、path、compound path、clipping、image
- geometry、paint、transform、stacking order
- 安定ID、source span、未知属性への参照

JSONはこのIRのserializationです。主な用途はfixture、debug、semantic diff、言語間交換、外部ツールとの境界です。単純な図形やテストではJSONを直接入力しても構いませんが、すべての制作物へ強制しません。

### 3. Illustrator source

AI/EPS/PDF上のoperator、resource、metadata、未知payloadを扱います。

- 新規生成時はIRから対応形式へserializeする
- 既存ファイル編集時はlossless source spanへ局所patchする
- 未対応領域は勝手に削除・再解釈しない
- Illustrator実機で編集構造と見た目を検証する

## JSONに残すもの

JSONは「振る舞い」ではなく、データと検証可能な結果に向いています。

```json
{
  "variant": "catalog",
  "rows": [
    {"name": "商品A", "price": 1200, "status": "new"}
  ]
}
```

この入力をどの列構成・体裁で描くかはPython componentが決めます。また、エージェントが実行する変更は任意Pythonではなく、`set_fill`や`replace_text`等の検証可能な操作JSONとして表現できます。

## source of truth

利用形態によってsource of truthを明示します。

### パラメトリック生成

Python source、template、入力データがsource of truthです。`.ai`はIllustratorで仕上げや確認ができる編集可能な成果物です。再生成可能性を優先します。

### 既存Illustratorファイルの編集

元の`.ai`がsource of truthです。readerは対応部分をIRへ投影し、writerは未知部分を保持して必要箇所だけpatchします。元ファイルを直接上書きしません。

### ハイブリッド運用

Python生成後にIllustratorで人手編集し、その結果を再びPythonへ戻す場合、二種類の往復を区別します。

- グラフィック往復: geometry、paint、text、階層、stacking order等を保つ
- 意味の往復: 「価格表の合計行」「商品カードの価格欄」等の制作上の役割を保つ

グラフィック往復はファイル構造から検証できます。意味の往復は一般の`.ai`から完全には復元できません。安定ID、埋め込みmetadata、sidecar manifest、または元のPython sourceとの対応表が必要です。

意味metadataが失われた場合、見た目が同じでも元componentへ安全に逆コンパイルできるとは扱いません。

## API設計への制約

今後の実装は次を守ります。

1. JSON手書きをすべての新規作成workflowへ強制しない。
2. コアIRはdomain固有componentから独立させ、JSONへ決定的にserializeできるようにする。
3. Python componentが`render(context) -> IR node(s)`相当の境界で拡張できるようにする。
4. render前の入力検証と、render後のIR・Illustrator適合検証を分ける。
5. `.ai`から復元できたグラフィック構造を、根拠なく高水準の意味へ昇格しない。
6. semantic metadataの保持状態をcompatibility reportへ含める。
7. Python実行を必要としないreader、validator、IR操作、CLIを維持する。

この分離により、Pythonの表現力を使いながら、ファイル互換層を特定の制作分野や一つのテンプレートシステムへ固定せずに済みます。
