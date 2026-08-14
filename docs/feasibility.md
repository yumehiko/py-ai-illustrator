# Illustrator ファイル相互変換の実現可能性調査

調査日: 2026-08-14

## 結論

構想は実現可能です。ただし「Illustrator で開ける `.ai` を生成する」と「現在の Illustrator の全編集状態を損失なく往復する」は、別の目標として扱う必要があります。

| 目標 | 判定 | 理由 |
| --- | --- | --- |
| PDF 互換の現代 `.ai` を解析し、図形・レイヤー等を Python オブジェクトにする | 実現可能 | Inkscape の `inkai` が PrivateData の抽出と typed object へのパースを実装中。別の OSS 実装も存在する |
| Python から Illustrator で開けるベクターファイルを生成する | 実現可能 | 公開仕様のあるレガシー AI/EPS、PDF、SVG を利用できる |
| 基本図形・パス・塗り・線・レイヤーを `.ai` と往復する | 段階的に可能 | 対応要素を限定し、未解釈データを保持する設計なら実用的な MVP を作れる |
| 現代版 `.ai` の全機能を Illustrator と完全互換で新規生成する | 高難度 | Illustrator 固有の編集データは公開仕様がなく、表示用 PDF と内部編集表現の整合も必要 |
| 任意の `.ai` を変更して完全に同一の見た目・編集性で戻す | 長期目標 | テキスト、フォント、透明、ライブ効果、メッシュ、リンク、カラー管理などの組合せ爆発がある |

## `.ai` は一種類の形式ではない

### Illustrator 8 以前

古い AI は PostScript/EPS 系のテキスト形式です。Adobe が公開した 1998 年の Illustrator 7 File Format Specification に命令と構文が記載されています。新しい透明機能などは持ちませんが、仕様に沿った serializer を独立実装しやすい形式です。

### Illustrator 9 以降

現在一般的な `.ai` は PDF 互換表現を含められます。Adobe の保存画面でも `Create PDF Compatible File` が既定で有効です。PDF 互換ファイルにはおおむね次の二つが共存します。

1. Adobe PDF として表示・配置するための表現
2. Illustrator が再編集に使う `PieceInfo` / `Illustrator` / `Private` 以下の Illustrator 固有データ

Adobe の資料は、AI ファイルが PDF と PGF の両方を含むこと、PDF 互換オプションによって二つの表現を同じファイルに保存することを説明しています。Inkscape の AI importer は、この PrivateData が deflate または zstd で圧縮される場合を扱っています。

重要なのは、PDF 側だけを書き換えても Illustrator 固有データは自動的には同期されないことです。Illustrator は固有データが存在するとそちらを編集用の正として扱う場合があるため、片側だけの変更は「他の PDF ビューアでは変更済み、Illustrator では古い内容」のような不整合を起こし得ます。

## `py-aep` と同じ発想を適用できるか

適用できます。`py-aep` は RIFX ベースの AEP を解析し、After Effects のスクリプト API に近い Python オブジェクトへ変換して再保存します。Illustrator 版でも、ユーザーが操作する API をアプリ本体のオブジェクトモデルに寄せる考え方は有効です。

ただし Illustrator では、ファイルが「ネイティブ編集表現 + 表示用 PDF」という二重構造を持ち得ます。さらにベクター描画の結果を PDF 側へ再生成するレンダリング責務が加わるため、AEP の chunk 更新だけよりも範囲が広くなります。

## 先行実装

### Inkscape `inkai`

- Python 3.10 以降
- `.ai` から内部データを抽出
- `inkai.parse()` で typed object structure を返す
- SVG へ変換
- 古い AI と PDF ラッパー内の新しい AI PrivateData を対象にする
- 2026 年時点でも更新があり、Python パーサーの最重要先行例
- GPL-2.0-or-later。コードを直接利用・派生する場合はプロジェクト全体のライセンス方針に影響する
- `pyproject.toml` 上は Development Status が Pre-Alpha
- 読み取りが中心で、現代版 AI の serializer は確認できない

### Open Design `illustrator-parser-pdfcpu`

- TypeScript + Go/WASM
- PDF の XRef、artboard、PrivateData、テキスト情報などを解析
- Apache-2.0
- 2023 年以降の活動は少なく、公開 API も読み取り中心

### Inkscape 本体の従来 AI import

Illustrator 9 以降の `.ai` を PDF importer として読む方式です。表示結果は取り込めますが、Illustrator 固有の編集意味を復元するものではありません。メッシュや透明などが近似・変換されることがあります。

## 実用上の到達点

### 到達しやすい MVP

- ファイル種別とバージョンの判定
- artboard、layer、group、path、compound path
- RGB / CMYK の基本色、fill、stroke
- 画像参照と埋め込み画像の抽出
- 単純な point text
- Python オブジェクト / JSON への変換
- オブジェクトの追加、削除、移動、色変更
- SVG / PDF への確実な書き出し
- Illustrator 7/8 互換 AI の書き出し
- レンダリング比較と round-trip 検証

### 後段に置くべき機能

- area/path text、縦書き、OpenType、複雑な字形配置
- opacity mask、blend mode、透明グループ
- gradient mesh、pattern、brush、symbol
- appearance stack、live effects、plugin object
- clipping の全組合せ
- spot color、ICC、overprint、PDF/X
- variable、graph、3D、生成系機能
- 現代版 PrivateData の完全な新規 serializer

## 最大の技術課題

### 1. 未公開・可変の内部形式

公開されている完全な AI 仕様は古く、現代 PrivateData は実ファイル差分と既存 OSS の知見から追跡する必要があります。Illustrator の更新で変化する前提の互換レイヤーが必要です。

### 2. 二つの表現の整合性

現代 AI writer は、Illustrator 用データだけでなく PDF 表現も生成し、両者を一致させる必要があります。PDF writer、フォント埋め込み、画像圧縮、透明処理まで関係します。

### 3. 損失のない往復

未知の operator や payload を捨てる通常の AST では、一度保存しただけで未対応機能が壊れます。元バイト列、token span、未知セクションを保持し、変更箇所だけ差し替える lossless concrete syntax tree が必要です。

### 4. 見た目と編集性は別

同じレンダリング結果でも、テキストがアウトライン化されたり、ライブ効果が展開されたりすれば編集性は失われます。テストでは画像差分だけでなく、オブジェクト種別・階層・属性の差分も検証します。

## ライセンスと検証環境

- `inkai` を内部ライブラリとして直接組み込む案は GPL-2.0-or-later を前提に評価します。
- permissive license を優先するなら、Apache-2.0 の先行実装を参考にするか、公開仕様と独自 fixtures による clean-room 実装を検討します。
- Adobe の仕様書本文や第三者の `.ai` サンプルを無断で再配布しない運用が必要です。
- 互換性検証に Illustrator を使うことと、実行時に Illustrator を不要にすることは両立します。CI の通常テストは Illustrator なし、リリース前の適合試験だけ Illustrator ありに分けるのが現実的です。
- 法的評価そのものではないため、公開・商用化前にライセンスとリバースエンジニアリング条件を別途確認します。

## 最終判断

Go です。ただし成功条件を「最初から完全な現代 AI 互換」に置かず、次の順番にします。

1. lossless に近い reader と安定した Python IR
2. Illustrator で利用できる限定 writer（AI8 / PDF / SVG）
3. エージェント向け安全編集 API と検証器
4. 現代 PrivateData の patch writer
5. 現代 `.ai` の新規生成範囲を段階的に拡張
