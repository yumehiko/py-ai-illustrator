# Phase 0 実装状況

更新日: 2026-08-14

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
| straight path | 対応 | 対応 | `m`, `L`, path render operators |
| RGB fill / stroke / width | 対応 | 対応 | `Xa`, `XA`, `w` |
| Bézier curve | 未対応 | 未対応 | 次のreader拡張候補 |
| compound path / clipping | 未対応 | 未対応 | lossless token層の設計後 |
| text / image | 未対応 | 未対応 | fixture調査後 |
| PDF-compatible AI semantic data | 未対応 | 未対応 | 現在は形式判定のみ |

## 意図的な境界

- 形式判定は拡張子ではなく `%PDF-` / `%!PS-Adobe` と Illustrator marker を使う。
- 大きな入力を無制限に読み込まないよう、形式判定の探索は先頭・末尾それぞれ 4 MiB に制限する。
- JSON IR のID、名前、最小metadataは、他のPostScript readerが無視できる独自DSCコメントで保持する。
- 未対応の現代AIをPDF内容だけでJSON化したように見せず、CLIを失敗させる。

## 次の検証ゲート

1. `examples/rectangle.json` から生成した `.ai` を現行 Illustrator で開き、再保存する。
2. Illustrator で作った最小fixtureを用意し、`inspect` と Phase 0 reader の差分を記録する。
3. Bézier (`c`, `v`, `y`) と CMYK (`k`, `K`) を追加する。
4. fixtureが揃った段階で、lossless token/CST 層と `inkai` adapter の境界を決める。
5. 対応を保証する Illustrator バージョンを決定する。

終了条件のうち、Python内の `IR -> AI7 -> IR` は自動テスト済みです。Illustrator本体で開く適合試験は未実施なので、AI7 writerは引き続き experimental です。

配布ライセンスは MIT に決定しました。GPL実装はコア依存に含めません。
