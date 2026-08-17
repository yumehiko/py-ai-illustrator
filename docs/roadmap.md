# 開発ロードマップ

更新日: 2026-08-17

## 目的と進め方

このプロジェクトは、次の三つの基盤を段階的に成立させます。

1. **Conversion Core**: PythonのグラフィックIRとIllustrator sourceを安全に相互変換する
2. **Authoring**: 意味・規則・再利用可能な体裁を持つPython componentからIRを生成する
3. **Safe Edit / Agent**: 検証可能な編集APIとCLIを、エージェントから安全に利用する

各トラックの優先順位と設計判断は、[開発原則と想定ユースケース](development-principles.md)に定義した利用者の仕事とUXを基準にします。

旧Phase制は実装順と実際の進捗がずれたため、今後は三つの開発トラックと、トラック間の品質ゲートで管理します。各トラックは並行して進められますが、エージェント向けskillはConversion CoreとSafe Edit APIを迂回してファイルを操作しません。

## 現在地

| トラック | 状態 | 現在の到達点 | 主な未完了 |
| --- | --- | --- | --- |
| Conversion Core | 限定subsetで実証済み | legacy AI7/AI8 subsetのIR/JSON往復、source付きreader result、parse coverage、fill / stroke / text局所patch、Illustrator 30.7.0適合試験 | 残りのnode source span / typed patch、modern AI semantic reader/writer |
| Authoring | Create MVP成立 | Table、共通component境界、point/area text、linked image、group、Artboard、rigid transform、複数の実制作例 | image crop、link診断、semantic manifest、高度なlayout/resource共有 |
| Safe Edit / Agent | 設計段階 | inspect/export/validateとIllustrator検証CLI | selector、typed edit、precondition、dry-run、diff、preview、skill/plugin |

2026-08-17時点のローカル基準は、`pytest` 117件成功、`ruff check`成功です。

現在の変換保証は、次のように区別します。

- **対応subsetの意味往復**: Python IR -> legacy AI7/AI8 -> Python IRは自動テスト済み
- **Illustratorを介したlegacy往復**: Python生成AIをIllustrator 30.7.0でAI8互換保存し、Python IRへ戻す経路を実機確認済み
- **native materialization**: legacy AIからIllustratorを介してPDF-compatible native AIを生成可能
- **modern AI往復**: 未実装。現時点ではcontainer判定のみ
- **任意AIのlossless往復**: 未実装。未知operatorを保持したsemantic edit/writeには未到達

## 品質ゲート

### Gate A: Trusted Legacy Conversion

任意のlegacy AIを「変換可能」「部分的に解析可能」「変換不可」に安全に分類できる状態です。次の機能追加より、黙った情報損失を防ぐことを優先します。

- [x] readerが`document + source + coverage + diagnostics`相当の結果を返す
- [x] 認識済み・未認識operator/resourceをcompatibility reportへ列挙する
- [x] unsupported featureを含む通常のIR再serializeは既定で拒否する
- [ ] 全IR nodeへorigin/source spanを接続する（`Path`の排他的fill / strokeと単一`Tx`の`TextFrame`は完了）
- [ ] text / image / pathのtyped editを局所source patchへ変換する（fill / stroke / textは完了）
- [ ] patchのprecondition、競合、範囲外変更を検出する
- [ ] read-onlyでは元bytesが完全一致し、局所編集では対象span外が一致する
- [ ] 対応feature profileとIllustrator対象バージョンを文書化する

終了条件:

- 未対応featureが存在するファイルを「validだから安全に再保存可能」と誤判定しない
- 対応subsetではsemantic round-trip、未知領域を含むファイルではbyte-preserving patchを自動検証できる
- エラー、warning、変換policyのいずれにも分類されない損失がない

### Gate B: Safe Editing Surface

エージェントを使わなくても、同じ安全性で編集できるPython API / CLIを成立させます。

- [ ] stable selector (`id`, name, type, bounds, hierarchy)
- [ ] selectorが0件または複数件の場合の明示的停止
- [ ] `replace_text` / `set_fill` / `translate` / image差し替えのtyped operation
- [ ] operation preconditionと入力上書き禁止
- [ ] dry-runとimpact report
- [ ] before/after semantic diff
- [ ] compatibility report
- [ ] raster/PDF previewとvisual diff
- [ ] `inspect -> plan -> apply -> validate -> preview`をCLIだけで完結させる

終了条件:

- 「ロゴの色変更」「見出し差し替え」「レイヤー移動」の標準シナリオを曖昧な対象へ誤適用しない
- 未対応featureと変更範囲が交差する場合は停止する
- 検証失敗時に出力を成功扱いしない

## Track A: Conversion Core

### A0. Legacy subset spike — 完了

- [x] 内容に基づくlegacy AI / PDF-compatible AI / PDF / EPSの形式判定
- [x] 最小グラフィックIRとJSON serialization
- [x] legacy AI7 subset reader/writer
- [x] RGB/CMYK、直線/Bézier、stroke style
- [x] group、compound path、clipping、異種item stacking order
- [x] ASCII/CP932 point textとarea text intent
- [x] named Artboardとlinked image intent
- [x] 元bytes・物理行・operator spanを保持するlossless source prototype
- [x] resource limitと非重複local replacement primitive
- [x] Illustrator 30.7.0での構造・再保存・visual QA
- [x] MITライセンスとGPL実装をコア依存に含めない方針

### A1. Trusted legacy conversion — 最優先

Gate Aを満たします。

- [x] parse coverageとunknown operator/resource inventory
- [ ] node-level CST/source span（`Path`のfill / strokeと単一`Tx`の`TextFrame`本文は実装済み）
- [x] source付きreader resultと新規作成IRの明確な区別
- [x] strict-by-default reserializeと明示的な`discard` loss policy
- [ ] typed patch writer（`SetPathFill` / `SetPathStroke` / `ReplaceText`は実装済み）
- [ ] semantic diffとcompatibility report
- [ ] Illustrator生成fixtureの種類と対象バージョンを拡張

### A2. Modern AI reader

- [ ] 再配布可能なmodern AI fixtureと期待structure manifestを整備
- [ ] PDF object tree、PieceInfo、Illustrator PrivateDataを抽出
- [ ] Flate / zstd streamをresource limit付きで展開
- [ ] PrivateData lexer/CSTとbyte spanを保持
- [ ] 対応nodeを共通グラフィックIRへ投影
- [ ] PDF表示表現をpreview/fallbackとして抽出
- [ ] PrivateDataとPDF表示表現の不一致を診断
- [ ] `inkai`等の先行実装を隔離環境で比較し、ライセンス境界を維持

終了条件:

- 対応fixtureの主要nodeをIllustratorなしでdeterministicに列挙できる
- 未解釈PrivateDataを欠落させず保持できる
- container検査とsemantic検査を明確に区別できる

### A3. Modern AI patch writer

- [ ] 対応operatorのserializer
- [ ] source spanベースの局所更新
- [ ] PDF contentの再生成または同期
- [ ] metadata / xref / stream compression更新
- [ ] 未知sectionのbyte-preserving保持
- [ ] Illustratorありの適合試験

終了条件:

- 対応featureに限り既存modern AIを編集し、Illustratorで警告なく開ける
- 未編集の未知featureが保持される
- Illustrator再保存後のsemantic / visual diffが許容範囲内になる

### A4. 交換形式writer

- [ ] SVG writer
- [ ] PDF writer
- [ ] raster preview renderer
- [ ] flatten / outline / unsupported feature policy
- [ ] spot color、ICC、overprint等を追加する前のcolor policy整理

## Track B: Authoring

### B0. Create MVP — 成立

- [x] `RenderedComponent` / `LayerBuilder`
- [x] `Table` / `TableColumn` / `TableStyle`
- [x] `TextBlock` / native `AreaTextBlock` / `FontSpec`
- [x] rectangle / ellipse / polyline primitives
- [x] nested editable groupとstable item order
- [x] rigid affine transformとtext rotation
- [x] PNG/JPEG linked imageと同一出力先の`Links/` package
- [x] multiple Artboardsとnative materialization
- [x] 表、名札、ポスター、棚札、KPI、パッケージ、誌面、variant、カタログの作例

### B1. Create workflow completion — 次点

- [ ] linked imageのcontain / cover / clipping crop
- [ ] missing / modified link診断
- [ ] package移動後にsibling `Links/`へ安全に再リンクするmanifest / command
- [ ] component identityとsidecar semantic manifest
- [ ] font、color、spacing、document contextの共有resource/theme境界
- [ ] page分割と複合cell/component layout
- [ ] CP932以外のtext encoding profile
- [ ] textを含む非rigid transformの明示的policy

終了条件:

- Python sourceと入力データから同一IRをdeterministicに再生成できる
- 生成物のlink、font、semantic identityの欠損をcompatibility reportで検出できる
- Illustratorでの手修正後に、保持された意味metadataと失われたmetadataを区別できる

## Track C: Safe Edit / Agent

### C1. Agent-independent editing API

Gate Bを満たします。エージェント固有コードより先に、Python APIとCLIを安定させます。

想定コマンド:

```text
py-ai inspect input.ai --json
py-ai plan input.ai operations.json
py-ai apply input.ai operations.json -o output.ai
py-ai validate output.ai --profile illustrator
py-ai diff input.ai output.ai --semantic --visual
py-ai render output.ai -o preview.png
```

### C2. Codex skill / plugin

- [ ] Python package / CLIの対応APIを固定
- [ ] Codex plugin manifestとskillを追加
- [ ] 必要性が確認できた場合のみMCP serverを追加
- [ ] `inspect -> plan -> apply -> validate -> preview`をskill workflow化
- [ ] fixture作成・互換性レポート作成の開発者向けskill
- [ ] pluginを外してもCLI単体で同じ操作が可能であることを確認

エージェントadapterの責務は、対象の曖昧性解消、操作計画、CLI実行、結果説明に限定します。ファイル解析、変換、検証ロジックをplugin内へ重複実装しません。

## パッケージとリポジトリ方針

当面はmono-repoを維持し、物理リポジトリより先に依存方向を分離します。

```text
packages/
  py-ai-core/                # IR、source/CST、reader/writer、validation
  py-ai-authoring/           # component、template、layout、style
  py-ai-illustrator-bridge/  # Illustrator起動、native化、実機適合試験
plugins/
  codex-illustrator/         # CLI/APIを呼ぶ薄いagent adapter
```

依存方向は次に限定します。

```text
codex-illustrator ---------> public CLI/API
py-ai-authoring -----------> py-ai-core
py-ai-illustrator-bridge --> py-ai-core
py-ai-core ----------------> 他の層へ依存しない
```

別リポジトリ化は、次の条件が揃った時点で再評価します。

- グラフィックIRとpublic APIの互換性方針を固定できた
- 独立したrelease cadenceまたは利用者が生じた
- plugin marketplace配布など、Python packageと異なる配布要件が確定した

## 継続的な監視項目

この文書を更新するときは、少なくとも次を確認します。

- `uv run pytest`と`uv run ruff check .`の結果
- 新規実装がどのtrack / gateを前進させたか
- 対応feature profileと未対応featureが明記されているか
- round-tripがbyte、graphic semantics、design semanticsのどれを検証したか
- Illustrator実機試験のversion、OS、font、保存option
- fixtureが自作・生成物で、再配布条件を満たすか
- public APIの層間依存が逆転していないか
- README、architecture、authoring model、phase statusとの記述差分

進捗更新では、単に「往復成功」とせず、次のどれかを必ず記録します。

1. byte-preserving round-trip
2. graphic semantic round-trip
3. visual round-trip
4. native editability round-trip
5. design semantic round-trip

## 現在の推奨着手順

1. Gate A / A1のsource spanをimage/path geometryと複数`Tx` textへ拡張する
2. typed patchをtranslate / image差し替えへ拡張する
3. B1のimage crop・link診断を並行実装
4. Gate B / C1のselector、dry-run、impact report
5. A2のmodern AI reader縦切り
6. CLIが安定してからC2のCodex skill/plugin

Phase 0の詳細な実機試験記録は[Phase 0の実装状況](phase0-status.md)と[Illustrator適合試験](illustrator-testing.md)へ残します。
