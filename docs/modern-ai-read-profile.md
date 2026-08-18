# Modern AI read-only feature profile

更新日: 2026-08-18

## 保証境界

このprofile v2は、PDF-compatible modern AIの実用デザイン向け読み取り専用semantic境界です。入力を変更せず、PDF containerから`/PieceInfo/Illustrator/Private`をたどり、`AIPrivateData*` streamを決定的な順序で抽出・展開・索引化します。その上で、source spanから証明できるlayer、group、通常/compound/clipping path、CMYK/RGB paint、曲線、textだけを共通`Document` IRへ投影します。

次の三状態を混同しません。

| 状態 | JSON field | 意味 |
| --- | --- | --- |
| container読取 | `modern_ai.container.status` | bounded PDF readerが対象containerを構造的に読めた |
| PrivateData抽出 | `modern_ai.private_data.status` | `absent` / `extracted` / `partial` / `failed` |
| semantic対応 | `modern_ai.semantic.status` | `unsupported` / `partial` / `supported`。実機profileはPrivateDataの未知operator、または配置を証明できないtextを含むため`partial` |

`extracted`はmodern AIを編集・再保存できるという意味ではありません。semantic結果が得られても読み取り専用です。writer、patch、PDF表示表現との同期は保証対象外です。

## 対応するPDF構造

- `%PDF-` headerを持つPDF 1.x container
- 通常のindirect objectと、dictionary / array / name / number / string / referenceの必要subset
- direct dictionaryまたはindirect referenceで接続された`PieceInfo -> Illustrator -> Private`
- `AIPrivateData`または数値suffix付き`AIPrivateData1`、`AIPrivateData2`等
- segment suffixの数値昇順による決定的な抽出（PDF dictionaryの記載順には依存しない）
- direct `/Length`を持つstreamと、`endstream`によるbounded fallback
- raw stream payloadのsource span、object span、raw bytes、SHA-256
- decoded bytes、SHA-256、filter chain、decode状態

汎用PDF parserではありません。object stream (`/ObjStm`)、xref stream (`/XRef`)、暗号化、任意のPDF featureは対応profile外であり、認識できるものはdiagnosticへ出します。PrivateDataへ到達する参照の欠落、循環、深さ超過は失敗として明示します。

## Filter matrix

| filter | 状態 | fixture |
| --- | --- | --- |
| なし | 対応 | `tests/fixtures/generated/modern-private-data.ai` segment 1 |
| `ASCIIHexDecode -> FlateDecode` | 対応 | 同fixture segment 2 |
| `FlateDecode` (`/Fl` alias含む) | 対応 | unit test |
| `ASCII85Decode` (`/A85` alias含む) | 対応 | decoder profile |
| `%AI24_ZStandard_Data` wrapper | 対応 | `examples/styled-table.native.ai` (Illustrator 30.7.0生成) |
| その他 | 非対応 | raw bytes/hashを保持し`unsupported_stream_filter`を返す |

zstd展開にはBSDライセンスの`zstandard` packageを使用します。GPL-2.0-or-laterの`inkai`コードやsample bytesはコア、fixture、テストへ取り込んでいません。

Decision Gate Lの隔離比較では、現行readerが評価modern fixture 2/2を抽出した一方、inkai revision `1a5f42a0`はIllustrator実機zstd fixtureを同一decoded SHA-256で抽出したものの、2-segmentのgenerated fixtureに失敗しました。このprofileを維持し、semantic parserもproject-owned実装として段階的に追加します。inkaiは隔離comparison oracleに限定します。[ADR 0001](adr/0001-modern-semantic-reader-strategy.md)に実測と判断を記録しています。

## Lossless token / section index

展開成功した各segmentは、decoded bytes自体を保持し、物理行単位のtokenで全byte範囲を隙間なく覆います。tokenは`start / content_end / end`を持ち、改行、未知operator、非UTF-8 byte、NULを正規化しません。

`%AI*_Begin* / %AI*_End*`と`%%Begin* / %%End*`はsection spanとして索引化します。sectionはsemantic ASTではなく、未知領域を欠落させず後続parserが段階的に解釈するためのread-only indexです。閉じていないsectionはbytesを保持したままwarningにします。

## Lexer / CSTとsemantic projection

展開済みbytesを元PDFから独立した入力としてproject-owned lexerへ渡します。lexerのlexeme spanはdecoded bytesを隙間なく覆い、whitespace、comment、literal string、name、number、delimiter、operator、opaque byteを正規化しません。CST statementは各operator tokenと、そのoperatorが消費するoperand tokenのexact `start / end`を保持します。

profile v2の対応範囲は次のとおりです。

| 項目 | 投影 | 制約 |
| --- | --- | --- |
| layer | `Document.layers` | `%AI5_BeginLayer`、`Lb`、`Ln`、`LB`、XMLUIDを認識 |
| group | `Layer.groups` / `Group.groups` | 実機の`u` / `U`を再帰構造として投影。異種item順は`item_order`、各nodeの親とsource内indexは`unknown.modern_source`に保持 |
| path geometry | 各containerの`paths` | `m` / `L` / `C` / `c` / `v` / `y`、`h` / `H`、paint終端を認識。Bézier handleを保持し、明示closeまたはpaint operatorのcaseから`closed`を決める |
| CMYK / RGB paint | `Path.fill` / `Path.stroke` | `k` / `K`、3成分`Xa` / `XA`はRGB、実機の7成分`Xa` / `XA`は先頭4成分をCMYKとして投影し、末尾RGB alternateもsource evidenceに保持。必要色が未証明ならpathをpartial化 |
| compound path | `CompoundPath` | 実機の`*u` / `*U`とsubpath直前の`D`を読み、component順とpositive / negative polarityを保持 |
| clipping | `ClippingGroup` | 実機の`q` / `Q`内で、`h` / `H`後の`W` / `W*`と`n`で終わるmaskをcontent pathから分離。実機ではcontentがmaskより先でも順序に依存せず分類し、`q`開始時のfill / stroke / width / polarityを対応する`Q`で復元 |
| AI11 text | `TextFrame`または`semantic.partial_nodes` | ASCII85 text documentを入れ子上限付きで読み、story indexと本文、AdobeNoteAttributeのidentity/nameを保持。下記の配置証拠を満たす場合だけ`TextFrame`化 |

各投影pathの`unknown.modern_source`はsegment名、object span、各geometry operator span、親containerとitem indexを持ちます。`modern_style_spans`はfill、stroke、stroke width、polarityを設定したstatementのexact spanを指します。group / compound / clippingにもopeningからclosingまでのspanとoperator spanがあります。partial nodeはknown / missing field、decoded span、親ID、元stack index、追加のevidence spanを持ちます。

source metadataまたは生成規則から同じIDが複数nodeへ割り当てられた場合、全階層をpreorderで走査し、最初のIDを維持して後続を`~2`、`~3`のsuffixで一意化します。各containerの`item_order`とpartialの親参照は最終node identityから同期し、source noteによる改名と重複suffixの両方を追従して`modern_duplicate_node_id`を返します。unknown operatorは名前、件数、first spanを、unknown statementはspanとSHA-256を返します。元decoded bytesは引き続き`PrivateDataSegment.decoded_bytes`が唯一の正であり、semantic resultがbytesを置換・破棄することはありません。

## Text配置の証拠境界

Illustrator 30.7.0実機fixtureでは、AI11 text document内にstory本文、font/paint resource、glyph geometry、6成分のplacementらしきmatrixが存在することを確認しました。しかし、そのmatrixとlayer streamの`StoryIndex`を結ぶsource-localなindex、およびdocument座標への変換基準をprofile v2で証明できませんでした。このため実機fixtureのtextは、本文を読めても`x / y / font_size / font_name / fill`を推測せずpartialのままです。reasonは未証明fieldを列挙し、text object span、AI11 text document span、identity note span、親group/layerとitem indexを返します。

`py-ai-text:` AdobeNoteAttributeに次の全fieldがsource内へ明示されている場合だけ`TextFrame`へ昇格します。

- `coordinate_space: "document"`
- `x`, `y`, `font_size`, `font_name`
- RGBまたはCMYKの`fill`
- 任意の`tracking`, `rotation`, `alignment`, `area_width / area_height`, `leading`

本文は同じ`StoryIndex`のAI11 storyから得られる必要があります。必須fieldの欠落、値不正、座標空間不明ではpartialを維持します。これはIllustrator内部matrixを解釈できたと主張するものではなく、source-local metadataで証明できる限定profileです。

AI11 text documentはPDF direct-object parserの限定subsetを再利用しますが、専用の入れ子上限を適用します。ASCII85、構文、文字コード、深さ超過を含む解析例外はsemantic boundaryから流出させず、decoded span付き`modern_text_document_partial` warningへ変換します。これは壊れたtextを修復または推測する保証ではなく、元bytesを保持したまま部分解析として報告する保証です。

coverageはdecoded byte数、全operator数、対応 / 未知operator数、projected layer / recursive leaf path / group / compound / clipping / text数、全partial node数、partial text数、unknown statement byte数を分離して返します。`operator_ratio`は文書全体の意味対応率ではなく、lexerがoperatorとして認識したtokenに対する現在のoperator tableの割合です。

## Resource limits

既定値は次のとおりです。Python APIでは`ModernReadLimits`で小さくできます。

| 対象 | 既定上限 |
| --- | ---: |
| PDF全体 | 64 MiB |
| indirect object数 | 100,000 |
| 1 object | 16 MiB |
| reference / direct-object depth | 64 |
| AI11 text document nesting | 64 |
| semantic group / compound / clipping nesting | 64 |
| 1 raw PrivateData segment | 64 MiB |
| 1 decoded segment | 128 MiB |
| decoded segment合計 | 256 MiB |
| 物理行token数 / semantic lexeme数 | 各2,000,000 |
| 1 token / lexeme | 8 MiB |
| zstd window | 128 MiB |

上限超過、展開失敗、未対応filterではdecoded resultを成功扱いにしません。利用可能なraw span、raw bytes、raw hashは保持します。入力は読み取り専用で開き、外部linkを解決せず、ファイルを書き換えません。

## Fixtureと期待manifest

`tools/generate_modern_ai_fixture.py`は第三者sample bytesを使わず、classic xrefを含む最小PDF-compatible AIを決定的に生成します。期待するsegment順序、filter、size、raw/decoded SHA-256は`tests/fixtures/manifests/modern-private-data.json`に固定しています。generatorの再実行結果がfixture bytesと一致することもテストします。

同manifest schema v2には、このプロジェクトが生成しIllustrator 30.7.0でnative保存した次の実機zstd fixtureを記録しています。source/raw/decoded sizeとSHA-256、semantic node countを固定し、Illustratorなしで同じ結果を回帰テストします。

- `examples/styled-table.native.ai`: 平坦なtable、16 paths、配置未証明text 20件
- `examples/campaign-variants.native.ai`: 典型的なbannerを含む3 groups、曲線、CMYK、配置未証明text 19件
- `examples/packaging-labels.native.ai`: 3 top-level groupsと2 nested groups、配置未証明text 20件
- `examples/cmyk-curve.native.ai`: `K`によるCMYK strokeとcubic Bézier
- `examples/mixed-stack.native.ai`: clipping / path / compoundの異種stack、mask/content、polarity

追加したnative fixtureも第三者sampleではなく、リポジトリ内のproject-authored sourceをIllustrator実機保存したものです。

## Python API

```python
from py_ai_illustrator import read_modern_ai

result = read_modern_ai("input.ai")
print(result.container_status)       # parsed
print(result.private_data_status)    # extracted / absent / partial / failed
print(result.semantic_status)        # partial
print(result.semantic.coverage.to_dict())
print(result.semantic.document.to_dict())

for node in result.semantic.partial_nodes:
    print(node.kind, node.parent_id, node.item_index)
    print(node.known_fields, node.missing_fields, node.reason)

for segment in result.segments:
    print(segment.index, segment.key, segment.filters)
    print(segment.raw_start, segment.raw_end, segment.raw_sha256)
    print(segment.decoded_sha256, len(segment.tokens), len(segment.sections))
```

## CLI

```bash
uv run py-ai inspect input.ai --json
uv run py-ai validate input.ai
```

`validate`はsemantic profile v2を適用できた実機profileを`classification: read_only_semantic_partial`として返します。semantic parserを適用できない抽出結果は`read_only_private_data`です。`safe_to_reserialize`は常に`false`です。通常PDFは`ordinary_pdf`、抽出失敗は`unconvertible`として区別されます。

非JSONの`inspect`はprofile名に加え、layer / recursive path / group / compound / clipping / projected text / partial node数を1行で表示します。JSONでは`reader_profile: modern-ai-read-only-v2`、`semantic.profile: modern-ai-semantic-read-only-v2`、`read_only: true`、`safe_to_reserialize: false`を明示します。
