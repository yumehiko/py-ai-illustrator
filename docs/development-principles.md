# 開発原則と想定ユースケース

## この文書の目的

この文書は、機能を追加するときの判断基準を定めます。ファイル形式として実装できるものを無差別に増やすのではなく、利用者がエージェントと一緒にデザインを作成・改訂する具体的な仕事を、速く、安全に、再利用可能な形で完了できることを目指します。

ここに挙げるユースケースは網羅的な仕様ではありません。ただし、設計判断や優先順位が曖昧なときに立ち戻る代表例です。

## 解決したい代表的なユースケース

### 1. 既存デザインの素早い改訂

利用者が作成したデザインデータをもとに、テキスト、数値、色、画像等をエージェントへ指示して変更し、改訂版を制作します。

このユースケースで重要なUX:

- 元の`.ai`をsource of truthとして扱う
- 変更前に、編集可能範囲と未対応featureを利用者へ説明する
- `id`、名前、型、bounds、親階層等から対象を特定し、曖昧なら停止する
- 指示された要素と必要な従属箇所だけを変更する
- 未知データや未編集要素を保持し、入力ファイルを既定で上書きしない
- apply前の計画、before/after diff、preview、compatibility reportを確認できる
- 見た目だけでなく、テキストやパス等の編集可能性が保たれたか検証する

### 2. 支給素材から複数サイズのバナーを量産

支給された画像、テキスト、ロゴ、色、サイズ一覧等を入力として、共通したデザイン意図を持つ複数サイズ・複数variantのバナーをエージェントで制作します。

このユースケースで重要なUX:

- 入力素材、文言、サイズ、variant条件を明示的なデータとして扱う
- component、layout規則、themeを共有し、サイズごとの手作業コピーを避ける
- contain / cover / crop、文字折り返し、余白、優先順位等のlayout policyを明示する
- 各variantを独立したArtboard、group、または成果物として追跡できる
- 全variantを一括生成・検証し、overflow、missing link、missing font等を一覧で報告する
- 一部の文言や素材が変更された場合に、同じ入力と規則から再生成できる

### 3. ゼロから再利用可能なデザインを制作

エージェントがゼロからデザインデータを制作し、次回以降の改訂にも対応できる、整理された構造データとして納品します。

このユースケースで重要なUX:

- Python component、template、入力データをsource of truthとして再生成可能にする
- 見出し、価格、CTA、商品画像等の役割を安定IDとsemantic metadataで識別する
- layer、group、text、path、image、Artboardを意図の分かる構造に保つ
- 単に同じ見た目のPDFや一枚画像を作るだけでなく、Illustrator上で再編集可能にする
- flatten、outline、font置換等が必要な場合は黙って行わず、policyと損失を報告する
- 次回改訂で、前回の成果物を解析し直すだけに頼らず、元のcomponentと入力データを利用できる

## 三つの層とユースケースの関係

### 1. 変換層

PythonのグラフィックIRとIllustrator sourceを相互変換します。

- `.ai` source、lossless CST/source map、グラフィックIRを分離する
- 対応範囲と未対応featureを診断する
- 既存ファイルでは、局所patchと未知データ保持を優先する
- 新規作成では、IRを選択したbackendへ決定的にcompileする
- pure conversionはIllustratorの起動を前提にしない
- production向けnative `.ai`生成ではIllustrator 2026をdirect compilerとして扱う
- legacy reader / writer / patchは既存legacy編集、回帰fixture、明示的なheadless AI7 exportへ限定する

この層は三つすべてのユースケースの安全性を支えます。ファイルを読めることと、安全に再保存できることを混同しません。

### 2. デザインモデル層

制作物の意味、規則、再利用可能な体裁をPython componentとして表現し、グラフィックIRへrenderします。

- component、template、variant、layout、theme、resource
- 条件分岐、反復、入力検証、文字列整形
- stable identityとsemantic metadata
- deterministic renderと再生成

この層は、特に複数サイズ展開とゼロからの構造化デザイン制作を支えます。一般の`.ai`から根拠なく高水準の意味を推測して復元しません。

### 3. エージェント層

変換層とデザインモデル層の公開API / CLIを使い、自然言語の依頼を安全な操作計画へ変換します。

- inspectして対応範囲と対象候補を確認する
- 曖昧性とpreconditionを解決してからapplyする
- validate、semantic diff、visual previewを実行する
- 成果物と一緒に診断と損失情報を返す

skillやpluginへファイル解析・変換ロジックを重複実装しません。エージェントは低レベルbyteを直接書き換えず、typed operationまたはデザインモデルを利用します。

## 開発中の後方互換ポリシー

### 現段階ではpublic APIの後方互換を保証しない

このプロジェクトはpre-1.0で、現時点では外部利用者や本番運用されている成果物を前提としていません。したがって、正しい責務分離、単純なモデル、明確なUXを実現するためなら、次を変更して構いません。

- Python module、class、function、field、return type
- JSON IR schemaとoperation schema
- CLI command、option、出力形式
- 内部ディレクトリとpackage構成
- fixture、example、テストの期待値

将来の利用者を想像して、deprecated alias、互換wrapper、新旧schemaの二重処理、移行分岐等を残しません。古い抽象が新しい設計に合わない場合は、同じ変更内で呼び出し元、テスト、fixture、example、文書を更新し、古いコードを削除します。

一時的な移行コードが必要なのは、同時進行中の作業を安全に統合する等、現在存在する具体的な移行対象がある場合だけです。その場合も削除条件を明記します。

### ファイル保全と後方互換を混同しない

public APIを壊してよいことは、入力ファイルや未知データを壊してよいことを意味しません。次はpre-1.0でも維持する製品要件です。

- 入力ファイルを既定で上書きしない
- 未対応featureを黙って破棄しない
- byte-preserving、graphic semantic、visual、native editabilityのどれを保証したか明示する
- Illustratorの対象version、OS、font、保存optionを適合試験へ記録する
- loss policyを明示しない破壊的変換を拒否する
- 生成物の再現性とsource of truthを明らかにする

つまり、「ライブラリAPIの互換性」は今は優先しませんが、「利用者のデザイン資産を安全に扱う互換性」は最初から優先します。

### 後方互換を開始する条件

次のいずれかが発生した時点で、versioningとmigration policyを別途定義します。

- 外部利用者がpublic Python API / CLIへ依存し始めた
- 保存済みJSON IRやoperation manifestが継続運用され始めた
- pluginやpackageを安定版として配布した
- 複数repositoryが同じAPI versionへ依存するようになった

## UXと実装の共通原則

1. **利用者の仕事から設計する**
   - 新しいoperatorやmodel fieldを追加する前に、どのユースケースを改善するか説明する。
2. **能力を正直に報告する**
   - parseできたことを、losslessに編集・再保存できることとして扱わない。
3. **非破壊を既定にする**
   - 別名保存、dry-run、precondition、局所patchを優先する。
4. **構造も成果物として扱う**
   - 見た目だけでなく、編集可能性、階層、identity、再生成可能性を検証する。
5. **曖昧な自動化は停止する**
   - selectorが0件または複数件、fontが不足、lossが不明な場合は推測で成功扱いしない。
6. **失敗を操作可能な情報にする**
   - エラーには対象、原因、対応範囲、利用者が選べる次の行動を含める。
7. **決定的に再現する**
   - 同じcomponent、入力、font、profileから同じIRを生成する。
8. **層の依存方向を守る**
   - 変換層はデザインモデル層やエージェント層へ依存せず、agent固有処理を下位層へ混ぜない。

## ドキュメント方針

ドキュメントの総量と重複を最小化し、開発時に必要なコンテキストとトークン消費を抑えます。

- 一つの事実は一つの正本だけに書き、他文書では要約せずリンクする。
- READMEは入口、roadmapは現在地と次の作業、architectureは安定した責務境界だけを扱う。
- 対応範囲はfeature profile、実機結果はtesting文書、設計判断の理由はADRへ置く。
- セッションごとの完了報告や古い進捗履歴を文書として残さず、Git履歴を利用する。
- コードとテストから明らかな実装詳細、長いAPI例、同種のexample一覧を重複記載しない。
- 新規文書を作る前に既存の正本へ短く追記できないか確認し、古い記述は併存させず削除する。
- 文書更新では情報量の増加だけでなく、不要になった記述と文書の削減も行う。

## 実装・レビュー時の確認事項

変更を始める前、またはレビューするときに次を確認します。

1. どの代表ユースケース、またはそれに準じる具体的な仕事を改善するか。
2. source of truthは元の`.ai`か、Python componentと入力データか。
3. byte、graphic semantics、visual、native editability、design semanticsのどこまで保証するか。
4. 未対応feature、曖昧なselector、missing resourceにどう失敗するか。
5. 成果物は次回改訂しやすい構造とidentityを保つか。
6. Illustratorなしで行うpure処理と、Illustrator 2026 native compilerの責務が分離されているか。
7. 使われていない後方互換コードで設計を複雑にしていないか。
8. 新しい複雑さが利用者のUX改善として説明できるか。
