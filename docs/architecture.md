# アーキテクチャ

## 三層構造

```text
第三層: エージェント
  自然言語・素材 -> 第二層のモデル作成 / 改訂
          |                       |
          v                       v
第二層: デザインモデル -------> 第一層の公開API / CLI
  component / layout / theme       inspect / validate / preview
          |
          v
第一層: 変換
  .ai source <-> lossless syntax / graphic IR <-> writer / patch
```

依存方向は上位から下位だけです。第一層はデザインモデルやagent固有処理を知りません。第三層は変換・検証を再実装せず、第一・第二層の公開境界を利用します。製品原則とユースケースは[開発原則](development-principles.md)、進行順は[ロードマップ](roadmap.md)を正とします。

## 第一層: 変換

第一層は`.ai`と低水準Python IRを安全に相互変換し、新規IRを選択したbackendで成果物へcompileします。

```text
.ai bytes
  -> format / container reader
  -> source-preserving CST + coverage + diagnostics
  -> graphic IR
  -> typed edit / writer
  -> validate + semantic diff + visual verification
```

主な責務:

- 拡張子ではなく内容でlegacy AI / PDF-compatible AI / PDF / EPSを判定する
- 元bytes、source span、未知operatorを保持する
- 対応部分だけをIRへ投影し、未対応部分を成功扱いで捨てない
- 新規IRをserializeし、既存sourceは可能な限り局所patchする
- 新規制作ではIllustrator 2026 DOMをdirect native compilerとして利用する
- selector、precondition、dry-run、別名保存、再読込検証を提供する
- byte、graphic semantics、visual、native editabilityを別々に検証する

共通IRはDocument、Artboard、Layer、Group、Path、CompoundPath、ClippingGroup、TextFrame、LinkedImageと、geometry / paint / stacking / stable IDを表現します。JSONはIRの交換・fixture・diff形式であり、複雑なデザインの主たる記述言語ではありません。

legacyの保証範囲は[legacy feature profile](legacy-feature-profile.md)、modern readerは[modern read profile](modern-ai-read-profile.md)を参照してください。modern AIではPrivateDataとPDF表示表現を別の証拠として扱い、片側だけの更新を完成したwriterとは呼びません。

## 第二層: デザインモデル

第二層はデザインの意図、規則、再利用可能な体裁をPythonで表し、第一層のIRへ決定的にrenderします。

- component / template / variant
- layout / theme / style / resource
- semantic identityと入力データ検証
- 複数サイズや素材差し替えを同じ規則から再生成する仕組み

一般の`.ai`から「価格表」「商品カード」等の意味を推測しません。意味の往復には元Python source、stable ID、metadata、sidecar manifest等の根拠が必要です。詳細は[オーサリングモデル](authoring-model.md)を参照してください。

## 第三層: エージェント

第三層は利用者の依頼と素材から第二層のPythonモデルを作成・改訂し、第一層で出力と検証を行います。

- 対象と対応範囲をinspectする
- 曖昧性を解消してからmodelまたはtyped operationを作る
- apply後にvalidate、semantic diff、previewを実行する
- 未対応・不一致・検証失敗を説明し、推測で成功扱いにしない

skill / pluginは薄いadapterです。ファイルparser、writer、renderer、validationをplugin内へ複製しません。

## Source of truth

- 既存デザイン編集: 元の`.ai`
- パラメトリック生成: Python component、template、入力データ
- ハイブリッド運用: graphic semanticsとdesign semanticsの保持を分けて報告する

入力は既定で上書きせず、外部linkを勝手に解決・送信しません。圧縮stream、再帰構造、入力サイズには上限を設けます。

## 実行時と検証環境

componentからIRを生成するauthoring、IR validation、JSON交換、legacy変換・編集はIllustratorなしで動作させます。新規制作のproduction向けnative `.ai` compileはIllustrator 2026を必須backendとし、暗黙のactive documentへ依存しない明示的な実行境界に置きます。既存modern `.ai`のsource-preserving編集と新規native compileは別の責務です。決定理由と昇格条件は[ADR 0002](adr/0002-direct-native-authoring-backend.md)、確認済み環境と手順は[Illustrator適合試験](illustrator-testing.md)へ記録します。

当面はmono-repoを維持します。将来分割する場合も、変換core、design model、Illustrator backend、agent adapterの依存方向を変えません。
