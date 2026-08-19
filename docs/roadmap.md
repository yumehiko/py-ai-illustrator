# 開発ロードマップ

更新日: 2026-08-19

優先順位は[開発原則と想定ユースケース](development-principles.md)を基準にします。この文書は現在地と次の作業だけを扱い、対応operatorや実機試験の詳細はfeature profileとtesting文書へ置きます。

## 三つの層

| 層 | 役割 | 現在地 |
| --- | --- | --- |
| 1. 変換層 | `.ai`と低水準Python IRの相互変換・編集・検証 | legacy相互変換とmodern read profile v2が成立 |
| 2. デザインモデル層 | 意図・規則・再利用可能な体裁を持つPythonモデル | component authoring MVPが成立 |
| 3. エージェント層 | 自然言語と素材から第二層のモデルを作り、第一層で検証・出力 | 未着手 |

依存方向は `エージェント層 -> デザインモデル層 -> 変換層` とします。エージェント層は検証のため変換層の公開APIも利用できますが、下位層は上位層へ依存しません。

ローカル基準は`pytest` 218件成功、`ruff check .`成功です。

## 1. 変換層

### 完了

- 内容に基づくlegacy AI / PDF-compatible AI / PDF / EPS判定
- legacy AI7/AI8 subsetのreader / writerと意味往復
- source span、未知byte、coverage、diagnosticsを保持するlossless reader
- path / text / image / containerのtyped local patch
- selector、precondition、plan / apply、semantic diffを持つ安全編集API / CLI
- modern AIのbounded PrivateData抽出、exact-span CST、read-only semantic profile v2
- modern layer / group / path / compound / clipping、RGB / CMYK、Bézier、partial textの投影
- Illustrator 30.7.0によるlegacy / native fixture適合試験
- `Document` IRからIllustrator 2026 DOMを直接構築するnative compiler
- 一時保存、再open、DOM照合、PDF-compatible判定後だけ成果物を確定するfail-closed compile
- `quarterly-kpi-report`、`editorial-brochure`、`product-catalog`によるproduction昇格gate

正確な保証境界は[legacy profile](legacy-feature-profile.md)と[modern read profile](modern-ai-read-profile.md)を参照してください。

### 未完了

1. **Preview & Verification**
   - modern AI内のPDF表示表現を抽出する
   - deterministicなraster previewとvisual diffを提供する
   - PrivateDataとPDF表示表現の不一致を診断する
   - `inspect -> plan -> apply -> validate -> preview`をCLIで完結させる
2. **Modern AI writer**
   - source spanベースでPrivateDataを局所更新する
   - PDF表示表現、metadata、xref、compressionを同期する
   - 未知sectionを保持し、Illustratorでの再編集性を検証する
3. **安全編集APIの完成**
   - name / bounds / hierarchy selector
   - visual diffをoperation impact検証へ接続する
4. **交換形式**
   - 必要性に応じてSVG / PDF writerとflatten policyを追加する

Direct native backendは新規制作を対象とし、既存modern `.ai`の安全編集を代替しません。Modern writerはbyte、graphic semantics、visual、native editabilityの四つを別々に検証し、PDF側だけ、またはPrivateData側だけを更新して成功扱いにしません。

## 2. デザインモデル層

### 完了

- `RenderedComponent` / `LayerBuilder`
- Table、text block、area text、基本図形、group、Artboard
- rigid transformとtext rotation
- linked image packaging
- 複数の実制作exampleによるdeterministic render

### 未完了

- image contain / cover / clipping crop
- missing / modified link診断と安全な再link
- component identityとsidecar semantic manifest
- font / color / spacing / document contextの共有theme
- page分割、複合layout、text encoding / non-rigid transform policy

第二層は第一層のIRへrenderします。一般の`.ai`から根拠なくデザイン意図を逆推定しません。

## 3. エージェント層

第三層は第一・第二層の公開境界が固まってから実装します。

- Codex skill / plugin
- 素材と依頼から第二層のPythonモデルを生成・改訂するworkflow
- `inspect -> plan -> apply -> validate -> preview`の実行と結果説明
- 対象が曖昧、未対応、検証失敗の場合の停止とユーザーへの選択肢提示

pluginは薄いadapterとし、parser、writer、renderer、validationを再実装しません。pluginを外してもPython API / CLIだけで同じ操作を実行できることを条件にします。

## 次の着手順

1. 第一層のPreview & Verification
2. 第一層のselector / safe edit API完成
3. 第一層のmodern AI patch writer
4. 第二層のimage workflowとsemantic identity
5. 第三層のskill / plugin

Direct native backendは3 fixtureのproduction昇格gateを完了し、新規制作の主経路になりました。次はPreviewをdirect backend、modern reader / writer、安全編集の共通検証基盤として完成させます。第三層を先行させて未完成の変換処理をskill内へ埋め込みません。

## リポジトリ方針

当面はmono-repoを維持し、物理分割より依存方向を先に分離します。別repository化は、public API、独立release cadence、配布要件のいずれかが具体化した時点で再評価します。

進捗更新では「往復成功」だけで済ませず、byte-preserving、graphic semantic、visual、native editability、design semanticのどれを検証したかを明記します。
