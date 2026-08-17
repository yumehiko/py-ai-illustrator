# Modern AI read-only feature profile

更新日: 2026-08-17

## 保証境界

このprofileは、PDF-compatible modern AIの最初の読み取り専用境界です。入力を変更せず、PDF containerから`/PieceInfo/Illustrator/Private`をたどり、`AIPrivateData*` streamを決定的な順序で抽出・展開・索引化します。

次の三状態を混同しません。

| 状態 | JSON field | 意味 |
| --- | --- | --- |
| container読取 | `modern_ai.container.status` | bounded PDF readerが対象containerを構造的に読めた |
| PrivateData抽出 | `modern_ai.private_data.status` | `absent` / `extracted` / `partial` / `failed` |
| semantic対応 | `modern_ai.semantic.status` | 現在は常に`unsupported`。Document IRを生成していない |

`extracted`はmodern AIを編集・再保存できるという意味ではありません。writer、patch、PDF表示表現との同期、Document IRへのsemantic projectionは保証対象外です。

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

## Resource limits

既定値は次のとおりです。Python APIでは`ModernReadLimits`で小さくできます。

| 対象 | 既定上限 |
| --- | ---: |
| PDF全体 | 64 MiB |
| indirect object数 | 100,000 |
| 1 object | 16 MiB |
| reference / direct-object depth | 64 |
| 1 raw PrivateData segment | 64 MiB |
| 1 decoded segment | 128 MiB |
| decoded segment合計 | 256 MiB |
| token数 | 2,000,000 |
| 1 token | 8 MiB |
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
print(result.semantic_status)        # unsupported

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

`validate`はcontainerとPrivateDataのread-only検査に成功したmodern AIを`classification: read_only_private_data`として返します。`safe_to_reserialize`は常に`false`です。通常PDFは`ordinary_pdf`、抽出失敗は`unconvertible`として区別されます。
