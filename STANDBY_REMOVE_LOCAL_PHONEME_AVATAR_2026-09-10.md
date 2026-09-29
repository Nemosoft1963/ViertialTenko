# 待機系変更指示書：ローカル音素連動3Dの削除

作成日：2026-09-10

## 目的

主系で試験した `realtime_local_avatar`（ローカル音素連動3D）は失敗判定となったため、主系・待機系の双方から削除します。
既存の `realtime`、`realtime_gpu`、`realtime_natural`、`realtime_3d` は維持します。
MetaHuman関連は保留のまま残し、この指示では変更しません。

## 適用内容

置換するファイル：

- `app/avatar3d.js`
- `app/realtime_browser.py`
- `app/realtime_dialogue.py`
- `app/main.py`
- `app/templates/index.html`
- `Dockerfile.chrome`
- `tests/test_avatar3d_browser.py`

削除するファイル：

- `app/local_tts.py`
- `tests/test_local_avatar.py`
- `LOCAL_PHONEME_AVATAR.md`

削除される機能・依存：

- 動作モード `realtime_local_avatar`
- `/avatar-local` プレビュー
- Open JTalk、MeCab、HTS音声パッケージのDocker依存
- 音素時刻、五母音、顎・口幅・唇丸めの追加処理

従来3Dの `avatar3d.js` は試験導入前の音量連動方式へ復元します。

## 待機系での適用手順

1. 待機系が通話を処理していないことを確認し、保守状態にする。
2. 同梱ZIPをプロジェクトルートへ展開し、置換対象を上書きする。
3. 上記3ファイルを削除する。存在しない場合はそのままでよい。
4. `realtime_local_avatar` がDBに残っている場合だけ、管理UIから `realtime` へ切り替える。本指示適用時点の主系は `realtime`。
5. `docker compose build app meet-browser meet-bot` を実行する。
6. SQLiteロックを避ける既存手順で再起動する。

```powershell
docker compose stop cluster-coordinator meet-browser meet-bot response-worker report-mailer gpu-avatar-worker
docker compose up -d --no-deps app
# /api/health のok:trueを確認
docker compose up -d --no-deps meet-browser meet-bot
docker compose start cluster-coordinator response-worker report-mailer gpu-avatar-worker
```

7. 管理UIに「ローカル音素連動3D」が存在しないこと、`/avatar-local` が404になることを確認する。
8. `/api/health`、全Dockerサービス、Meet再接続、通常発話を確認して保守解除する。

## 主系確認結果

- 削除対象文字列の残存：なし
- Python構文確認：成功
- Dockerイメージ再構築：成功
- 全62件のunittest：成功
- 主系の動作モード：`realtime`

## 注意

- `.env`、DB、録音データ、車番・氏名、点呼結果は本パッケージに含めない。
- ZIP展開だけでは削除対象ファイルは消えないため、削除一覧を必ず実施する。
- 主系と待機系の優先度、リーダー、保守設定は変更しない。
