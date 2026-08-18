# デザインモデル層

## 役割

第二層は、制作物の意味、規則、再利用可能な体裁をPythonで表し、第一層のグラフィックIRへ決定的にrenderします。

```text
Python component / template + input data
        -> deterministic render
graphic IR: document / layer / group / text / path / image
        -> 第一層のserializer / validator
.ai成果物
```

JSON化可能なIRはfixture、debug、semantic diff、交換境界に使います。表、商品カード、バナーvariant等の文脈と振る舞いを、低水準JSONへすべて手書きすることは要求しません。

## モデルに置くもの

- component / template / variant
- layout規則、条件分岐、反復、値の整形
- theme、font、color、spacing、resource
- 入力データのvalidation
- stable identityとsemantic metadata
- IRへのdeterministic render

第一層のIRは特定業務の意味を知りません。たとえば`Table`は列、formatter、行variant、折り返し、体裁を第二層で解決し、最終的に編集可能なPath、TextFrame、Groupへ展開します。

現在の共通境界は`RenderedComponent`、`LayerBuilder`、`Table`、`TextBlock`、`AreaTextBlock`、基本図形、Group、Artboard、rigid transform、LinkedImageです。実行例は`examples/`を参照し、個別exampleの説明はこの文書へ重複掲載しません。

## Source of truth

### 新規生成・量産

Python source、template、入力データをsource of truthとします。同じ明示的な入力、font、profileから同じIRを再生成できることを優先します。`.ai`はIllustratorで確認・仕上げできる編集可能な成果物です。

### 既存`.ai`の編集

元の`.ai`をsource of truthとし、第一層のsource-preserving patchを使います。一般の`.ai`から高水準componentを根拠なく復元しません。

### Illustratorで手修正した生成物

次を区別します。

- graphic semantics: geometry、paint、text、階層、stacking
- design semantics: 「価格欄」「CTA」「商品variant」等の役割と生成規則

見た目が同じでもdesign semanticsが保持されたとは限りません。意味の往復には元Python source、stable ID、埋め込みmetadata、sidecar manifest等の対応根拠が必要です。

## Render契約

1. componentは入力を検証してからIRを生成する。
2. 暗黙の時刻、環境、global stateへ依存しない。
3. font、座標系、色、単位、layout policyを明示する。
4. render後は第一層のcompatibility / semantic / visual validationを通す。
5. flatten、outline、font置換等の損失を黙って行わない。
6. textを含む非rigid transform等、意味が未定義な操作は拒否する。

## 第三層との境界

エージェントは自然言語と素材から、この層のPythonモデルまたは検証可能な入力データを作ります。第三層専用のデザイン表現を増やさず、人間がPythonから利用する場合と同じcomponent境界を使います。
