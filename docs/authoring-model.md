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
price_table = Table(
    rows=products,
    columns=[
        TextColumn("商品名", key="name"),
        MoneyColumn("価格", key="price"),
        BadgeColumn("状態", key="status"),
    ],
    style=shared_table_style,
    variant="catalog",
)

page.place(price_table, x=20, y=40, width=170)
```

`Table.render(context)`が列幅、折り返し、罫線、ページ分割等を解決し、汎用グラフィックIRの`Group`、`Text`、`Path`へ展開します。コアIRが`PriceTable`等のあらゆる業務概念を直接知る必要はありません。

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
