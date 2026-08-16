# Phase 0 実装状況

更新日: 2026-08-16

## 今回作った縦切り

```text
legacy .ai bytes
  -> format detector
  -> Phase 0 legacy reader
  -> Python IR / JSON
  -> Phase 0 AI7 writer
  -> legacy .ai bytes
```

現代版の PDF-compatible AI はコンテナと代表 marker の検出までです。PrivateData を「PDFとして見える内容」と混同しないよう、未対応時は JSON export を明示的に停止します。

## 対応 feature profile

| 項目 | Reader | Writer | 備考 |
| --- | --- | --- | --- |
| legacy AI container | 対応 | 対応 | Illustrator 7 公開仕様のサブセット |
| document bounds / title | 対応 | 対応 | 原点は `(0, 0)` |
| layer name / visibility / lock | 対応 | 対応 | 安定IDは独自DSCコメントでも保持 |
| straight path | 対応 | 対応 | `m`, `l/L`, path render operators。ID・名前は`%AI3_Note`でも保持 |
| Bézier curve | 対応 | 対応 | `c/C`, `v/V`, `y/Y`を読み、writerは`c/C`へ正規化 |
| RGB fill / stroke / width | 対応 | 対応 | `Xa`, `XA`, `w` |
| CMYK fill / stroke | 対応 | 対応 | `k`, `K` |
| stroke style | 対応 | 対応 | dash pattern/offset、cap、join、miter limit |
| compound path | 対応 | 対応 | `*u` / `*U`、subpath polarity `D` |
| clipping group | 対応 | 対応 | `q` / `Q`、maskの`h/H`・`W` |
| nested ordinary group | 対応 | 対応 | `u` / `U`、path/text/containerの異種描画順 |
| mixed item stacking | 対応 | 対応 | `item_order`はAI描画順、Illustrator DOMは逆順のtop-to-bottomとして照合 |
| point text | 対応 | 対応 | 内容・位置・size・RGB/CMYK fill。ASCIIとCP932/RKSJ日本語 |
| area text | 未対応 | 未対応 | AI8互換保存ではoutline化。modern materializationでの再構成が必要 |
| semantic table | n/a | 対応 | formatter/variant/style、日英文字幅、折り返し、自動行高をrender |
| semantic components | n/a | 対応 | TextBlock、LayerBuilder、名札・ポスター・入れ子棚札作例 |
| native AI materialization | n/a | Illustrator経由 | legacy textをnative TextFrameへ変換、段落揃えとidentity noteを保持 |
| rigid transform / text rotation | 対応 | 対応 | path・handle・text・nested group、native角度検証 |
| image | 未対応 | 未対応 | fixture調査後 |
| PDF-compatible AI semantic data | 未対応 | 未対応 | 現在は形式判定のみ |

## 意図的な境界

- 形式判定は拡張子ではなく `%PDF-` / `%!PS-Adobe` と Illustrator marker を使う。
- 大きな入力を無制限に読み込まないよう、形式判定の探索は先頭・末尾それぞれ 4 MiB に制限する。
- JSON IR のpath ID・名前は独自コメントに加え、IllustratorがAI8再保存でも保持する標準`%AI3_Note` path属性へ格納する。
- JSONはIRのserialization、fixture、debug、semantic diff、交換境界に使い、複雑なデザインの主たる手書き言語にはしない。
- domain固有の意味や再利用規則はPython component/templateで表現し、汎用グラフィックIRへ決定的にrenderする。
- 一般の`.ai`から「価格表」「商品カード」等の意味を根拠なく推測しない。意味往復には安定ID、metadata、sidecar、元Python sourceとの対応が必要。
- layer/containerのID・名前とdocument metadataは、現時点では他のPostScript readerが無視できる独自DSCコメントだけで保持する。
- 未対応の現代AIをPDF内容だけでJSON化したように見せず、CLIを失敗させる。

## 次の検証ゲート

1. Illustrator再保存をまたぐlayer/container IDとdocument metadataの保持方式を調査する。
2. 実装済みのoperator span/local patch primitiveをnode source spanとtyped editへ接続する。
3. CP932以外のtext encoding、image、transformの次期feature profileを決める。
4. 実装した共通render境界へsemantic metadata manifestを追加する。
5. 検証対象とするIllustratorバージョンの範囲を広げる。

終了条件のうち、Python内の `IR -> AI7 -> IR` と、Illustrator 30.7.0でfixtureを開いた際の編集構造検査は完了しました。対応範囲はまだ小さいため、AI7 writer全体の位置付けは引き続き experimental です。

配布ライセンスは MIT に決定しました。GPL実装はコア依存に含めません。

### Illustrator 2026 適合試験メモ

2026-08-15、Creative Cloudへのログインと初回画面の完了後、macOS上のIllustrator 30.7.0で実機試験を完了しました。入力は一時コピーとし、取得したドキュメント参照だけを保存せず閉じています。開いていた別のユーザー文書や`current document`は操作していません。

| fixture | Illustratorでの結果 | 確認した内容 |
| --- | --- | --- |
| `rectangle.ai` | pass | 1 layer、1 closed path、4 anchors、RGB fill/stroke、stroke width 3 |
| `cmyk-curve.ai` | pass | 1 layer、1 open path、2 anchors、Bézier方向点、CMYK 100/25/0/10、stroke width 4 |

逆方向ではIllustrator自身にRGB矩形とCMYK Bézier曲線を作らせ、AI8互換で一時保存したファイルをPython readerへ読み戻しました。両fixtureともlayer/path/anchor、開閉、paint style、stroke width、RGB/CMYK、Bézier handleの照合に合格しています。この過程で、Illustrator標準AI8が出力する7成分`Xa/XA` paint style、同じ行に並ぶ`w` operator、埋め込みprocset内の`%%Title`をreaderで正しく扱うよう修正しました。

さらにPython生成fixtureをIllustratorでAI8再保存し、再びPython IRへ戻す完全往復を実施しました。RGB矩形とCMYK Bézierはいずれも、平行移動とRGB量子化を正規化した意味比較に合格しています。path ID・名前はAI7仕様の`%AI3_Note`属性へASCII payloadとして格納することで、Illustrator 30.7.0のDOMとAI8再保存の両方に保持されました。通常path、compound subpath、clipping mask/contentの全fixtureでID照合に合格しています。

一方、独自DSCコメントだけに保存しているlayer ID、compound/clipping containerのID・名前、document metadataはIllustrator再保存で除去されます。元のdocument title/boundsも保存先名とartwork boundsへ変わるため、引き続き既知のlossです。

point textのIRとAI7 reader/writerを追加し、Illustrator生成AI8の`To` / `Tp` / `Tf` / `Ta` / `Tx`を読み取れるようにしました。Python生成表は16個のpath（背景・罫線）と20個のlegacy textとしてIllustrator 30.7.0で認識されます。AI7は現行Illustratorで直接はlegacy textになるため、`materialize-native`で20個すべてを現代の編集可能なTextFrameへ変換しました。再オープン後もLEFT/CENTER/RIGHTのparagraph justification、文字内容、配置が保持されます。`FontSpec`とfont catalog検査を追加し、native化した6作例の全132 TextFramesへ指定PostScript fontを割り当てました。保存後の再オープンでも、日本語予定表18個が`KozGoPr6N-Regular`、英字表20個が`Helvetica` / `Helvetica-Bold`を保持しています。従来のAI8 legacy再保存経路におけるfont置換・alignment正規化は、引き続きadvisoryとして区別します。

Python意味モデルの最初の実装として`Table` / `TableColumn` / `TableStyle`を追加しました。列formatter/accessor、行variant、header/body/alternate配色、文字色、列幅、余白、行高、罫線、font要求を共有・派生でき、低水準IRへ決定的にrenderします。[`examples/styled_table.py`](../examples/styled_table.py)がsource of truthで、生成AIはPython readerとIllustrator実機の両方で検査します。

表に依存しない`RenderedComponent` / `LayerBuilder` / `TextBlock` / `TextStyle`と矩形・Bézier楕円primitiveを追加しました。参加者とrole variantから4枚を生成する[`examples/conference_badges.py`](../examples/conference_badges.py)と、日本語文字階層・折り返し・装飾図形を持つ[`examples/event_poster.py`](../examples/event_poster.py)を同じ汎用IRへrenderしています。posterの英字series labelはtracking 160を指定し、native AI保存・再オープン後もIllustrator DOMで160を保持しました。両方ともlegacy構造検査、native materialization、Illustrator再オープン、PDF/PNG visual previewに合格しました。

通常groupの`u` / `U`を再帰的な`Group` IRとして読み書きし、path、text、compound、clipping、子groupの異種描画順を保持するようにしました。商品データから6枚を生成する[`examples/retail_price_tags.py`](../examples/retail_price_tags.py)では、棚札と価格欄を2階層のgroupにしています。Illustrator 30.7.0で12 groups / 19 paths / 41 TextFrames、RIGHT段落揃え、variant配色をnative化後も保持し、PDF/PNG visual previewにも合格しました。

pathのnative stroke styleとしてdash pattern/offset、line cap/join、miter limitを追加しました。Illustrator生成AI8の複合statement `1 J 2 j 6 w 7 M [18 8 4 8 ]3 d`を読み、Python writerからも同じ属性を出力します。[`examples/quarterly_kpi_report.py`](../examples/quarterly_kpi_report.py)はactual/target/gridを異なる線styleで生成し、Illustrator直接検査、AI8完全往復、native materialization、PDF/PNG previewに合格しました。

area textも同時にfixture調査しましたが、Illustrator 30.7.0でAI8互換保存すると文字がoutline群へ変換されました。長文の再編集性を上げるには、modern AI materialization時にarea frameをDOMで再構成する必要があります。

`AffineTransform`とtext rotationを追加し、rigid matrixでpath、Bézier handle、text、nested groupを一括配置できるようにしました。[`examples/packaging_labels.py`](../examples/packaging_labels.py)は3つのlabel groupと2つのbadge group、14 paths、20 TextFramesを持ちます。Illustrator 30.7.0でnative化・再オープンし、side code 3件の90度、badge 2件の-12度、font、tracking、identity mappingを確認しました。PDF visual QAでは初期side codeの枠外配置を検出し、rotation前positionを保持するmaterializationとanchor調整で解消しました。legacy AI8互換再保存では一部の回転textがoutline化したため、rotationの再編集保証はnative materialization経路に限定します。

legacy textへ標準`%AI3_Note`を書くだけでは、Illustrator DOMへnoteが付かずAI8再保存で失われます。一方、native materializationで変換直後のTextFrameへDOM `note`を設定する方式は保持されました。layer/groupのtop-to-bottom DOM順を再帰的に再現してIDと役割名を対応付け、6つのnative作例すべてで全TextFrameのidentity noteを再オープン確認しています。

Illustrator生成の日本語AI8 fixtureから、`RKSJ-H` font profileとCP932 octal textを採取しました。readerはfont名からCP932を選んでUnicodeへ復号し、writerはRKSJ fontのencoding resourceとCP932 bytesを生成します。日本語予定表は列ごとの折り返しと可変行高を含む15 paths / 18 TextFramesとして認識され、Illustrator直接読込・AI8完全往復・PDF/PNG visual previewのすべてに合格しました。

compound pathはIllustrator生成AI8から`*u` / `*U` containerと`D` polarityを採取し、専用IR、reader、writerを追加しました。Python生成fixtureはIllustrator 30.7.0で2 componentを持つ1つの`CompoundPathItem`として認識され、Illustrator再保存後の完全往復でもcontainer、polarity、geometry、RGB fillが保持されました。

clipping groupは`q` / `Q` container、mask pathの`h/H`・`W`、後続content pathを専用IRへ分離します。Python生成fixtureはIllustrator 30.7.0で1つのclipped `GroupItem`として認識され、完全往復でもgroup、mask/content数、geometry、RGB fillが保持されました。

通常path、compound、clippingを別配列に保持しつつ、Layerの`item_order`で異種itemのAI描画順を参照するようにしました。古いJSONにこのfieldがなければ従来の配列順から自動導出します。混在fixtureはIllustrator DOMでtop-to-bottomの`CompoundPathItem → PathItem → GroupItem`として認識され、AI8再保存後も逆向きのAI描画順`clipping_group → path → compound_path`が保持されました。

lossless source prototypeは元bytesを所有し、各物理行を`start/content_end/end`の半開byte spanとして索引化します。CRLF/LF/CR、非UTF-8 byte、未知operatorをそのまま再構築でき、legacy semantic readerも同じsource mapを入力境界に使います。PostScript文字列とinline commentを考慮してstatement末尾operatorのspanも取得できます。入力全体・単一行・行数に既定上限を設けました。

範囲外・重複spanを拒否する`SourceReplacement` / `LegacySource.patched()`で、既知operatorだけを差し替え、未知byteと改行を完全維持する局所patchも実証しました。これは意味検証をしない低レベルprimitiveであり、IR nodeのsource spanやtyped editとの接続は次段階です。

最初の試験では閉じた矩形が3 anchorsとして読まれました。AI7の閉じパスに開始点へ戻る明示的な最終segmentを出力するようwriterを修正し、4 anchorsで再試験に合格しています。また、日本語環境でExtendScript内のreverse solidusが円記号として解釈される問題を避けるため、検査用JSXは該当文字とファイルパスを文字コードから構築します。詳細は [Illustrator 適合試験](illustrator-testing.md) を参照してください。
