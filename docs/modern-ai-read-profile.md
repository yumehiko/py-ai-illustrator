# Modern AI read-only feature profile

更新日: 2026-08-17

## 保証境界

このprofileは、PDF-compatible modern AIの最初の読み取り専用semantic境界です。入力を変更せず、PDF containerから`/PieceInfo/Illustrator/Private`をたどり、`AIPrivateData*` streamを決定的な順序で抽出・展開・索引化し、証明できるlayer / path / paintだけを共通`Document` IRへ投影します。

次の三状態を混同しません。

| 状態 | JSON field | 意味 |
| --- | --- | --- |
| container読取 | `modern_ai.container.status` | bounded PDF readerが対象containerを構造的に読めた |
| PrivateData抽出 | `modern_ai.private_data.status` | `absent` / `extracted` / `partial` / `failed` |
| semantic対応 | `modern_ai.semantic.status` | `unsupported` / `partial` / `supported`。実機profileは未知要素とpartial textを含むため`partial` |

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

最小縦切りの対応範囲は次のとおりです。

| 項目 | 投影 | 制約 |
| --- | --- | --- |
| layer | `Document.layers` | `%AI5_BeginLayer`、`Lb`、`Ln`、`LB`、XMLUIDを認識 |
| path geometry | `Layer.paths` | `m` / `L` / `C`とpaint終端を認識。2点未満はpartial。paintされないpathも新しい`m`、`%AI5_EndLayer`、segment終端で捨てずにpartial化 |
| RGB fill / stroke | `Path.fill` / `Path.stroke` | `Xa` / `XA`の明示RGB成分と`w`のみ。未対応paintは推測しない |
| AI11 text | `semantic.partial_nodes` | ASCII85 text documentを入れ子上限付きで読み、story indexと本文、AdobeNoteAttributeからidentity/nameを保持。absolute placementを証明できないため`TextFrame`を捏造しない |

各投影pathの`unknown.modern_source`はsegment名、object span、各operator spanを持ちます。partial nodeもknown / missing fieldとdecoded spanを持ちます。source metadataまたは生成規則から同じIDが複数nodeへ割り当てられた場合、最初のIDを維持し、後続を`~2`、`~3`のsuffixで一意化して`modern_duplicate_node_id`を返します。`Layer.item_order`も一意化後のpath IDへ同期します。unknown operatorは名前、件数、first spanを、unknown statementはspanとSHA-256を返します。元decoded bytesは引き続き`PrivateDataSegment.decoded_bytes`が唯一の正であり、semantic resultがbytesを置換・破棄することはありません。

AI11 text documentはPDF direct-object parserの限定subsetを再利用しますが、専用の入れ子上限を適用します。ASCII85、構文、文字コード、深さ超過を含む解析例外はsemantic boundaryから流出させず、decoded span付き`modern_text_document_partial` warningへ変換します。これは壊れたtextを修復または推測する保証ではなく、元bytesを保持したまま部分解析として報告する保証です。

coverageはdecoded byte数、全operator数、対応 / 未知operator数、projected layer / path数、partial text数、unknown statement byte数を分離して返します。`operator_ratio`は文書全体の意味対応率ではなく、lexerがoperatorとして認識したtokenに対する現在のoperator tableの割合です。

## Resource limits

既定値は次のとおりです。Python APIでは`ModernReadLimits`で小さくできます。

| 対象 | 既定上限 |
| --- | ---: |
| PDF全体 | 64 MiB |
| indirect object数 | 100,000 |
| 1 object | 16 MiB |
| reference / direct-object depth | 64 |
| AI11 text document nesting | 64 |
| 1 raw PrivateData segment | 64 MiB |
| 1 decoded segment | 128 MiB |
| decoded segment合計 | 256 MiB |
| 物理行token数 / semantic lexeme数 | 各2,000,000 |
| 1 token / lexeme | 8 MiB |
| zstd window | 128 MiB |

上限超過、展開失敗、未対応filterではdecoded resultを成功扱いにしません。利用可能なraw span、raw bytes、raw hashは保持します。入力は読み取り専用で開き、外部linkを解決せず、ファイルを書き換えません。

## Fixtureと期待manifest

`tools/generate_modern_ai_fixture.py`は第三者sample bytesを使わず、classic xrefを含む最小PDF-compatible AIを決定的に生成します。期待するsegment順序、filter、size、raw/decoded SHA-256は`tests/fixtures/manifests/modern-private-data.json`に固定しています。generatorの再実行結果がfixture bytesと一致することもテストします。

同manifestには、このプロジェクトが生成しIllustrator 30.7.0でnative保存した`examples/styled-table.native.ai`のzstd profileも記録しています。この実fixtureでsource、raw、decoded hashが固定され、Illustratorなしで同じPrivateDataを抽出できることをテストします。

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
    print(node.kind, node.known_fields, node.missing_fields)

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

`validate`はsemantic最小縦切りが成立した実機profileを`classification: read_only_semantic_partial`として返します。semantic parserを適用できない抽出結果は`read_only_private_data`です。`safe_to_reserialize`は常に`false`です。通常PDFは`ordinary_pdf`、抽出失敗は`unconvertible`として区別されます。
