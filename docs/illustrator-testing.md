# Illustrator 適合試験

`py-ai test-illustrator` は、生成したAIファイルが実際のIllustratorで編集可能な構造として開けるかを検査します。通常のPythonテストとは分離し、Illustratorを利用できるmacOS環境で明示的に実行します。

modern synchronized patch成果物は、openだけでなくcurrent-format再保存と再openまで行います。

```bash
uv run py-ai test-illustrator-modern-roundtrip patched.ai
```

第一層の最終確認は、legacy patch、modern fill/translate/text atomic batch、modern Bézier strokeを一度に生成して実機試験します。

```bash
uv run python scripts/test_layer1_illustrator.py
```

このmatrixは一時出力だけを使い、入力や既存成果物を上書きしません。

2026-08-18にIllustrator 30.7.0で最終matrix 3/3件が`passed`になりました。modern fill/translate/text batchの変更pixel比は約0.064%、Bézier strokeは約0.049%で、DOM・PrivateData/PDF・timestampの必須checkはすべて一致しました。legacy paint/translate batchはgeometry・visible paint・path identityを保持し、矩形描画に影響しないmiter limit 4→10の正規化だけをadvisoryとして記録しました。

実機結果を対応versionとして扱う条件とPython側のreader/writer/patch範囲は[Trusted Legacy Conversion feature profile](legacy-feature-profile.md)に定義します。

## 初回準備

1. Adobe Creative Cloudへログインする。
2. Illustratorを手動で起動する。
3. ライセンス確認、オンボーディング、ワークスペース選択などの初回画面を完了する。
4. Home画面が操作できる状態で、次の応答確認を実行する。

```bash
osascript -e 'with timeout of 10 seconds' \
  -e 'tell application "Adobe Illustrator" to return version' \
  -e 'end timeout'
```

バージョン文字列が返れば、AppleScript経由の適合試験を実行できます。

## 実行

利用可能なフォントをfamily・style・PostScript名で検索し、制作物の必須フォントを検証できます。書体名はfamily表示名ではなく、`postscript_name`を`FontSpec`へ渡します。

```bash
uv run py-ai illustrator-fonts --query "Noto Sans JP"
uv run py-ai illustrator-fonts \
  --require KozGoPr6N-Regular \
  --require Helvetica-Bold
```

```bash
uv run py-ai test-illustrator examples/rectangle.ai
uv run py-ai test-illustrator examples/cmyk-curve.ai
```

逆方向（Illustratorでfixtureを作成・AI8保存し、Python IRへ読み戻す）:

```bash
uv run py-ai test-illustrator-export --fixture rgb-rectangle
uv run py-ai test-illustrator-export --fixture cmyk-curve
uv run py-ai test-illustrator-export --fixture group
uv run py-ai test-illustrator-export --fixture stroke-style
uv run py-ai test-illustrator-export --fixture point-text
uv run py-ai test-illustrator-export --fixture unicode-text

# generator/parserの追加回帰fixture（実機version matrixへ加える場合は結果を下表へ記録）
uv run py-ai test-illustrator-export --fixture compound-path
uv run py-ai test-illustrator-export --fixture clipping-group
```

完全往復（Python生成AIをIllustratorでAI8再保存し、Python IRへ戻す）:

```bash
uv run py-ai test-illustrator-roundtrip examples/rectangle.ai
uv run py-ai test-illustrator-roundtrip examples/cmyk-curve.ai
uv run py-ai test-illustrator-roundtrip examples/compound-path.ai
uv run py-ai test-illustrator-roundtrip examples/clipping-group.ai
uv run py-ai test-illustrator-roundtrip examples/mixed-stack.ai
uv run py-ai test-illustrator examples/styled-table.ai
uv run py-ai test-illustrator-roundtrip examples/styled-table.ai
uv run py-ai test-illustrator examples/japanese-table.ai
uv run py-ai test-illustrator-roundtrip examples/japanese-table.ai
uv run py-ai test-illustrator examples/retail-price-tags.ai
uv run py-ai test-illustrator examples/quarterly-kpi-report.ai
uv run py-ai test-illustrator-roundtrip examples/quarterly-kpi-report.ai
uv run py-ai test-illustrator examples/packaging-labels.ai
```

現行Illustratorで再編集可能なnative TextFrameを持つ現代AIへ変換する場合:

```bash
uv run py-ai materialize-native examples/styled-table.ai \
  -o examples/styled-table.native.ai
uv run py-ai materialize-native examples/product-catalog.ai \
  -o examples/product-catalog.native.ai
uv run py-ai test-illustrator examples/styled-table.native.ai
uv run py-ai test-illustrator examples/retail-price-tags.native.ai
uv run py-ai test-illustrator examples/quarterly-kpi-report.native.ai
uv run py-ai test-illustrator examples/packaging-labels.native.ai
```

`materialize-native`はAI7の`Ta`段落属性を含むlegacy textを`convertToNative()`で現代TextFrameへ変換します。変換後は再帰的なIllustrator DOM順に基づき、各TextFrameの`note`へ`py-ai-text:` identity（安定ID・役割名）を設定し、`FontSpec.postscript_name`のfont、`TextStyle.tracking`、rotationを割り当てます。linked image placeholderは同じ親・描画順の`PlacedItem`へ置換し、成果物横の`Links/`を参照したまま保存します。指定fontやlinkが見つからない場合、割り当て後の属性が一致しない場合は成功扱いにしません。出力はPDF-compatible AIで、既存出力の上書きを拒否します。

rotationを含むlegacy AIをIllustratorでAI8互換再保存する経路では、一部textがoutlineへ変換され、text/path/group数の完全往復は成立しませんでした。回転textの保証対象はnative materialization経路です。`packaging-labels.native.ai`では保存・再オープン後も全20 TextFramesがnativeのまま残り、5件の回転角を保持します。

調査用にIllustrator生成AIを残す場合は、既存ファイルではない出力先を指定します。上書きは拒否されます。

```bash
uv run py-ai test-illustrator-export \
  --fixture cmyk-curve \
  --ai-output illustrator-native-curve.ai
```

JSONレポートを保存する場合:

```bash
uv run py-ai test-illustrator examples/rectangle.ai \
  -o compatibility-report.json
```

## 安全性

- リポジトリ内の入力は直接開かず、一時コピーを作る。
- JavaScriptが返したドキュメント参照だけを検査する。
- `current document`や既存ドキュメントを操作しない。
- 検査対象は`DONOTSAVECHANGES`で閉じる。
- 逆方向試験は新規ドキュメントを作り、指定した一時出力だけへ保存する。
- native化は入力の一時コピーだけを開き、明示した新規出力へ保存する。
- linked assetは`Links/`へcopy/reuseし、同名別内容の既存ファイルを上書きしない。
- `--ai-output`が既存ファイルを指す場合は上書きしない。
- Illustratorが応答しない場合はタイムアウトし、互換性失敗ではなく`environment-unavailable`を返す。

## 判定

自動判定ではPython IRとIllustrator上の次の構造を比較します。

- layer数とlayer名
- path item数
- text frame数
- ordinary / clipping group数とlayer直下のitem種別順
- 各pathのanchor数
- closed / filled / strokedの個数

レポートにはartboard、anchor座標、Bézier方向点、stroke幅、RGB/CMYK colorも含めます。modern current-format再保存ではページ寸法一致を必須とし、IllustratorによるPDF再生成に限って1ページあたり変更pixel比0.1%以下をbounded normalizationとして許容します。exact pixel一致もadvisoryとして別に報告します。

完全往復では次を意味属性として比較します。

- layer数・名前・visibility
- layer内の異種item種別順（通常path・compound・clipping・通常group）
- path数、anchor数、open/closed、fill/strokeの有無
- pathの安定IDと名前
- anchor間の相対座標と、anchorに対するBézier handleの相対座標
- stroke width
- dash pattern/offset、line cap/join、miter limit
- RGB/CMYK process color
- point textの内容、size、fill、相対配置

Illustratorはlegacy AIを開く際にdocument原点を移動することがあるため、path全体の平行移動は正規化します。RGBはIllustratorの8-bit値への量子化を許容します。

pathの安定IDと名前はAI7仕様の`%AI3_Note` path属性へ`py-ai:`接頭辞付きのASCII payloadとして格納します。Illustrator 30.7.0では通常path、compound subpath、clipping mask/contentのDOMへ読み込まれ、AI8再保存後も復元・照合できました。仕様上noteは254文字までなので、payloadが上限を超える場合は従来の独自コメントだけへフォールバックします。

現在、独自DSCコメントだけで保持しているdocument metadata、layer ID、compound/clipping containerのID・名前はIllustratorのAI8再保存で除去されます。また、document titleとboundsは保存先名とartwork boundsに変わります。これらは既知のlossとしてレポートの意味合格判定から除外しています。

AI8互換のlegacy再保存ではfont名、paragraph属性、描画結果に影響しない既定miter limitの正規化が起こり得るため、この経路では`advisory_checks`へ分離します。geometry、stroke width、cap/join、dash、visible paintは必須です。一方、native materialization経路ではAI7 `Ta`をnative paragraph justificationへ変換し、現代AIを再度開いた後もLEFT/CENTER/RIGHTと全TextFrameのidentity noteが保持されることを必須の実機確認にしています。

## 確認済み環境

現時点の正式サポート対象はIllustrator 2026です。新しい正式版が公開された場合は、この文書のfixture一式をそのversionで検証し、合格後にのみ対応versionへ追加します。

2026-08-15にIllustrator 30.7.0で次のfixtureが`passed`になりました。

| fixture | 構造 | Illustratorが取得した主な属性 |
| --- | --- | --- |
| `examples/rectangle.ai` | 1 layer / 1 path / 4 anchors | closed、RGB fill/stroke、stroke width 3 |
| `examples/cmyk-curve.ai` | 1 layer / 1 path / 2 anchors | open、Bézier方向点、CMYK stroke、stroke width 4 |
| `examples/compound-path.ai` | 1 layer / 1 compound / 2 component paths | 8 anchors、正負polarity、RGB fill |
| `examples/clipping-group.ai` | 1 layer / 1 clipped group / mask + content | mask 4 anchors、content 4 anchors、RGB fill |
| `examples/mixed-stack.ai` | clipping / path / compoundの混在 | DOM top-to-bottom順、5 paths、3 container種別 |
| `examples/point-text.ai` | 1 layer / 1 point text | TextFrame、内容、size 14、RGB fill |
| `examples/styled-table.ai` | 1 layer / 16 paths / 20 point texts | 5背景、11罫線、20セル、612×360 artboard |
| `examples/japanese-table.ai` | 1 layer / 15 paths / 18 point texts | CP932日本語、折り返し、自動行高、560×380 artboard |
| `examples/conference-badges.ai` | 1 layer / 16 paths / 20 point texts | 4名札、role variant、楕円avatar、左右中央揃え |
| `examples/event-poster.ai` | 1 layer / 4 paths / 9 point texts | 日本語階層、折り返し、装飾Bézier楕円 |
| `examples/retail-price-tags.ai` | 1 layer / 12 nested groups / 19 paths / 41 point texts | 6棚札、価格欄group、販売variant、右揃え価格 |
| `examples/quarterly-kpi-report.ai` | 1 layer / 4 groups / 17 paths / 24 point texts | actual/target/grid、dash、round cap/join、KPI cards |
| `examples/packaging-labels.ai` | 1 layer / 5 groups / 14 paths / 20 point texts | rigid transform、90度side code、-12度badge |
| `examples/editorial-brochure.ai` | 1 layer / 4 paths / 7 text intents | native化後に4 AreaText、2段本文、導入、中央揃え引用 |
| `examples/campaign-variants.ai` | 1 layer / 3 groups / 9 paths / 19 point texts | composite canvas上のSquare・Portrait・Banner variant |
| `examples/product-catalog.ai` | 1 layer / 3 paths / 5 texts / 1 image placeholder | linked image・point/area text・vectorの混在stacking |

native materializationも同じ環境で確認済みです。

下記9 fixtureはすべて、再オープン後に全native TextFrameの`note`が`py-ai-text:` identityを保持することも確認しています。

| native fixture | 変換結果 | 再オープン後 |
| --- | --- | --- |
| `styled-table.native.ai` | 20 legacy → 20 native TextFrames | LEFT/CENTER/RIGHT、内容、配置を保持 |
| `japanese-table.native.ai` | 18 legacy → 18 native TextFrames | 日本語内容と3種の揃えを保持 |
| `conference-badges.native.ai` | 20 legacy → 20 native TextFrames | 名前・role・番号と3種の揃えを保持 |
| `event-poster.native.ai` | 9 legacy → 9 native TextFrames | 日本語階層・折り返しと3種の揃えを保持 |
| `retail-price-tags.native.ai` | 41 legacy → 41 native TextFrames | 12 groups、販売variant、RIGHT価格揃えを保持 |
| `quarterly-kpi-report.native.ai` | 24 legacy → 24 native TextFrames | chart group、dash、KPI値と3種の揃えを保持 |
| `packaging-labels.native.ai` | 20 legacy → 20 native TextFrames | 5 groups、90度side code、-12度badge、font・tracking・IDを保持 |
| `editorial-brochure.native.ai` | 7 legacy → 7 native TextFrames | 4 AreaText、枠寸法、font/size/fill/leading、揃え、IDを保持 |
| `campaign-variants.native.ai` | 19 legacy → 19 native TextFrames | 3 named Artboards、3 groups、font/size/fill/揃え/IDを保持 |
| `product-catalog.native.ai` | 5 legacy → 5 native TextFrames、1 linked image | `PlacedItem`、link存在、300×252 pt、point/AreaText、IDを保持 |

逆方向も同じ環境で確認済みです。

| Illustrator生成fixture | Python readerでの結果 |
| --- | --- |
| `rgb-rectangle` | legacy AI検出、1 layer、4 anchors、closed、RGB fill/stroke、stroke width 3 |
| `cmyk-curve` | legacy AI検出、1 layer、2 anchors、open、Bézier方向点、CMYK stroke、stroke width 4 |
| `point-text` | legacy AI検出、文字列、size 14、RGB fill |
| `unicode-text` | RKSJ-H + CP932をUnicode日本語へ復号、size 16、RGB fill |
| `group` | legacy AI検出、1 ordinary group、2 child paths |
| `stroke-style` | dash pattern/offset、round cap、bevel join、miter limitを復号 |

完全往復も両fixtureで`passed`です。RGB矩形は全体が平行移動しRGB値が8-bitへ量子化されましたが、正規化後のpath geometryとpaint属性は一致しました。CMYK Bézierはanchor・handle・CMYK値・stroke widthが保持されました。両方ともpath ID・名前も保持されました。

compound pathも完全往復で`passed`です。比較器はcontainer数、component数、各subpathのpolarityも照合します。

clipping groupも完全往復で`passed`です。比較器はclipping container数、content数、maskとcontent双方のgeometry・paint属性を照合します。

mixed stackも完全往復で`passed`です。IRの`item_order`はAIのback-to-front描画順を保持し、Illustrator DOMのpage item列は逆向きのtop-to-bottom順として照合します。AI8再保存後もcontainer種別順が一致しました。

styled tableも必須項目の完全往復で`passed`です。文字font名とalignmentのadvisory lossを除き、16 paths、20 texts、内容、size、fill、相対配置、罫線geometry、RGB色、stackingが一致しました。

japanese tableも完全往復で`passed`です。18 textsすべての日本語内容、RKSJ font、size、fill、相対配置と、15 pathsのgeometry・paint・stackingが一致しました。表・名札・ポスター・誌面のnative AIをPDF/PNG化したpreviewでも、文字化け、セル越境、行の重なり、文章欠落がないことを確認しました。

editorial brochureはnative保存・再オープン後も4枠が`TextType.AREATEXT`で、390×66、222×310（2枠）、180×80 ptを保持しました。本文10.5pt、導入13pt、引用15pt、指定leading、RGB fill、LEFT/CENTER justification、全7 TextFramesのidentityと内容が一致しています。

campaign variantsはnative保存・再オープン後に`Square 1x1`、`Portrait 3x4`、`Banner 3x1`の3 Artboardsを保持しました。矩形はそれぞれ360×360、270×360、540×180 ptで、PDF-compatible previewも対応する3ページとして生成され、全ページの見切れ・重なりがないことを確認しました。

これは記載したfixtureと機能subsetの適合結果です。任意のAI7ファイルや未対応機能の互換性を保証するものではありません。
