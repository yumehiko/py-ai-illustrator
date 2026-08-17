# 開発ロードマップ

## 近接マイルストーン

### MVP-A: Create — 新規の編集可能なAIを作る

- [x] point text / native AreaText
- [x] PNG/JPEG linked imageと同一出力先の`Links/` package
- [x] 基本図形・Bézier・塗り・線
- [x] layer / nested group / stacking order / multiple Artboards
- [x] Illustrator保存・再オープン・PDF/PNG visual QA
- [ ] linked imageのcontain / cover / clipping crop
- [ ] missing / modified linkをまとめたcompatibility report
- [ ] package移動後にsibling `Links/`へ安全に再リンクするmanifest / command

### MVP-B: Edit — 対応要素を安全に編集する

- [ ] stable selector (`id`, name, type, bounds, hierarchy)
- [ ] text / image / pathのtyped editとprecondition
- [ ] dry-run、impact report、semantic diff、preview
- [ ] component identityとsidecar semantic manifest

### MVP-C: Modern AI — 既存modern AIを高忠実度で往復する

- [ ] PrivateData semantic reader
- [ ] node-level CSTとsource spanベースの局所patch
- [ ] 未知featureのlossless保持とIllustrator再保存検証

## Phase 0: 技術スパイク

目標: 2〜3 種類の `.ai` で構造抽出と変更可能性を実証する。

- `inkai` を隔離した検証環境で動かす
- 単純な Illustrator fixtures を用意する
  - 長方形 1 個
  - 2 artboards + 2 layers
  - path + text + linked image
- PDF object tree と PrivateData をダンプ
- 同一ファイルを Illustrator で一項目ずつ変え、差分を記録
- `parse -> IR -> JSON` の POC
- AI8 writer で長方形と path を新規生成

終了条件:

- Illustrator なしで fixture の主要 node を列挙できる
- Python から生成した AI8 ファイルを現行 Illustrator で開ける
- reader の採用方針とライセンス方針を決定できる

進捗（2026-08-15）:

- 完了: 依存ゼロの形式判定、最小Python IR、legacy AIサブセットreader/writer、JSON往復、CLI、自動テスト、Illustrator 30.7.0実機適合試験、MITライセンス決定
- 実証済みsubset: RGB/CMYK、直線/Bézier、native stroke style、compound、clipping、ASCII/CP932 point/area text intent、異種itemのstacking order、path ID・名前のAI8再保存保持
- 意味モデル実装済み: Python `Table`、共通`RenderedComponent` / `LayerBuilder`、`TextBlock` / `AreaTextBlock`、名札・ポスター・入れ子棚札・KPI chart・誌面作例からIRへのdeterministic render
- native出力実証済み: legacy textをIllustrator経由でnative point/area TextFrameへ変換し、font/size/fill/leading/揃えと安定IDを設定してPDF-compatible AIとして保存
- 基盤実装済み: legacy元bytes・物理行改行・operator byte spanを保持するlossless source map、resource limit、非重複local patch primitive
- 実証済み画像subset: PNG/JPEGを`Links/`へpackageし、placeholderからlinked `PlacedItem`へnative復元。埋め込みは既定にしない
- 未完了: 現代AI fixtureのPrivateData semantic reader、`inkai`比較環境、node-level CSTとtyped patch、CP932以外のtext、image crop、non-rigid transform
- 詳細: [Phase 0 の実装状況](phase0-status.md)

## Phase 1: Reader + IR + inspection CLI

- format detector
- lossless token model
- document / artboard / layer / group / path
- fill / stroke / transform
- resource table
- `inspect`, `export --to json`, `diff --semantic`
- malformed input と resource limit

終了条件:

- 対応 fixture の parse 結果が deterministic
- 未知 operator が欠落せず保持される
- 同じ入力の read-only round trip で PrivateData が同一、または意味的同一

## Phase 2: 限定 writer

- AI7/AI8 serializer
- SVG writer
- PDF writer
- raster preview
- compatibility report
- `validate`, `render`, visual diff

終了条件:

- 基本図形、パス、色、レイヤー、単純テキストを Illustrator で開ける
- サポート外表現は黙って壊さず、error/warning/policy のいずれかになる

## Phase 3: Agent editing API

- selector (`id`, name, type, bounds, hierarchy)
- typed edit operations
- operation preconditions
- dry-run と impact report
- before/after semantic diff
- プレビューを含む agent workflow
- Python component / templateからIRへのdeterministic render境界
- semantic metadataとsidecar manifest

終了条件:

- 「ロゴの色変更」「見出し差し替え」「レイヤー移動」などの標準シナリオを、自然言語から安全に実行できる
- 同名 node が複数ある場合に誤編集せず停止できる
- Pythonで定義した表やカードのvariantを、共有styleから再現可能に生成できる

## Phase 4: modern AI patch writer

- 対応 operator の serializer
- source span ベースの局所更新
- PDF content の再生成または同期
- metadata / xref / stream compression 更新
- Illustrator ありの適合試験

終了条件:

- 対応 feature に限り、既存 modern `.ai` を編集して Illustrator で警告なく開ける
- 未編集の未知 feature が保持される
- Illustrator 再保存後の semantic diff が許容範囲内

## Phase 5: plugin 化

- Python package / CLI を安定版として固定
- Codex plugin manifest、skill、必要なら MCP server を追加
- inspect → plan → apply → validate → preview のワークフローを skill 化
- fixture 作成・互換性レポート作成の開発者向け skill

終了条件:

- プラグインを外しても CLI 単体で同じ操作が可能
- プラグインが入力を直接上書きしない
- 検証失敗時に出力を成功扱いしない

## 最初の意思決定

実装前に次を決めます。

1. ライセンス（決定済み）
   - 本体は MIT
   - permissive な独自 core を維持し、GPL の `inkai` は任意の隔離 adapter として評価する
2. MVP writer
   - AI8 を正式成果物にする
   - SVG/PDF を正式成果物、AI8 を互換出力にする
3. 対応保証
   - Illustrator の対象バージョン
   - OS とフォント環境
   - 対応 feature profile
4. fixture の権利
   - 自作・生成 fixture のみリポジトリへ格納
   - 顧客ファイルはハッシュ・構造 manifest のみ保持

## 推奨する次の一手

MVP-Aの文字・linked image・図形を含む最小作成セットがIllustrator 30.7で成立しました。次はlinked imageのcontain / cover / clipping cropとlink診断を追加し、同時に未知operatorを保持するlossless token/CSTをtext / image / pathのtyped editへ接続します。
