# Contributing

## 基本方針

点呼は安全に関係するため、会話の自然さより状態遷移の確実性、監査可能性、フォールバックを優先します。LLMの出力だけで車番、本人、安全項目、完了状態を確定しないでください。

## 開発手順

1. Issueまたは変更目的を明確にする。
2. 小さなブランチで変更する。
3. 認証情報、録音、DB、Googleプロファイル、モデルをコミットしない。
4. 関係するユニットテストを追加する。
5. Compose構文とテストを確認する。
6. 主系・待機系、NAS同期、フォールバックへの影響を記載する。

```powershell
docker compose --env-file .env.example config --quiet
python -m pip install -r requirements.txt pytest
python -m pytest -q
```

## 必須の観点

- 質問番号を誤って進めない
- 同一質問を無限に繰り返さない
- BOT自身の音声を回答として認識しない
- 一人の点呼中に別参加者へ対象が切り替わらない
- 車番、氏名、否定、安全語、会社伝達事項を正しく扱う
- DBロックやNAS遅延が音声処理を停止させない
- 障害時に既存の決定論的モードへ戻れる

## MuseTalk

MuseTalkは `third_party/MuseTalk` のGitサブモジュールです。クローン時は `--recurse-submodules` を使ってください。ローカル互換変更は外部リポジトリを直接改変せず、`patches/` に最小パッチとして保存します。

## Pull Request

PRには目的、変更範囲、確認結果、運用への影響、戻し方を記載してください。画面画像やログを添付する前に、氏名、車番、Meet ID、URL、IPアドレス、認証情報を除去してください。
