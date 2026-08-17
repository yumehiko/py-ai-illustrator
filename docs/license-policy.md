# ライセンス・先行実装利用方針

更新日: 2026-08-17

## 現在の状態

現在リポジトリにあるコードはMIT Licenseで公開されています。すでにMITで提供した版に対する利用許諾は維持されます。

Decision Gate Lでは、現在のMIT方針を維持し、modern AI semantic parserをproject-owned実装とする判断をしました。GPL-2.0-or-laterの`inkai`は製品へ組み込まず、任意の隔離comparison oracleとしてのみ利用します。根拠、実測、再評価条件は[ADR 0001](adr/0001-modern-semantic-reader-strategy.md)に記録しています。

このプロジェクトは現時点で外部利用や外部contributorを前提としていないため、作者が保有するコードの将来版については、技術・製品上の判断を先に行います。第三者由来のコード、fixture、依存packageについては、それぞれのlicenseと由来を別途遵守します。

## 判断が必要な理由

`inkai`はlegacy AIとmodern AI PrivateDataの抽出・意味解析を実装している重要な先行実装です。利用すればmodern AI readerの開発量を減らせる可能性があります。一方で、主にreader / SVG conversionを目的としたpre-alpha実装であり、このプロジェクトが必要とする次の保証をそのまま満たすとは限りません。

- 元bytes、未知section、source spanの保持
- resource limitとmalformed inputに対する安全な停止
- 局所patchとmodern AI writeback
- PDF表示表現とPrivateDataの同期
- このプロジェクトのグラフィックIR、diagnostics、compatibility reportとの統合

したがって、license名だけで採否を決めず、独自実装の継続コストを含む技術比較を行いました。

## Decision Gate L: licenseとinkai利用方式（完了）

A2のread-only container / PrivateData抽出の最小縦切りを比較基準に、2026-08-17に次を完了しました。

1. 自作したlegacy / modern fixtureとIllustrator 30.7.0実機fixtureを固定しました。
2. `inkai` revision `1a5f42a0`をnetworkなし、read-only、non-root、resource limit付きcontainerで実行し、コードやsample bytesを現在の実装へコピーせず比較しました。
3. 抽出成功率、semantic coverage、未知データ保持、source span、resource limit、診断、IR mapping、依存関係、保守状況を評価しました。
4. 次の利用方式の技術責務、長期保守、配布条件を比較しました。
   - GPLを採用して`inkai`を直接利用する
   - 自前のsource-preserving層と`inkai`の意味解析を組み合わせる
   - `inkai`を開発時のreference / comparison oracleとしてのみ利用する
   - permissiveな独自実装を継続する
5. agent中心の開発では人間向けengineer-week見積もりの不確実性が大きいため、長期依存を決める根拠から除外しました。
6. MITを維持し、project-owned semantic parserを実装し、inkaiをcomparison oracleに限定する判断を[ADR 0001](adr/0001-modern-semantic-reader-strategy.md)へ記録しました。

別process、optional dependency、adapter、別repositoryへの分離をlicense回避の根拠にはしません。将来GPL実装を製品へ導入する案を再検討する場合は、配布形態とlicenseを改めて評価します。

## Semantic implementationの運用

- 現在のソースと配布物にはMITの表示を維持する。
- `inkai`のコードやfixtureをコア、テスト、配布物へコピーしない。
- `inkai`はrepository外の隔離環境でcomparison oracleとして利用できる。
- A2 semantic readerは現行decoded bytes上のproject-owned lexer / CST / reducerとして実装する。
- inkai固有型やinternal APIをpublic API、runtime、fixture contractへ持ち込まない。
- 新しいIllustrator objectはunknown bytesを保持して安全に停止し、自作fixtureと実機fixtureを追加してから段階対応する。
- 将来別実装の採用が明確に安全性・保守性を改善する場合はADRを再評価する。
