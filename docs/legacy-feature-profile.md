# Trusted Legacy Conversion feature profile

更新日: 2026-08-17

## Profile identity

機械可読profile IDは`legacy-ai7-trusted-v1`です。`LegacyReadResult.compatibility_report()`はprofile、入力ごとのcoverage/diagnostics、保証、変換policyを同じreportで返します。

対象はIllustrator 7公開仕様を基礎にしたlegacy AI7/AI8の限定subsetです。PDF-compatible modern AI、一般のPostScript/EPS、spot color、ICC profile、gradient、pattern、transparency、effect、embedded image、arbitrary text runは対象外です。拡張子ではなくcontainer内容で形式を判定します。

## 判定

| 判定 | 条件 | 許可される処理 |
| --- | --- | --- |
| `convertible` | operator/resource inventoryが完全で、未表現の構文・文脈・metadata diagnosticがない | semantic reserialize、対応typed patch |
| `partially_parsed` | IRは得られるが、未知featureまたは未表現semanticsが1件以上ある | read-only inspection、診断と交差しない対応typed patch |
| 例外（変換不可） | container、必須bounds、resource limit等の前提を満たさずIRを構築できない | sourceを変更せず停止 |

「operator名が既知」であるだけでは`convertible`にしません。未対応operand、text/path/container外のoperator、styled multi-`Tx`、未終端construct、layerと異なるitem lock、壊れた/孤立した意味metadataはsource位置付きdiagnosticとなり、通常re-serializeを拒否します。

## Semantic reader/writer subset

| IR/feature | Reader | Canonical writer | Node origin | Typed local patch |
| --- | --- | --- | --- | --- |
| Document bounds/title/metadata | 対応 | 対応 | source全体 | なし |
| named Artboard intent | private metadata | 対応 | metadata行 | なし |
| Layer name/visibility/lock | 対応 | 対応 | begin/end全体 | descendant translate |
| nested Group | `u` / `U` | 対応 | metadataから`U` | descendant translate |
| CompoundPath/polarity | `*u` / `*U`, `D` | 対応 | metadataから`*U` | descendant translate |
| ClippingGroup | `q` / `Q`, `h/H`, `W` | 対応 | metadataから`Q` | descendant translate |
| straight/Bézier Path | `m`, `l/L`, `c/C`, `v/V`, `y/Y` | `m`, `L`, `c/C` | ID/name/noteからrender終端 | fill、stroke、translate |
| RGB/CMYK process color | `Xa/XA`, `k/K` | 対応 | 排他的field span | fill、stroke |
| stroke style | width、dash、cap、join、miter | 対応 | node span内 | translate時は不変 |
| point/area Text intent | `To`…`TO` + private metadata | 対応 | ID/属性metadataから`TO` | 単一`Tx`本文、container translate |
| ASCII / CP932 text | 対応 | 対応 | `Tx`ごとの本文span | 単一`Tx`のみ |
| LinkedImage intent | private metadata + placeholder | 対応 | metadataからplaceholder終端 | source、container translate |

複数`Tx`は各本文spanを保持し、同じstyleならgraphic semanticsとして読めます。ただし現在のIRはtext run境界を持たないため、本文patchは単一`Tx`だけを許可します。run間でfont/fill/alignmentが異なる場合は未表現semanticsとして診断します。

## 保証とpolicy

1. Read-only: `result.source.to_bytes()`は入力bytesと完全一致します。
2. Typed patch: planに列挙したreplacement span以外のbytesは完全一致します。元source全体のSHA-256、各field bytes、semantic precondition、selector一意性、unsupported交差、span範囲、operation間競合をapply前に検証します。
3. Canonical reserialize: `convertible`入力ではgraphic semantic round-tripを保証対象とします。物理改行、数値表記、header順、preview、ruler/page origin等のdocument setupはIRの`(0, 0)`原点を基準にcanonical writerで正規化されるためbyte一致は保証しません。任意text encoding resourceはこのpolicyに含めず、未対応として診断します。
4. Discard: `loss_policy="discard"`だけが、診断済み/未対応source dataを捨てる明示policyです。既定では拒否します。
5. Semantic diff: 安定IDでnodeを対応付け、field変更、追加、削除、stacking変更を別々に報告します。

## Safe edit CLIとの境界

`py-ai plan` / `apply`はこのprofileのtyped local patchだけを公開operationへ束ねます。対応operationはfill、stroke、単一`Tx` text、path/container translate、linked image sourceであり、profileのsemantic reader/writer subsetを拡張しません。selector保証も現時点では`type + id`だけです。

plannerは現在IRからtyped operationのexpected valueとcontainer member集合を導出し、unsupported diagnosticが変更spanと交差する場合は停止します。交差しないunsupported dataはbyte-preservingで保持し、入力classificationを`convertible`へ格上げしません。apply後は出力を再読込し、replacement span外のbytesと、operationごとに許可されたsemantic fieldだけが変化したことを検証します。name / bounds / hierarchy selector、preview / visual diff、modern AI編集はこのprofileの保証対象外です。

## Illustrator適合範囲

現時点の正式サポート対象はmacOS上のAdobe Illustrator 2026（30.7.0）です。AI7/AI8互換open/save、native materialization、PDF-compatible native AI再openについて、fixture、font、保存option、既知advisory lossを[Illustrator適合試験](illustrator-testing.md)に記録しています。

30.7.0以外のIllustrator version、Windows、異なるfont環境は未検証であり、現行profileのサポート対象外です。将来、新しいIllustratorの正式版が公開されたときに適合試験を実施し、合格したversionだけをreport上の対応versionとprofileへ追加します。

## 自動検証

- 対応subsetの`IR -> AI7 -> IR` semantic diffが空であること
- 未知operator/resourceと既知だが未表現のsemanticsを`convertible`にしないこと
- read-only source bytesが完全一致すること
- 単一/複数typed patchで全replacement span外が完全一致すること
- plan後のsource変更、重複span、同一点の空span挿入、範囲外spanを拒否すること
- container translateがPath、Text、LinkedImage、およびCompoundPath/ClippingGroup descendantを同期更新すること

実機試験は自動Python test suiteと分離されています。新しいIllustrator versionをprofileへ追加する前に、`docs/illustrator-testing.md`の双方向fixtureと再open検証をそのversionで実行し、結果を記録します。新versionが公開されていない間、複数versionの同時サポートはこのprofileの完了条件に含めません。
