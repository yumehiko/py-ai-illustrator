# 調査ソース

調査日: 2026-08-14

技術判断では、Adobe の公式資料、仕様書、実装リポジトリなどの一次情報を優先しました。

## Adobe / 仕様

- [Adobe: How to save artwork in Illustrator](https://helpx.adobe.com/illustrator/using/saving-artwork.html)
  - AI、PDF、EPS、SVG の保存特性、PDF-compatible option、legacy version の制約。
- [Adobe: How to create Adobe PDF files in Illustrator](https://helpx.adobe.com/illustrator/using/creating-pdf-files.html)
  - Illustrator Default preset が Illustrator data を保持すること、Save As と Export の違い。
- [Adobe: IllustratorSaveOptions](https://ai-scripting.docsforadobe.dev/jsobjref/IllustratorSaveOptions/)
  - `pdfCompatible`、`compressed`、`embedLinkedFiles`、互換バージョンなど。
- [Adobe: PDF Reference 1.7](https://opensource.adobe.com/dc-acrobat-sdk-docs/pdfstandards/pdfreference1.7old.pdf)
  - PDF container、object、stream、PieceInfo 等の基礎仕様。
- [Adobe Illustrator File Format Specification, version 7 (mirror)](https://13thmonkey.org/documentation/fonts/AI7FileFormat.pdf)
  - 1998 年の PostScript ベース AI 構文。現代 PrivateData の完全仕様ではない。
- [Adobe archive: Illustrator troubleshooting](https://helpx.adobe.com/archive/illustrator/illustrator-cs4-troubleshooting.pdf)
  - AI が PDF と PGF を併存させる説明、PDF compatible option のファイルサイズへの影響。

## 先行 OSS

- [forticheprod/py-aep](https://github.com/forticheprod/py-aep)
  - 本構想の比較対象。AEP の RIFX を Python object model へ変換し、編集・保存する。
- [Inkscape extension-ai / inkai](https://gitlab.com/inkscape/extras/extension-ai)
  - Python の AI PrivateData reader/parser、typed object、SVG conversion。GPL-2.0-or-later。
- [inkai pyproject.toml](https://gitlab.com/inkscape/extras/extension-ai/-/blob/main/pyproject.toml)
  - Python 要件、依存関係、version、Pre-Alpha classification。
- [Inkscape AI Import Project – Take 2](https://lists.inkscape.org/hyperkitty/list/inkscape-board%40lists.inkscape.org/message/WGWWBRUMJKBM3TOFGZAJQ2P5GFWPLLXT/attachment/4/AIImportProjectTake2.pdf)
  - PrivateData 抽出、deflate/zstd、対応済み構造、未実装 feature の整理。
- [opendesigndev/illustrator-parser-pdfcpu](https://github.com/opendesigndev/illustrator-parser-pdfcpu)
  - Go/WASM + TypeScript の PDF/PrivateData parser。Apache-2.0。
- [Inkscape Beginners' Guide: import formats](https://gitlab.com/inkscape/inkscape-docs/manuals/blob/master/Inkscape-Beginners-Guide/source/import-other-formats.rst)
  - Illustrator 9 以降の AI import の位置づけ。

## 調査から除外したもの

- `.ai` を画像へ rasterize するだけのツール
- Illustrator を GUI/COM/AppleScript/ExtendScript で遠隔操作するだけの実装
- PDF を `.ai` に rename するだけの手法
- 出所・ライセンスが不明な proprietary converter

これらはプレビューや検証の補助には使えますが、「Python object と AI の相互変換」という中核要件を満たしません。
