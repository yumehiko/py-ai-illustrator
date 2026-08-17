# ライセンス・先行実装利用方針（検討中）

更新日: 2026-08-17

## 現在の状態

現在リポジトリにあるコードはMIT Licenseで公開されています。すでにMITで提供した版に対する利用許諾は維持されます。

一方、将来のreleaseを引き続きMITで配布するか、別のlicenseへ変更するか、GPL-2.0-or-laterの`inkai`をruntime dependencyまたは実装基盤として利用するかは未決定です。「permissive licenseを維持する」「GPL実装をコア依存に含めない」は、現時点の確定した製品要件として扱いません。

このプロジェクトは現時点で外部利用や外部contributorを前提としていないため、作者が保有するコードの将来版については、技術・製品上の判断を先に行います。第三者由来のコード、fixture、依存packageについては、それぞれのlicenseと由来を別途遵守します。

## 判断が必要な理由

`inkai`はlegacy AIとmodern AI PrivateDataの抽出・意味解析を実装している重要な先行実装です。利用すればmodern AI readerの開発量を減らせる可能性があります。一方で、主にreader / SVG conversionを目的としたpre-alpha実装であり、このプロジェクトが必要とする次の保証をそのまま満たすとは限りません。

- 元bytes、未知section、source spanの保持
- resource limitとmalformed inputに対する安全な停止
- 局所patchとmodern AI writeback
- PDF表示表現とPrivateDataの同期
- このプロジェクトのグラフィックIR、diagnostics、compatibility reportとの統合

したがって、license名だけで採否を決めず、独自実装の継続コストを含む技術比較を行います。

## Decision Gate L: licenseとinkai利用方式

A2のread-only container / PrivateData抽出の最小縦切りは完了済みです。それ以降のPrivateData semantic parserを大きく独自実装する前に、次を完了します。

1. 自作した代表fixtureとIllustrator実機fixtureを固定する。
2. `inkai`を隔離した評価環境で実行し、コードやsample bytesを現在の実装へコピーせず結果を比較する。
3. 抽出成功率、semantic coverage、未知データ保持、source span、resource limit、診断、IR mapping、依存関係、保守状況を評価する。
4. 次の利用方式ごとの開発工数と配布条件を比較する。
   - GPLを採用して`inkai`を直接利用する
   - 自前のsource-preserving層と`inkai`の意味解析を組み合わせる
   - `inkai`を開発時のreference / comparison oracleとしてのみ利用する
   - permissiveな独自実装を継続する
5. 商用・proprietary製品への組み込みを必要条件とするか、copyleftを許容するか、外部contributionを受けるかを決める。
6. 採用案、棄却案、根拠、再評価条件をADRとして記録し、package metadata、LICENSE、third-party notices、repository境界を整合させる。

別process、optional dependency、adapter、別repositoryに分離すれば自動的にlicense上の問題が解消するとは仮定しません。配布形態が決まった段階で、必要に応じて専門家の確認を行います。

## Gate完了までの運用

- 現在のソースと配布物にはMITの表示を維持する。
- `inkai`のコードやfixtureをコア、テスト、配布物へコピーしない。
- `inkai`は隔離した比較評価には利用できる。
- A2最小縦切りを越える大規模なPrivateData semantic parserの独自実装は開始しない。
- licenseを理由に先行実装を最初から排除せず、同時にGPL適用範囲を未確認のまま配布依存へ追加しない。
