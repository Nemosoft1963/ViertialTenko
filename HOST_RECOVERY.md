# Dockerホスト監視・自動復旧

2026年9月24日に待機系で検証された仕様を主系向けに取り込んだ。Dockerエンジンと管理APIを1分間隔で確認し、3回連続失敗後にDocker Desktop起動とComposeサービス復旧を試みる。15分の再実行抑止と多重起動防止を備える。

## ファイル

- `ops/host-watchdog.ps1`: ホスト監視、起動、点呼中判定
- `ops/install-host-recovery.ps1`: Windowsタスク登録（明示的な `-EnableTasks` が必要）

ログは `%LOCALAPPDATA%\ViertialTenkoS2\watchdog\watchdog.log`、状態は同フォルダの `state.json` に保存する。

## 安全な確認

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\ops\install-host-recovery.ps1
```

上記は検証モードでタスクを登録しない。構文確認後、主系で有効化する場合のみ次を実行する。

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\ops\install-host-recovery.ps1 -WeeklyRestartTime 03:00 -EnableTasks
```

待機系は3:30を指定する。登録後は `Get-ScheduledTask -TaskName 'ViertialTenko-*'` で確認する。

## 注意

- 週次処理は点呼中、または点呼状態を確認できない場合は中止する。
- 今回の安全版はDocker/WSLバックエンドの強制停止を実装しない。
- タスク登録後は手動停止も障害と判定するため、保守停止時はWatchdogとStartupタスクを先に無効化する。
- DB、Chromeプロファイル、録音、認証情報を削除しない。
- 共有変更要求には実ファイルがなかったため、READMEの要件と現行Compose/APIから再構築した。
