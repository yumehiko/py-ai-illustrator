# 第1層の開発原則

このリポジトリは`.ai`と低水準Python IRの変換・編集・検証に集中します。デザインcomponent、layout、theme、自然言語workflowは`illustrator-agent`が所有します。

## 原則

1. 読めることと、安全に再保存できることを分ける。
2. 未対応featureを黙って破棄しない。
3. 入力は既定で上書きせず、局所patch、precondition、別名保存を優先する。
4. byte、graphic semantics、visual、native editabilityの保証を分けて報告する。
5. 圧縮stream、再帰構造、入力サイズに上限を設ける。
6. Python APIの抽象より、実ファイルfixtureと再現可能な検証を先に置く。
7. 第1層は上位層へ依存しない。

## 機能追加の入口

新機能は、上位層の具体的な要求に対応する最小profileとして実装します。fixture、対象version、operation、保持条件、失敗条件、検証方法が不明な段階では、対応済みと推測せずdiagnosticを返します。

このプロジェクトはpre-1.0のためPython APIの後方互換はまだ保証しません。一方、入力ファイルの非破壊性、未知情報の保持、損失の明示は初期段階から製品要件です。
