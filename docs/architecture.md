# 推奨アーキテクチャ

## 設計原則

1. ファイル形式の AST と、利用者向けの図形モデルを分ける
2. 読めない要素を削除せず opaque data として保持する
3. reader / model / writer / renderer / validator を分離する
4. 「見た目を保つ」と「編集意味を保つ」を別々に検証する
5. エージェントには低レベル token 操作ではなく、安全な高レベル操作を公開する
6. Python意味モデル、JSON化可能なグラフィックIR、Illustrator sourceを分離する
7. JSON手書きをすべてのオーサリングworkflowへ強制しない

プロダクト上の代表ユースケース、UX原則、開発中の後方互換ポリシーは[開発原則と想定ユースケース](development-principles.md)に、意味モデルとsource of truthの詳細は[オーサリングモデル](authoring-model.md)に定義します。

## レイヤー構成

```text
Python design components + input data -> deterministic render --.
                                                               +-> Illustrator document IR
.ai bytes -> format/container reader -> lossless syntax tree --'          |
                                                                          v
  edit operations / agent tools
  -> writer(s)
       |- AI7/AI8 writer
       |- PDF/SVG writer
       `- modern AI patch writer (experimental)
  -> validators + preview renderer + semantic diff
```

## Python IR

最初の IR は Illustrator の全 API を模倣せず、安定した共通部分に絞ります。
IRはJSONへserializeできますが、JSON自体を複雑なデザインの主たるオーサリング言語とはしません。domain固有のcomponentはPython側でIRへrenderします。

```python
Document
  metadata
  color_space
  artboards: list[Artboard]
  layers: list[Layer]
  resources: ResourceTable
  source: LosslessSource | None

Layer / Group
  id, name, visible, locked
  transform
  children
  source_span

Path / CompoundPath
  geometry
  fill, stroke
  opacity, blend_mode
  clipping
  source_span

Text
  text
  frame/path geometry
  runs
  font references
  source_span

PlacedImage
  link or embedded bytes
  transform
  crop/mask
```

すべての node に安定 ID、元形式、source span、未知属性を持たせます。新規作成 node は `source_span=None` とし、writer が正規形で生成します。

## Reader

### 形式判定

- `%PDF-` なら PDF-compatible AI 候補
- `%!PS-Adobe` なら legacy AI/EPS 候補
- 拡張子だけでは判定しない
- PDF の `/PieceInfo/Illustrator/Private` と AI metadata を確認する

### modern AI

1. PDF object graph を読む
2. `AIPrivateData*` と metadata stream を順序どおり抽出
3. Flate / zstd 等を展開
4. lexer で token と byte span を保存
5. parser で concrete syntax tree を生成
6. reducer で Python IR に投影
7. PDF content stream から preview/fallback IR も作り、PrivateData との対応を記録

### legacy AI

Adobe Illustrator 7 specification の DSC comments と operator を読みます。古い形式を単なる中間形式とみなさず、最初の writer の仕様にも使います。

最初のlossless層として、元bytesと各物理行の`start/content_end/end` spanを保持する`LegacySource`を実装済みです。改行や未知byteを正規化しないため`LegacySource.to_bytes()`は入力と完全一致します。statementはPostScript文字列とinline commentを飛ばして末尾operator spanも索引化します。semantic parserはこの行索引から既知subsetをIRへ投影します。

`SourceReplacement`と`LegacySource.patched()`は、範囲内かつ非重複のspanだけを差し替えてsource mapを再構築します。これはlossless patch writerの低レベルprimitiveで、operatorの妥当性やIR preconditionは検証しません。高レベル操作はnode source spanとtyped editを結び、意味・source双方のpreconditionを確認してからreplacementを生成します。

semantic readerの公開境界は`LegacyReadResult(document, source, coverage, diagnostics)`です。新規作成`Document`にはsource provenanceがなく、既存ファイル由来の結果には元bytesと互換性証拠があります。operator/resource inventoryに未対応項目がある場合、通常の再serializeは既定で拒否し、明示的なloss policyなしに未知sourceを捨てません。

node provenanceはIR dataclassへ直接混在させず、`LegacyReadResult.origins`のside tableに保持します。`Path` nodeと排他的なfill / stroke field spanに加え、`TextFrame` nodeと単一`Tx` statementの本文spanを索引化し、`SetPathFill` / `SetPathStroke` / `ReplaceText`から`SourceReplacement`を生成します。同じcolor stateを複数nodeが参照する場合や、本文が複数`Tx`へ分割される場合はfield originをpatch可能として公開せず、局所性を証明できる操作だけを許可します。

## Writer 戦略

### Writer A: AI7/AI8

最初に実装します。対応機能は限定されますが、仕様が公開され、テキストベースで検査しやすく、Illustrator が開ける `.ai` をアプリなしで生成する最短経路です。透明など表現できない機能は、明示的なエラー、flatten、outline のいずれかを policy で選びます。

### Writer B: PDF/SVG

利用者が Illustrator で開いて編集できる交換形式です。`.ai` ネイティブ編集情報を保証しませんが、エージェント生成物をデザイナーへ渡す経路として早期に価値があります。

### Writer C: modern AI patch writer

既存ファイルを読み、対応 node の変更だけを PrivateData と PDF 表現の両方へ反映します。未知セクションはバイト列のまま保持します。新規ファイル生成より既存ファイルの局所変更を先に扱います。

### Writer D: modern AI full writer

新規 Document から PDF wrapper、PrivateData、resources、metadata を生成します。十分な fixtures と Illustrator 適合試験が揃うまで experimental 扱いにします。

## エージェント向けインターフェース

まず CLI を唯一の実行境界にし、どのコーディングエージェントからも利用可能にします。

```text
py-ai inspect input.ai --json
py-ai export input.ai --to json|svg|pdf|ai8
py-ai apply input.ai changes.json -o output.ai
py-ai validate output.ai --profile illustrator
py-ai render output.ai -o preview.png
py-ai diff before.ai after.ai --semantic --visual
```

`changes.json` は任意コードではなく、検証可能な操作列にします。

```json
{
  "operations": [
    {"op": "set_fill", "target": {"id": "logo-mark"}, "color": "#FF4D00"},
    {"op": "translate", "target": {"layer": "CTA"}, "dx": 12, "dy": 0},
    {"op": "replace_text", "target": {"name": "headline"}, "text": "New title"}
  ]
}
```

エージェント用プラグインは薄い adapter とし、次を担当します。

- ファイルを `inspect` して編集可能範囲を把握
- 曖昧な対象を名前・型・bounds・親階層で検索
- 操作計画を生成して `apply`
- `validate` と `diff` を必ず実行
- preview を確認して成果物と診断レポートを返す

ファイル解析ロジックをプラグイン内へ重複実装しません。プラグインを Codex、MCP、他エージェントへ展開しても、同じ CLI / Python API を呼ぶ構成にします。

## 安全性

- デフォルトは別名保存で、入力を上書きしない
- 未対応 feature が変更対象に交差する場合は停止する
- `--allow-flatten` や `--outline-text` は明示指定制
- 外部リンクを勝手に解決・送信しない
- 埋め込みデータと圧縮ストリームにサイズ上限を設ける
- malformed PDF、zip bomb 相当、循環参照を想定する
- 出力ごとに compatibility report を生成する

## 暫定ディレクトリ案

```text
py-ai-illustrator/
  docs/
  packages/
    py-ai-illustrator/      # Python library + CLI
  plugins/
    codex-illustrator/      # 将来のエージェント adapter
  tests/
    fixtures/
      generated/            # 自作・再配布可能 fixture
      manifests/            # 期待する semantic structure
    golden/
  tools/
    fixture-generator/
```

実装開始時は mono-repo にし、Python core の API が安定してから plugin を分離するのが扱いやすい構成です。
