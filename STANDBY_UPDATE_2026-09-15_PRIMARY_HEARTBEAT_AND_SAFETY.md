# 待機系変更指示書（2026-09-15）

## 運用ルール

- 点呼を実行中のPCを主系とする。
- 通常はどちらのPCが実行主系になってもよい。
- 同一時刻に両方が主系になってはならない。
- 現在の優先主系は `primary-pc` とする。

## 必須変更

1. `app/cluster_coordinator.py` を置換する。
   - 重いNAS同期とは独立して5秒ごとにハートビートを更新する。
   - 同期処理が45秒を超えても相手を停止と誤判定しない。
2. `app/logistics_speech.py` と `app/meet_response_worker_v2.py` を置換する。
   - フロントガラス文脈の「日々」を「ひび」に補正する。
   - 車両安全に関する伝言を要注意として扱う。
3. `Dockerfile.chrome` と `meet-browser-init/10-fix-startwm-permissions` を反映する。
   - Meet起動時の `startwm_wayland.sh: Permission denied` を防止する。

## 待機系での反映手順

同梱ファイルを同じ相対パスへ上書きしてから、プロジェクトフォルダで実行する。

```powershell
docker compose up -d --build app response-worker cluster-coordinator meet-browser
docker compose up -d --force-recreate meet-browser-proxy
docker compose exec -T app python -m unittest discover -s tests -v
```

## 完了条件

- 全76テストが成功する。
- `nodes/primary-pc.json` の時刻が約5秒間隔で更新される。
- NASの `status/backup-pc.json` が、実行主系を1台だけ報告する。
- `sync_audit` の missing / mismatch / corrupt がすべて0になる。
- Meetログに `startwm_wayland.sh: Permission denied` が出ない。
