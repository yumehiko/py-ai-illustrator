# Illustrator 適合試験

`py-ai test-illustrator` は、生成したAIファイルが実際のIllustratorで編集可能な構造として開けるかを検査します。通常のPythonテストとは分離し、Illustratorを利用できるmacOS環境で明示的に実行します。

## 初回準備

1. Adobe Creative Cloudへログインする。
2. Illustratorを手動で起動する。
3. ライセンス確認、オンボーディング、ワークスペース選択などの初回画面を完了する。
4. Home画面が操作できる状態で、次の応答確認を実行する。

```bash
osascript -e 'with timeout of 10 seconds' \
  -e 'tell application "Adobe Illustrator" to return version' \
  -e 'end timeout'
```

バージョン文字列が返れば、AppleScript経由の適合試験を実行できます。

## 実行

```bash
uv run py-ai test-illustrator examples/rectangle.ai
uv run py-ai test-illustrator examples/cmyk-curve.ai
```

逆方向（Illustratorでfixtureを作成・AI8保存し、Python IRへ読み戻す）:

```bash
uv run py-ai test-illustrator-export --fixture rgb-rectangle
uv run py-ai test-illustrator-export --fixture cmyk-curve
```

完全往復（Python生成AIをIllustratorでAI8再保存し、Python IRへ戻す）:

```bash
uv run py-ai test-illustrator-roundtrip examples/rectangle.ai
uv run py-ai test-illustrator-roundtrip examples/cmyk-curve.ai
```

調査用にIllustrator生成AIを残す場合は、既存ファイルではない出力先を指定します。上書きは拒否されます。

```bash
uv run py-ai test-illustrator-export \
  --fixture cmyk-curve \
  --ai-output illustrator-native-curve.ai
```

JSONレポートを保存する場合:

```bash
uv run py-ai test-illustrator examples/rectangle.ai \
  -o compatibility-report.json
```

## 安全性

- リポジトリ内の入力は直接開かず、一時コピーを作る。
- JavaScriptが返したドキュメント参照だけを検査する。
- `current document`や既存ドキュメントを操作しない。
- 検査対象は`DONOTSAVECHANGES`で閉じる。
- 逆方向試験は新規ドキュメントを作り、指定した一時出力だけへ保存する。
- `--ai-output`が既存ファイルを指す場合は上書きしない。
- Illustratorが応答しない場合はタイムアウトし、互換性失敗ではなく`environment-unavailable`を返す。

## 判定

自動判定ではPython IRとIllustrator上の次の構造を比較します。

- layer数とlayer名
- path item数
- 各pathのanchor数
- closed / filled / strokedの個数

レポートにはartboard、anchor座標、Bézier方向点、stroke幅、RGB/CMYK colorも含めます。今後fixtureが増えたとき、数の一致だけでなく属性ごとの許容差比較へ拡張します。

完全往復では次を意味属性として比較します。

- layer数・名前・visibility
- path数、anchor数、open/closed、fill/strokeの有無
- anchor間の相対座標と、anchorに対するBézier handleの相対座標
- stroke width
- RGB/CMYK process color

Illustratorはlegacy AIを開く際にdocument原点を移動することがあるため、path全体の平行移動は正規化します。RGBはIllustratorの8-bit値への量子化を許容します。

現在、独自DSCコメントで保持しているdocument metadata、layer/pathの安定ID、path名はIllustratorのAI8再保存で除去されます。また、document titleとboundsは保存先名とartwork boundsに変わります。これらは既知のlossとしてレポートの意味合格判定から除外しています。

## 確認済み環境

2026-08-15にIllustrator 30.7.0で次のfixtureが`passed`になりました。

| fixture | 構造 | Illustratorが取得した主な属性 |
| --- | --- | --- |
| `examples/rectangle.ai` | 1 layer / 1 path / 4 anchors | closed、RGB fill/stroke、stroke width 3 |
| `examples/cmyk-curve.ai` | 1 layer / 1 path / 2 anchors | open、Bézier方向点、CMYK stroke、stroke width 4 |

逆方向も同じ環境で確認済みです。

| Illustrator生成fixture | Python readerでの結果 |
| --- | --- |
| `rgb-rectangle` | legacy AI検出、1 layer、4 anchors、closed、RGB fill/stroke、stroke width 3 |
| `cmyk-curve` | legacy AI検出、1 layer、2 anchors、open、Bézier方向点、CMYK stroke、stroke width 4 |

完全往復も両fixtureで`passed`です。RGB矩形は全体が平行移動しRGB値が8-bitへ量子化されましたが、正規化後のpath geometryとpaint属性は一致しました。CMYK Bézierはanchor・handle・CMYK値・stroke widthが保持されました。

これは記載したfixtureと機能subsetの適合結果です。任意のAI7ファイルや未対応機能の互換性を保証するものではありません。
