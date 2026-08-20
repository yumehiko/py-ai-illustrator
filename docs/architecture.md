# アーキテクチャ

このリポジトリは第1層の変換coreとIllustrator backendだけを所有します。

入出力、主経路、CLIごとのIllustrator境界は[System overview](system-overview.md)に集約しています。この文書は、その概要を実装境界と保証の分離へ掘り下げます。

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

## modern / operation の内部境界

公開モジュールは既存のPython APIとCLIの互換入口として残し、実装は次の依存方向に固定しています。

```text
modern reader facade
  -> _modern_container (PDF syntax / object resolution / bounded codecs)
  -> _modern_projection (read-only semantic IR projection)
  -> _modern_cst (decoded PrivateData lexeme / exact-span CST)

modern writing facade
  -> _modern_patch (synchronized PDF + PrivateData patch backend)
  -> _modern_discovery (editable target inventory and evidence)
  -> _modern_container / _modern_cst
  -> verification (representation / timestamp / visual evidence)

operation facade
  -> _operation_schema (manifest / selector validation)
  -> _operation_plan (selector resolution / read-only plan)
  -> _operation_orchestration (apply / post-apply verification)
```

| 公開入口 | 内部責務 | 保証境界 |
| --- | --- | --- |
| `modern` | PDF-compatible AIのbounded readとPrivateData section discovery | `modern-ai-read-only-v2` |
| `modern_semantic` | exact-span CST、unknown span、partial node、semantic projection | fixture coverage / diagnostics / classification |
| `modern_writing` | facadeのみ。mutationは `_modern_patch` へ委譲 | `modern-ai-synchronized-patch-v1` |
| `editing` | facadeのみ。manifestは `_operation_schema`、planは `_operation_plan`、applyは `_operation_orchestration` | source precondition / fail-closed apply |
| `verification` | PDF display、timestamp freshness、representation consistency、visual diff | semantic / representation / visual evidence |

Python exact-span patchでPDF/PrivateData同期を証明できないlive objectは、`_illustrator_native_local`が明示的なlicensed-runtime profileとして所有します。operation schemaは共有しますが、通常のmodern patchへfallbackせず、DOM inspection → copy上のatomic edit → current-format save-as → reopen → Python container/visual検証を一つの独立経路にします。Illustrator再保存はsource不変を保証しますが、outputのsource-prefix保持は保証しません。

container層はoperation selectorを知りません。CST層はPDFを再解析せず、decoded PrivateDataのspanを所有します。projection層はCSTとmodelだけから読み取り専用IRを構築し、未対応syntaxをeditableとは分類しません。discoveryはpatchを実行せず、対象・根拠・停止理由を返します。patchはdiscovery結果とsource preconditionを受け、PDF表示表現とPrivateDataを同じoperationで更新できない場合は成功扱いにしません。operation schemaはplanやapplyをimportせず、planはschemaとread-only backendだけを使い、orchestrationだけがmutation backendを呼び出します。

## 上位層との境界

デザインcomponentとエージェントworkflowは兄弟リポジトリ`illustrator-agent`が所有し、このパッケージの公開Python API / CLIへ依存します。依存方向は常に次の向きです。

```text
illustrator-agent -> py-ai-illustrator
```

第1層はTable、商品カード、バナー等の業務上の意味や自然言語処理を知りません。上位層の都合だけの抽象をcoreへ入れず、具体的な`.ai` fixture、必要なoperation、保持すべき情報、検証可能な完了条件が示されたときにprofileを拡張します。

## 実行時の境界

reader、IR validation、JSON交換、legacy変換・編集、modern patch、preview / visual diffはIllustratorなしで動作します。production向けnative `.ai` compileだけは、インストール・認証済みで応答可能なIllustrator 2026をruntime dependencyとします。

direct native compilerは既存modern `.ai`のsource-preserving編集を代替しません。またlegacy AIを内部実装や必須pre-passに使わず、自ら作成したdocument参照だけを操作します。決定理由は[ADR 0002](adr/0002-direct-native-authoring-backend.md)、実機環境と手順は[Illustrator適合試験](illustrator-testing.md)を正とします。

Python coreとIllustrator runtimeの入力・出力境界は、version 1のUTF-8 JSON contractとして固定しています。PythonはIR/spec、schema、validation、serialization、結果分類、昇格を担当し、`runtime/direct_native.jsx`はDOM materializationと再open後のDOM検証だけを担当します。requestの配置・runtimeの配置・AppleScript実行は`NativeRuntimeBridge`に集約しています。フィールドと失敗分類の正本は[direct native runtime contract](native-runtime-contract.md)です。

## 保証の分離

- byte-preserving
- graphic semantics
- visual equivalence
- native editability

これらを別々に検証します。読めたことを安全に再保存できることとして扱わず、PDF表示表現とIllustrator PrivateDataの片側だけの更新を完成したmodern writerとは呼びません。

legacyの保証範囲は[legacy feature profile](legacy-feature-profile.md)、modern reader / writerは[modern read profile](modern-ai-read-profile.md)と[modern write profile](modern-ai-write-profile.md)、実機環境は[Illustrator適合試験](illustrator-testing.md)を正とします。
