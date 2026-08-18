# 開発ロードマップ

更新日: 2026-08-18

優先順位は[開発原則と想定ユースケース](development-principles.md)を基準にします。この文書は現在地と次の作業だけを扱い、対応operatorや実機試験の詳細はfeature profileとtesting文書へ置きます。

## 三つの層

| 層 | 役割 | 現在地 |
| --- | --- | --- |
| 1. 変換層 | `.ai`と低水準Python IRの相互変換・編集・検証 | v1完了 |
| 2. デザインモデル層 | 意図・規則・再利用可能な体裁を持つPythonモデル | component authoring MVPが成立 |
| 3. エージェント層 | 自然言語と素材から第二層のモデルを作り、第一層で検証・出力 | 未着手 |

依存方向は `エージェント層 -> デザインモデル層 -> 変換層` とします。エージェント層は検証のため変換層の公開APIも利用できますが、下位層は上位層へ依存しません。

ローカル基準は`pytest`全件成功、`ruff check .`成功です。件数は機能追加で変わるため固定しません。

## 1. 変換層

### 完了

- 内容に基づくlegacy AI / PDF-compatible AI / PDF / EPS判定
- legacy AI7/AI8 subsetのreader / writerと意味往復
- source span、未知byte、coverage、diagnosticsを保持するlossless reader
- path / text / image / containerのtyped local patch
- selector、precondition、plan / apply、semantic diffを持つ安全編集API / CLI
- modern AIのbounded PrivateData抽出、exact-span CST、read-only semantic profile v2
- modern layer / group / path / compound / clipping、RGB / CMYK、Bézier、partial textの投影
- PDF表示証拠、normalized raster preview、pixel visual diff、timestamp不一致診断
- modern synchronized patch v1のfill / stroke / rectangle移動 / 一意text
- modern patchのPrivateData / PDF content / XMP / timestamp / xref / compression同期
- id / name / bounds / hierarchy selectorとmodern operation impact visual検証
- legacy IR reference previewとsafe editのvisual impact検証
- modern同一node順序付きatomic batch、証明済みpath-only container展開
- 証明可能なpaint / geometry / textのPrivateData / PDF cross-representation診断
- modern成果物のIllustrator current-format再保存・再open試験runnerと最終matrix
- Illustrator 30.7.0によるlegacy / native fixture適合試験

正確な保証境界は[legacy profile](legacy-feature-profile.md)と[modern read profile](modern-ai-read-profile.md)を参照してください。

### v1完了判定

2026-08-18に`scripts/test_layer1_illustrator.py`をIllustrator 30.7.0で実行し、3/3ケースが合格しました。legacy paint/translate batchと、modern fill/translate/text atomic batch、modern Bézier strokeについて、open、再保存、再open、DOM構造、text identity、PrivateData/PDF再parse、timestamp、bounded visual normalizationを確認済みです。

### v1の明示的な非対応（後続profile候補）

- modern linked image patch。現行semantic projectionに配置・link・PDF XObjectを同一対象と証明するfixture/evidenceがないため推測更新しない。legacy typed patchとnative materializationのlinked image経路は対応済み。
- partial textや非矩形pathを含むmodern container。全descendantを同期できるcontainerだけをcapabilityとして許可する。
- gradient、pattern、spot color、effect、artboard等、source-localに両表現を証明できないmodern operation。
- 任意のPrivateData / PDF全体が意味一致するという一般保証。v1は証明可能なpaint / geometry / textとtimestampを診断する。
- SVG / standalone PDF writer。第一層v1の`.ai`往復・安全編集・previewに必須のconsumer要件がないため、flatten policyを決める具体案件が出た時点で追加する。

Modern writerはbyte、graphic semantics、visual、native editabilityの四つを別々に検証します。PDF側だけ、またはPrivateData側だけを更新して成功扱いにしません。

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

1. 第二層のimage workflowとsemantic identity
2. 第三層のskill / plugin

第三層を先行させて未完成の変換処理をskill内へ埋め込みません。

## リポジトリ方針

当面はmono-repoを維持し、物理分割より依存方向を先に分離します。別repository化は、public API、独立release cadence、配布要件のいずれかが具体化した時点で再評価します。

進捗更新では「往復成功」だけで済ませず、byte-preserving、graphic semantic、visual、native editability、design semanticのどれを検証したかを明記します。
