# Phase 0 実装状況

更新日: 2026-08-15

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
| compound path | 対応 | 対応 | `*u` / `*U`、subpath polarity `D` |
| clipping group | 対応 | 対応 | `q` / `Q`、maskの`h/H`・`W` |
| mixed item stacking | 対応 | 対応 | `item_order`はAI描画順、Illustrator DOMは逆順のtop-to-bottomとして照合 |
| point text | 対応 | 対応 | 内容・位置・size・RGB/CMYK fill・整列要求。writerは現在ASCII限定 |
| semantic table | n/a | 対応 | Pythonのcolumn/row/formatter/variant/styleをpath + textへrender |
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
3. Unicode text/image/nested groupの次期feature profileを決める。
4. 実装した`Table.render_layer()`を共通render protocolとsemantic metadata manifestへ一般化する。
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

point textのIRとAI7 reader/writerを追加し、Illustrator生成AI8の`To` / `Tp` / `Tf` / `Tx`を読み取れるようにしました。Python生成表は16個のpath（背景・罫線）と20個のTextFrameとしてIllustrator 30.7.0で認識され、612×360 artboard上の座標、stacking、paint、文字内容・size・配置を確認しています。AI8再保存ではfont名が環境既定へ置換され、point textのalignmentがleftへ正規化されるため、この2項目はadvisoryとして報告します。

Python意味モデルの最初の実装として`Table` / `TableColumn` / `TableStyle`を追加しました。列formatter/accessor、行variant、header/body/alternate配色、文字色、列幅、余白、行高、罫線、font要求を共有・派生でき、低水準IRへ決定的にrenderします。[`examples/styled_table.py`](../examples/styled_table.py)がsource of truthで、生成AIはPython readerとIllustrator実機の両方で検査します。

compound pathはIllustrator生成AI8から`*u` / `*U` containerと`D` polarityを採取し、専用IR、reader、writerを追加しました。Python生成fixtureはIllustrator 30.7.0で2 componentを持つ1つの`CompoundPathItem`として認識され、Illustrator再保存後の完全往復でもcontainer、polarity、geometry、RGB fillが保持されました。

clipping groupは`q` / `Q` container、mask pathの`h/H`・`W`、後続content pathを専用IRへ分離します。Python生成fixtureはIllustrator 30.7.0で1つのclipped `GroupItem`として認識され、完全往復でもgroup、mask/content数、geometry、RGB fillが保持されました。

通常path、compound、clippingを別配列に保持しつつ、Layerの`item_order`で異種itemのAI描画順を参照するようにしました。古いJSONにこのfieldがなければ従来の配列順から自動導出します。混在fixtureはIllustrator DOMでtop-to-bottomの`CompoundPathItem → PathItem → GroupItem`として認識され、AI8再保存後も逆向きのAI描画順`clipping_group → path → compound_path`が保持されました。

lossless source prototypeは元bytesを所有し、各物理行を`start/content_end/end`の半開byte spanとして索引化します。CRLF/LF/CR、非UTF-8 byte、未知operatorをそのまま再構築でき、legacy semantic readerも同じsource mapを入力境界に使います。PostScript文字列とinline commentを考慮してstatement末尾operatorのspanも取得できます。入力全体・単一行・行数に既定上限を設けました。

範囲外・重複spanを拒否する`SourceReplacement` / `LegacySource.patched()`で、既知operatorだけを差し替え、未知byteと改行を完全維持する局所patchも実証しました。これは意味検証をしない低レベルprimitiveであり、IR nodeのsource spanやtyped editとの接続は次段階です。

最初の試験では閉じた矩形が3 anchorsとして読まれました。AI7の閉じパスに開始点へ戻る明示的な最終segmentを出力するようwriterを修正し、4 anchorsで再試験に合格しています。また、日本語環境でExtendScript内のreverse solidusが円記号として解釈される問題を避けるため、検査用JSXは該当文字とファイルパスを文字コードから構築します。詳細は [Illustrator 適合試験](illustrator-testing.md) を参照してください。
