# ADR 0002: 新規制作の主backendをIllustrator 2026 direct native compilerとする

- 状態: Accepted
- 決定日: 2026-08-19
- 関連issue: [#3](https://github.com/yumehiko/py-ai-illustrator/issues/3)

## Context

上位層`illustrator-agent`の正式対応対象はIllustrator 2026であり、旧Illustratorとの互換性は製品要件ではない。現在の新規制作は、Python componentから共通`Document` IRを生成した後、legacy AI7を中間表現として書き出し、Illustratorでopenしてnative TextFrame、AreaText、linked image、Artboard等を再構築している。

この経路では、最終成果物がcurrent-formatのPDF-compatible native `.ai`であるにもかかわらず、AI7のencoding、font名、placeholder、対応subsetが新規authoring APIとproduction gateへ漏れる。また、legacy相互変換とnative materializationを別々に実装・検証する必要がある。

新規制作のproduction実行環境では、Illustrator 2026がインストール・認証済みで応答可能であることを必須条件にできる。Illustratorなしで開ける`.ai`を生成することは、新規制作の必須要件ではない。

## Decision

新規制作の正式な主backendは、**共通`Document` IRからIllustrator 2026 DOMを直接構築し、PDF-compatible native `.ai`を保存するcompiler**とする。

```text
Python component / template + input data
        -> Document IR
        -> pure validation
        -> Illustrator 2026 DOM materialization
        -> temporary native .ai save
        -> reopen / semantic / visual / editability validation
        -> verified output
```

境界は次のように定める。

1. Python componentからIRを生成し、IRを検証・JSON化・diffする処理はIllustratorなしで動作する。
2. production向けnative `.ai`のcompileは、Illustrator 2026を明示的なruntime dependencyとする。
3. legacy reader / writer / patchは、既存legacyファイルの安全編集、回帰fixture、明示的なheadless AI7 exportとして維持する。
4. legacy backendをdirect compilerの内部実装や必須pre-passにしない。新しいauthoring featureをlegacy subsetへ適合させることも要求しない。
5. 既存のlegacy bridgeは、direct compilerが下記の昇格条件を満たすまで比較対象とfallbackとして残す。このADRだけを理由に既存実装を削除しない。
6. modern AI source-preserving writerは、既存modern `.ai`の安全編集に必要な独立課題であり、新規制作backendの前提にはしない。

## Backend contract

direct compilerは暗黙のactive documentを操作せず、入力IR、compile profile、asset、font、Illustrator versionから結果と検証reportを再現できなければならない。

- document color space等の新規document作成条件をIRまたは明示的なcompile profileで受け取る
- Artboard、Layer、nested Group、異種item orderを再帰的に構築する
- straight / Bézier Path、fill / stroke / dash / cap / joinを設定する
- point / area TextFrameのfont、size、fill、tracking、rotation、alignment、leadingを設定する
- LinkedImageを同じ親とstacking位置へ配置する
- stable identityを再open後に取得できる属性へ保存する
- missing font / link、unsupported IR、DOM属性不一致では成功扱いにしない
- 一時出力を保存・再openして検証し、全必須checkの成功後にだけ成果物を確定する
- Illustratorのinteraction levelやcoordinate system等、変更したglobal stateを復元する

現在のlegacy bridgeは入力IRの正しさを定めるoracleではない。direct出力とlegacy bridge出力はそれぞれ入力IRへ照合し、legacy bridgeはvisual regressionと移行比較にのみ利用する。

## Promotion gate

次の3 fixtureをdirect compilerの正式昇格gateとする。

| fixture | 主に検証する境界 |
| --- | --- |
| `quarterly-kpi-report` | document / layer / nested group、異種item order、path geometry、stroke style、point text |
| `editorial-brochure` | AreaText、frame geometry、font / size / fill、leading、paragraph alignment |
| `product-catalog` | linked image、link存在、placement / size、point / area text / vectorの混在stacking |

実装は`quarterly-kpi-report`で最小縦切りを完成させ、そのbackend境界を変えずに残り2 fixtureへ広げる。3 fixtureすべてについて次を満たした時点で、direct compilerを新規制作のproduction defaultへ昇格する。

1. 入力IRに対してlayer / group / itemの数、name、hierarchy、item orderが一致する。
2. geometry、paint、stroke、text content / style / alignment、linked image属性が一致する。
3. stable identityが保存・再open後も一致する。
4. PDF previewがvisual acceptanceを満たす。
5. current-format再保存・再open後もnative editabilityを保持する。
6. missing resource、unsupported feature、属性不一致を明示的に失敗させる。
7. 暗黙のactive documentへ依存せず、連続実行と失敗後の再実行が安全である。

## Consequences

- 新規制作APIからlegacy font名、AI7 encoding、placeholder等を段階的に除去できる。
- modern PrivateData writerの完成を待たずに、Illustrator自身をcurrent-format writerとして利用できる。
- production native compileはmacOS、Illustrator 2026、Creative Cloud認証、利用可能なfontとlinkへ依存する。
- Illustrator processの起動状態、timeout、同時実行、global state、障害回復はbackendの運用責務になる。
- pure IR生成・検証とlegacy編集はheadlessで維持されるため、通常の単体試験と実機適合試験を分離できる。
- 「Illustratorなし」はproject全体ではなくpure coreとlegacy機能の性質として扱う。

## Rejected alternative

legacy bridgeを新規制作の主経路に残す案は採用しない。Illustratorなしでproduction成果物を生成する要件がなく、AI7互換性のための二重実装と上位層への制約を正当化できないためである。

## Re-evaluation conditions

次のいずれかが起きた場合は方式を再評価する。

- production実行環境でIllustrator 2026を必須にできなくなる。
- direct compilerが3 fixtureのsemantic、visual、native editability、運用安全性を満たせない。
- Illustrator automationの変更により、非対話で再現可能なcompileが維持できなくなる。
- headlessでcurrent-format native `.ai`を生成することが新たな製品要件になる。
