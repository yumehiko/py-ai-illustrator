# Phase 0 実装状況

更新日: 2026-08-15

## 今回作った縦切り

```text
legacy .ai bytes
  -> format detector
  -> Phase 0 legacy reader
  -> Python IR / JSON
  -> Phase 0 AI7 writer
  -> legacy .ai bytes
```

現代版の PDF-compatible AI はコンテナと代表 marker の検出までです。PrivateData を「PDFとして見える内容」と混同しないよう、未対応時は JSON export を明示的に停止します。

## 対応 feature profile

| 項目 | Reader | Writer | 備考 |
| --- | --- | --- | --- |
| legacy AI container | 対応 | 対応 | Illustrator 7 公開仕様のサブセット |
| document bounds / title | 対応 | 対応 | 原点は `(0, 0)` |
| layer name / visibility / lock | 対応 | 対応 | 安定IDは独自DSCコメントでも保持 |
| straight path | 対応 | 対応 | `m`, `l/L`, path render operators |
| Bézier curve | 対応 | 対応 | `c/C`, `v/V`, `y/Y`を読み、writerは`c/C`へ正規化 |
| RGB fill / stroke / width | 対応 | 対応 | `Xa`, `XA`, `w` |
| CMYK fill / stroke | 対応 | 対応 | `k`, `K` |
| compound path | 対応 | 対応 | `*u` / `*U`、subpath polarity `D` |
| clipping group | 対応 | 対応 | `q` / `Q`、maskの`h/H`・`W` |
| text / image | 未対応 | 未対応 | fixture調査後 |
| PDF-compatible AI semantic data | 未対応 | 未対応 | 現在は形式判定のみ |

## 意図的な境界

- 形式判定は拡張子ではなく `%PDF-` / `%!PS-Adobe` と Illustrator marker を使う。
- 大きな入力を無制限に読み込まないよう、形式判定の探索は先頭・末尾それぞれ 4 MiB に制限する。
- JSON IR のID、名前、最小metadataは、他のPostScript readerが無視できる独自DSCコメントで保持する。
- 未対応の現代AIをPDF内容だけでJSON化したように見せず、CLIを失敗させる。

## 次の検証ゲート

1. Illustrator再保存をまたぐ安定ID・metadata保持方式を調査する。
2. 通常path、compound、clippingのstacking orderを保持する共通item列を設計する。
3. lossless token/CST 層と `inkai` adapter の境界を決める。
4. 検証対象とするIllustratorバージョンの範囲を広げる。

終了条件のうち、Python内の `IR -> AI7 -> IR` と、Illustrator 30.7.0でfixtureを開いた際の編集構造検査は完了しました。対応範囲はまだ小さいため、AI7 writer全体の位置付けは引き続き experimental です。

配布ライセンスは MIT に決定しました。GPL実装はコア依存に含めません。

### Illustrator 2026 適合試験メモ

2026-08-15、Creative Cloudへのログインと初回画面の完了後、macOS上のIllustrator 30.7.0で実機試験を完了しました。入力は一時コピーとし、取得したドキュメント参照だけを保存せず閉じています。開いていた別のユーザー文書や`current document`は操作していません。

| fixture | Illustratorでの結果 | 確認した内容 |
| --- | --- | --- |
| `rectangle.ai` | pass | 1 layer、1 closed path、4 anchors、RGB fill/stroke、stroke width 3 |
| `cmyk-curve.ai` | pass | 1 layer、1 open path、2 anchors、Bézier方向点、CMYK 100/25/0/10、stroke width 4 |

逆方向ではIllustrator自身にRGB矩形とCMYK Bézier曲線を作らせ、AI8互換で一時保存したファイルをPython readerへ読み戻しました。両fixtureともlayer/path/anchor、開閉、paint style、stroke width、RGB/CMYK、Bézier handleの照合に合格しています。この過程で、Illustrator標準AI8が出力する7成分`Xa/XA` paint style、同じ行に並ぶ`w` operator、埋め込みprocset内の`%%Title`をreaderで正しく扱うよう修正しました。

さらにPython生成fixtureをIllustratorでAI8再保存し、再びPython IRへ戻す完全往復を実施しました。RGB矩形とCMYK Bézierはいずれも、平行移動とRGB量子化を正規化した意味比較に合格しています。独自DSCコメント由来の安定ID、path名、metadata、元のdocument title/boundsはIllustrator再保存では保持されないことも確認し、既知のlossとして明示しました。

compound pathはIllustrator生成AI8から`*u` / `*U` containerと`D` polarityを採取し、専用IR、reader、writerを追加しました。Python生成fixtureはIllustrator 30.7.0で2 componentを持つ1つの`CompoundPathItem`として認識され、Illustrator再保存後の完全往復でもcontainer、polarity、geometry、RGB fillが保持されました。

clipping groupは`q` / `Q` container、mask pathの`h/H`・`W`、後続content pathを専用IRへ分離します。Python生成fixtureはIllustrator 30.7.0で1つのclipped `GroupItem`として認識され、完全往復でもgroup、mask/content数、geometry、RGB fillが保持されました。

最初の試験では閉じた矩形が3 anchorsとして読まれました。AI7の閉じパスに開始点へ戻る明示的な最終segmentを出力するようwriterを修正し、4 anchorsで再試験に合格しています。また、日本語環境でExtendScript内のreverse solidusが円記号として解釈される問題を避けるため、検査用JSXは該当文字とファイルパスを文字コードから構築します。詳細は [Illustrator 適合試験](illustrator-testing.md) を参照してください。
