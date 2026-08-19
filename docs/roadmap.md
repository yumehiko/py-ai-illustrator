# 第1層ロードマップ

更新日: 2026-08-19

## 現在地

第1層v1は完了しています。legacy相互変換、modern semantic reader v2、modern synchronized patch v1、安全編集、preview / visual diff、direct native compile、Illustrator 30.7.0での最終matrixを備えます。正確な保証範囲は各feature profileと[ADR 0002](adr/0002-direct-native-authoring-backend.md)を正とします。

direct native compilerは上位層が生成する低水準`Document` IRを入力とし、Illustrator 2026でcurrent-format AIを構築します。3 fixtureのproduction昇格gateは完了しています。デザインcomponentとエージェントworkflowは兄弟リポジトリ`illustrator-agent`が所有します。

## 拡張方針

網羅的なIllustrator機能翻訳を先回りして目指しません。新しい第1層機能は、`illustrator-agent`または具体的な利用案件から次が揃ったときに追加します。

1. 実際に編集・生成したい`.ai` fixture
2. 必要なread / write / patch / compile operation
3. 未対応情報の保持方針
4. semantic / visual / native editabilityの完了条件
5. 自動回帰テストと、必要ならIllustrator実機matrix

## 既知の候補

- modern linked image patch
- partial textや非矩形pathを含むmodern container
- gradient、pattern、spot color、effect、artboardのmodern operation
- direct native compile profileの追加feature
- より広いPrivateData / PDF cross-representation診断
- SVG / standalone PDF writer

これは実装予定順ではなく、上位層から要求が来たときの調査候補です。要求のない機能網羅より、証明可能なprofileとデザイン資産の非破壊性を優先します。
