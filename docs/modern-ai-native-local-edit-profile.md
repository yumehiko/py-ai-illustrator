# Illustrator native local-edit profile

Profile ID: `illustrator-native-local-edit-v1`

このprofileは、既存PDF-compatible AIで、PrivateDataとPDF表示の同じ局所spanをPythonだけでは証明できないlive objectを、licensed Illustrator runtimeのDOMで編集する経路です。`modern-ai-synchronized-patch-v1`の拡張やfallbackではなく、保証とruntime条件が異なる明示的なprofileです。

## 対応operation

- `replace_text`: Illustrator DOMで一意なlive `TextFrame`
- `replace_linked_image_source`: Illustrator DOMで一意な`PlacedItem`と、存在する外部asset
- 2つを含む複数operationの1 manifest内atomic batch

`inspect-native-local`はsource SHA-256に対応する一時コピーだけをopenし、global DOM collection indexから決定的なIDを作ります。selector evidenceにはcontent/link hint、parent、position、geometric/visible bounds、matrix、text font/style、linked image size/placement/clippingを含めます。切れたlinkはDOMの`file`を取得できないため、source内に一意な`%%DocumentFiles`またはXMP `filePath`がある場合だけhintとして公開します。

## CLI

```bash
uv run py-ai inspect-native-local input.ai
uv run py-ai plan-native-local input.ai operations.json
uv run py-ai apply-native-local input.ai operations.json -o output.ai
```

manifestはoperation schema version 1を使い、このprofileでは`source_sha256`が必須です。通常の`inspect` / `plan` / `apply`はIllustratorを起動せず、source-preserving profileの境界を維持します。

## atomic apply

1. source digest、manifest、replacement assetをPythonで検証する。
2. sourceを一時コピーし、そのコピーだけをIllustratorでopenする。
3. 全selectorのDOM fingerprintを再確認してから、manifest順に全operationを適用する。
4. 保存前にtargetのrequested value、font/style、text基準matrix、linked image identity/link/bounds/size/rotation/clipping、全非対象text/image/path fingerprint、document structureを照合する。
5. `pdfCompatible=true`、`embedLinkedFiles=false`で新しい一時`.ai`へ別名保存する。
6. 一時出力を再openし、同じDOM条件とnative editabilityを再検証する。
7. Pythonでcontainer、PrivateData、PDF display、PDF/PrivateData timestamp、target限定visual diff、source不変を検証する。
8. 全checkがtrueの場合だけ、既存でない最終outputとvisual diff artifactを作成する。

一件でも失敗した場合、最終outputは作りません。入力や既存outputの上書きは拒否します。

## pure gateとruntime gate

Illustratorなしで確認するpure gate:

- operation manifest schemaと必須source digest
- replacement assetの存在、SHA-256、PNGならpixel dimensions
- 保存後container、PrivateData、PDF displayの再parse
- PDF表示とPrivateData timestampの一致
- target bounds外のchanged pixelが0であること（144 dpi、channel threshold 8）
- source bytesが不変であること

Illustrator 2026が必要なruntime gate:

- selector inventoryとapply直前の一意性/precondition
- linked imageのrelinkと外部参照状態
- live textのcontent、font/style、基準matrix、native editability
- 非対象text/image/pathとdocument structureの保存前・再open後一致
- current-format PDF-compatible save-asと再open
- font substitutionがないこと

## synchronized patchとの違い

Illustratorのcurrent-format保存はPDF表示とPrivateDataを同期再生成します。そのためsourceは不変ですが、outputはsource prefixや未知PrivateDataのbyte-for-byte保持を保証しません。exact-span同期が証明できる対象は引き続き`modern-ai-synchronized-patch-v1`を使います。PDF商品名がoutline/CID subsetで、PrivateDataのlive textだけを書き換えると表示同期を証明できない場合は、このnative profileだけを使用します。

## Issue #17 fixture evidence

2026-08-21にユーザーがIllustrator 30.7.0で作成した`banner-test-oven.ai`（SHA-256 `57f4c266077eeb74960475e7f4f607599a4650e2e18fdee894887381ee2bf5c4`、1080×1080）から、最小DOM captureを`tests/fixtures/modern-native-local-banner.json`へ取り込みました。元`.ai`と商品画像はユーザー端末の外部fixtureであり、リポジトリには複製しません。captureは3 live textのうち商品名`オーブントースターが`が1件、768×768のlinked imageが1件であることと、その保持fingerprintだけを含みます。

Illustrator 30.7.0実機では、同じsourceから空気清浄機版と最新型炊飯器版を別々に生成し、全pure/runtime gate、再open、font/link/text editability、target限定visual diffが合格しました。
