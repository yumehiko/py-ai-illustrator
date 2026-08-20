# Modern AI synchronized patch profile

Profile ID: `modern-ai-synchronized-patch-v1`

このprofileは、PDF-compatible AIのIllustrator PrivateDataとPDF表示表現を同じoperationで更新できることを、source-localな証拠が揃った対象だけに保証します。一般的なPDF writerや任意のmodern AI再serializeを保証するものではありません。

## 対応operation

- `set_fill`: PrivateDataのfill spanとPDFの矩形が一意なpath
- `set_stroke`: PrivateDataのstroke spanとPDF path geometryが一意なpath
- `translate`: PrivateData/PDFのgeometryが一意な矩形path
- `replace_text`: AI11 story値とPDF literalが一意で、printable ASCIIかつsource-localな初期text matrixを持つtext
- container `translate`: 全descendantが一意に同期可能な矩形pathだけで構成され、partial/non-path descendantがないlayer/group。各memberへ展開してatomic batchとして検証する

`inspect --json`の`selectors[].operations`は、その入力で実際に使用できるoperationだけを列挙します。ID、name、boundsを組み合わせて一意に解決できない場合や、片方の表現しか特定できない場合はplanで停止します。複数operationはmanifest順に一時revision上で再plan・再検証します。同じnodeを複数回変更する場合も、直前の一時revisionをpreconditionの正本とし、全件成功後だけ最終outputを作成します。

## 書き込み方式

1. 元sourceのSHA-256とtyped preconditionを確認する。
2. decoded PrivateDataのexact spanだけを変更し、元のcompression profileで再圧縮する。
3. 対応するPDF content streamを変更する。
4. page/PieceInfoの`LastModified`とXMPの`ModifyDate` / `MetadataDate`を同期する。
5. 変更objectをincremental updateとして追記し、classic xrefとtrailerを追加する。
6. 元source全体をoutputのprefixとして保持し、入力や既存outputを上書きしない。

## apply後の検証

- PrivateDataを再読込し、対象値またはgeometryがrequestと一致する
- PDF page/content/resource evidenceを再抽出できる
- PDFとPieceInfoのtimestampが一致する
- XMPの二つの変更日時が一致する
- normalized raster visual diffが計画したimpact bounds内に収まる
- 証明可能なpaint / geometry / textについて、変更前からPrivateDataとPDF表示値が一致する
- 入力bytesが変化せず、outputが元source prefixを保持する

## 対応外

- linked image、partial/non-path descendantを含むcontainer、artboardのmodern patch
- 一意でないAI11 story/PDF text、文字styleやfontの変更、printable ASCII外のtext置換
- source-localに証明できないpath geometry、gradient、pattern、spot color、effect
- encrypted PDF、object stream内だけにある更新対象、未対応filter/DecodeParms

linked imageや、PDF表示がoutline/CID subsetでPrivateData live textとのexact-span同期を証明できない対象は、このprofileへ無理に含めません。licensed Illustrator runtimeでcopy/edit/save-as/reopenを行う明示的な別経路は[Illustrator native local-edit profile](modern-ai-native-local-edit-profile.md)を参照してください。

Illustratorでのnative再編集性はPython検証とは別の保証軸です。2026-08-18にIllustrator 30.7.0で、fill/translate/text atomic batchとBézier stroke成果物のcurrent-format再保存・再openが合格しました。DOM構造、text identity/content、PrivateData/PDF再parse、timestampを保持し、PDF再生成によるpixel差は各ページ0.1%以下でした。

実機では`py-ai test-illustrator-modern-roundtrip patched.ai`を使い、open、current-format再保存、再open、DOM構造・text identity・PrivateData・PDF表示・pixel previewをまとめて確認します。第一層の最終matrixは`scripts/test_layer1_illustrator.py`です。
