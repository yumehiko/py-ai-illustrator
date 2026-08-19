# Direct native runtime contract

direct native compilerでは、Python coreとIllustrator ExtendScriptを同じソース文字列として扱いません。Pythonが`Document` IRを検証・変換した後、[NativeRuntimeBridge](../src/py_ai_illustrator/native_bridge.py)が一時ディレクトリへrequest JSONと`runtime/direct_native.jsx`を配置し、AppleScript経由でIllustratorへ渡します。

## Version 1

入力のトップレベルは次のJSON objectです。

```json
{
  "contract": "py-ai-illustrator.native-compile",
  "version": 1,
  "operation": "compile",
  "destination": "/absolute/path/to/temporary.ai",
  "document": {"...": "Document spec"}
}
```

`document`はPythonの`Document` IRそのものではなく、DOM materializationに必要な検証済みspecです。`destination`はbridgeが作成した一時`.ai`の絶対パスです。requestは`ensure_ascii=False`、固定separator、`allow_nan=False`でserializeされます。Unicode、JSON number、`null`、array、objectを文字列へ埋め込まず、UTF-8 JSONとしてExtendScriptへ渡します。

Illustrator runtimeは`py-ai-native-request.json`を読み、`contract`、`version`、`operation`を検証してからdocumentを作成します。runtimeは自分が作成した`documentRef`だけを保存・close・reopenし、暗黙のactive documentを参照しません。

## Result

stdout相当の戻り値は次のenvelopeを持ちます。

```json
{
  "contract": "py-ai-illustrator.native-compile-result",
  "version": 1,
  "operation": "compile",
  "ok": true,
  "illustrator_version": "30.7.0",
  "checks": {"structure_and_order": true}
}
```

`ok: true`では`checks`も返します。`ok: false`は次の2種類です。

- `checks`を含む: DOM・geometry・style・link・native editabilityなどの意味照合不一致。Pythonは`mismatch`とする。
- `checks`を含まない: Illustrator例外、request contractエラーなどruntime実行失敗。Pythonは`failed`とする。

PythonがJSONでない応答、空応答、未知contract、未知version、未知operation、型の異なる`ok`を受け取った場合も`failed`とし、stdoutを診断情報として返します。AppleScriptの非0終了とtimeoutはIllustrator runtimeが利用できない状態として`environment-unavailable`に分類します。

## File and promotion policy

bridgeが作るrequest、runtime、temporary native AIはcompileの一時ディレクトリに閉じます。出力先に同名ファイルがある場合は開始前に拒否します。runtimeが`ok`を返しても、temporary AIの存在、PDF-compatible AI形式、runtime checksをPythonで再確認し、全て通過した場合だけhard linkで指定出力へ昇格します。失敗時に指定出力を作成・上書きしません。

このcontractはIllustrator 2026 direct native compiler専用です。対応featureや`Document` IR schemaを拡張する契約ではなく、Pythonのpure core責務とIllustrator DOM責務の境界を固定するためのものです。
