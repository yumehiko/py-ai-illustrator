# ADR 0001: modern AI semantic readerをproject-owned実装とする

- 状態: Accepted
- 決定日: 2026-08-17
- 対象: Decision Gate L
- 比較対象inkai revision: `1a5f42a0b0407fc869e032420570682048810108`

## Context

A2のread-only最小縦切りは、PDF containerからIllustrator PrivateDataを抽出し、raw / decoded bytes、hash、source span、lossless token / section indexを保持できる。PrivateDataから共通グラフィックIRへのsemantic projectionは未実装である。

GPL-2.0-or-laterの[inkai](https://gitlab.com/inkscape/extras/extension-ai)はPrivateData抽出、typed parser、SVG変換を先行実装している。既存機能を理由なく再実装しないため、代表fixtureで現行readerとの差を隔離比較した。

## Evaluation

inkaiのコード、fixture、sample bytesはrepositoryへコピーしていない。公式repositoryをrepository外へcloneし、networkなし、read-only、non-root、memory / PID / timeout制限付きDocker containerで、project既存fixtureだけを読ませた。比較harnessは[`tools/evaluate_inkai.py`](../../tools/evaluate_inkai.py)である。

| fixture | 現行reader | inkai |
| --- | --- | --- |
| generated modern | 2/2 segment抽出。raw span一致、tokenからdecoded bytesを完全再構成し、未知operatorとopaque bytesを保持 | extract / parseとも`AssertionError` |
| Illustrator 30.7.0 native modern | zstd抽出、raw span一致、decoded bytes完全再構成 | 同じdecoded SHA-256を抽出し、915 objectのtyped graphへparse |
| project-authored legacy table | 414/414 statements、16 paths / 20 TextFrames、38 origins | parseは完了するがgraphに`Operator` 0件、warning 1件 |

native modern fixtureのinkai graphは、layer marker、path / paint operator、20 `AI11Text`を含んだ。これはmodern semantic parserの比較対象として有用だが、共通IRへの投影、geometry、stacking、identity、field-level provenanceの正しさは証明しない。

比較で確認した主な差は次のとおりである。

| 評価軸 | 現行reader | inkai revision `1a5f42a0` |
| --- | --- | --- |
| extraction | 評価modern fixture 2/2 | 1/2。複数segment fixtureで失敗 |
| semantic parsing | 未実装 | native fixtureをtyped graph化 |
| lossless / provenance | PDF、stream、decoded token / sectionのspanとbytesを保持 | public resultにPDF / node / field spanなし |
| unknown preservation | decoded bytes全体を保持 | unknown operator offsetなし。一部section内容を保持しない |
| safety | input、object、reference、decode、token、zstd windowに上限 | library APIに同等の入力 / token上限なし |
| dependency | `zstandard`のみ | pypdf、inkex、pyparsing、Pillow等。parserがinkex型へ結合 |
| stability | projectがAPIとreleaseを管理 | version 1.2.0 / Pre-Alpha、internal parser APIは非安定 |

人間のengineer-weekによる比較見積もりも作成したが、本プロジェクトではagentによる反復実装が中心であり、実測velocityを反映していない。長期の依存・保守方針を決める根拠には使用しない。

## Decision

modern AI semantic readerは、**現行のbounded / source-preserving reader上に、このprojectが所有・保守するparser / CST / reducerとして実装する**。

inkaiはruntime dependency、development dependency、fork、semantic workerとして製品へ組み込まない。隔離したcomparison oracleとして、対応operator、object count、hierarchy等の観測結果を独自実装のfixture contractと比較する用途に限定する。GPLコード、fixture、sample bytesをコピーまたは派生実装へ流用しない。

次を実装原則とする。

1. `ModernAIReadResult`がcontainer、segment ordering、raw bytes / spans / hashes、decode limit、unknown bytesの唯一の正であり続ける。
2. semantic parserは上限確認済みdecoded bytesだけを入力とし、元PDFを再読込しない。
3. lexer / CSTでoperatorとoperandのexact spanを保持し、対応部分だけをproject-owned IRへ投影する。
4. source spanへ証明可能に対応できないfieldはpatch対象にしない。未知bytesはsemantic結果と無関係に保持する。
5. unsupported object / operatorはpartialとして診断し、推測で既知IRへ変換しない。
6. Illustrator versionごとにproject-authored fixtureと実機fixtureを追加し、semantic IR、stacking、identity、unknown preservationをcontract testにする。
7. parserの入力と出力はproject-owned型に限定する。将来第二実装が必要になった場合に交換できるmodule境界を保つが、現時点では汎用plugin / worker機構を作らない。

## License and repository boundary

- 現在のMIT Licenseとpackage metadataを維持する。
- inkaiをpackage、extra、development dependencyへ追加しない。
- `tools/evaluate_inkai.py`はrepository外にinkaiが存在する隔離評価環境でのみ動く任意の開発toolとし、wheelへ含めない。
- 外部contribution開始時のinbound policyは別途決める。
- 将来別の実装を採用する場合も、技術適合性とlicenseをその時点で再評価する。

## Rejected or deferred alternatives

- **inkaiを唯一のreaderとして直接利用**: source provenance、安全上限、複数segment、unknown preservationの保証を下げるため棄却する。
- **現行readerとinkai semantic workerのhybrid**: semantic先行実装を利用できるが、GPL依存、非安定なinternal API、adapter / worker / fork保守が長期の柔軟性を下げるため棄却する。
- **inkaiをforkして継続利用**: GPL上は可能だが、upstream parserとproject固有adapterの双方を保守することになり、project-owned parserより責務が単純にならないため採用しない。
- **inkaiを一切参照しない**: 比較可能な先行実装を捨てる理由はないため採用しない。コードではなく観測結果をoracleとして利用する。

## Re-evaluation conditions

次のいずれかが起きた場合は方式を再評価する。

- 実測したagent開発velocityでも主要nodeのsemantic projectionが長期間成立しない。
- exact provenanceとsemantic editを両立できず、別parserの利用が安全性を改善すると証明できる。
- inkaiまたは別実装が、互換性のあるlicense、安定したdecoded-bytes API、structured diagnostics、resource limits、source spanを提供する。
- 対象Illustrator versionでPrivateData形式が大きく変化し、独自実装継続より別実装の採用が明確に低リスクになる。

## Consequences

- modern AIの抽出、意味解析、source provenance、将来writerを同じproject-ownedモデルで段階的に接続できる。
- 新しいIllustrator objectをupstreamの対応時期に依存せず追加できる。
- semantic parserの調査・実装・保守はこのprojectの責務になる。
- inkaiの先行知見は比較に利用できるが、実装を直接再利用する短期的な省力化は選ばない。
- Decision Gate Lではsemantic parser自体を実装せず、方針と比較証拠だけを確定する。
