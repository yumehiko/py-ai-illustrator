# アーキテクチャ

このリポジトリは第1層の変換coreとIllustrator backendだけを所有します。

```text
.ai bytes
  -> format / container reader
  -> source-preserving CST + coverage + diagnostics
  -> low-level graphic IR
  -> typed edit / writer
  -> validate + semantic diff + visual verification

low-level Document IR
  -> pure validation
  -> Illustrator 2026 direct native compiler
  -> temporary current-format .ai
  -> reopen + DOM / container verification
  -> verified output
```

主な責務は、形式判定、未知情報を保持する読取、対応部分のIR投影、局所patch、限定profileのserialize、再読込・意味・表示・実機検証です。共通IRはDocument、Artboard、Layer、Group、Path、CompoundPath、ClippingGroup、TextFrame、LinkedImageと、geometry / paint / stacking / stable IDを表現します。

## 上位層との境界

デザインcomponentとエージェントworkflowは兄弟リポジトリ`illustrator-agent`が所有し、このパッケージの公開Python API / CLIへ依存します。依存方向は常に次の向きです。

```text
illustrator-agent -> py-ai-illustrator
```

第1層はTable、商品カード、バナー等の業務上の意味や自然言語処理を知りません。上位層の都合だけの抽象をcoreへ入れず、具体的な`.ai` fixture、必要なoperation、保持すべき情報、検証可能な完了条件が示されたときにprofileを拡張します。

## 実行時の境界

reader、IR validation、JSON交換、legacy変換・編集、modern patch、preview / visual diffはIllustratorなしで動作します。production向けnative `.ai` compileだけは、インストール・認証済みで応答可能なIllustrator 2026をruntime dependencyとします。

direct native compilerは既存modern `.ai`のsource-preserving編集を代替しません。またlegacy AIを内部実装や必須pre-passに使わず、自ら作成したdocument参照だけを操作します。決定理由は[ADR 0002](adr/0002-direct-native-authoring-backend.md)、実機環境と手順は[Illustrator適合試験](illustrator-testing.md)を正とします。

## 保証の分離

- byte-preserving
- graphic semantics
- visual equivalence
- native editability

これらを別々に検証します。読めたことを安全に再保存できることとして扱わず、PDF表示表現とIllustrator PrivateDataの片側だけの更新を完成したmodern writerとは呼びません。

legacyの保証範囲は[legacy feature profile](legacy-feature-profile.md)、modern reader / writerは[modern read profile](modern-ai-read-profile.md)と[modern write profile](modern-ai-write-profile.md)、実機環境は[Illustrator適合試験](illustrator-testing.md)を正とします。
