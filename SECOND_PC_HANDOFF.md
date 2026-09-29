# 2台目PCセットアップ引継ぎ資料

作成日: 2026-09-01  
対象: ViertialTenko 仮想点呼システム

## 1. 目的と構成

現在のPCを優先主系、2台目を待機系として構成する。

- PCのIPアドレスは動的でよい。
- NASを第三の判定点として使用する。
- 主系PC停止後、最大約45秒で待機系が点呼を引き継ぐ。
- 完了点呼、回答、警告状態、会社伝言をNAS経由で相互同期する。
- NASへ接続できない場合は二重点呼防止のため点呼を安全停止する。
- 制御UIから優先主系を変更できる。

## 2. 現在の1台目

- ノードID: primary-pc
- 表示名: Primary-PC
- 優先度: 100
- 管理画面: http://localhost:8080/
- 制御画面: http://localhost:8080/cluster-control
- 監視画面: http://localhost:8080/health-monitor
- Meet画面: http://localhost:3001/
- Meet画面ユーザー: tenko
- パスワードとNAS認証値: .envを参照

パスワードは資料に直接記載しない。.envを安全に管理すること。

## 3. コピー方法と注意

ViertialTenkoフォルダ全体を2台目のローカルディスクへコピーする。

推奨配置:

    C:AI_AutomaterViertialTenko

ネットワーク共有から直接実行するより、Dockerの速度と安定性のためローカルディスクを推奨する。

フォルダコピーに含まれないもの:

- Docker名前付きボリューム
- ChromeのGoogleログイン状態
- ローカルDB
- Whisperモデル
- 録音音声
- 生成動画キャッシュ

完了点呼は起動後にNASから自動取得される。録音音声ファイル自体は同期されない。

## 4. 必要ソフトウェア

- Windows 11
- Docker Desktop
- Docker Compose v2
- NAS 192.168.11.198へ接続できるLAN
- GPUモードを使う場合のみNVIDIA GPU環境

Docker DesktopはLinuxコンテナモードで起動する。

## 5. 起動前の必須変更

重要: 1台目と同じCLUSTER_NODE_IDで起動してはいけない。生存情報が上書きされ、主系判定が壊れる。

コピー先の.envを次へ変更する。

    CLUSTER_NODE_ID=backup-pc
    CLUSTER_NODE_NAME=Backup-PC
    CLUSTER_NODE_PRIORITY=50

次は1台目と同じ値を維持する。

    NAS_CIFS_USERNAME=<1台目と同じNASユーザー>
    NAS_CIFS_PASSWORD=<1台目と同じNASパスワード>

注意:

- ノードIDは英数字、ハイフン、アンダースコアだけを使う。
- 2台のノードIDは必ず異なる値にする。
- .envをメール、チャット、Gitへ登録しない。
- .envは.gitignoreと.dockerignoreで除外済み。

## 6. 初回起動

PowerShellでコピー先へ移動する。

    Set-Location 'C:AI_AutomaterViertialTenko'
    docker compose config --quiet
    docker compose up -d --build

初回はイメージ、Playwright、Whisper関連の準備で時間がかかる。

状態確認:

    docker compose ps
    docker compose logs --tail=50 cluster-coordinator
    docker compose logs --tail=50 app
    docker compose logs --tail=50 response-worker

期待する状態:

- app: Up、healthy
- cluster-coordinator: Up
- health-monitor: Up
- meet-browser: Up
- meet-browser-proxy: Up
- response-worker: Up
- 2台目ログ: leader=primary-pc active=False
- database is lockedが繰り返されていない

gpu-avatar-worker、meet-bot、report-mailerは1台目と同じ稼働方針を維持する。現在は競合回避のため標準起動対象外になっている場合がある。

## 7. Meet画面の初期設定

2台目で http://localhost:3001/ を開く。

- ユーザー: tenko
- パスワード: コピー先.envのCHROME_GUI_PASSWORD

Chrome上で次を行う。

1. 点呼用Googleアカウントへログインする。
2. 対象Google Meetを開く。
3. カメラとマイクを許可する。
4. 必要なら音声自動再生を許可する。
5. Meet参加前画面または会議画面まで進める。
6. Restore Pagesメッセージが出ないことを確認する。

待機系でもMeet接続準備まで済ませる。主系へ切り替わると点呼処理が自動的に有効になる。

## 8. 制御画面の確認

2台目自身:

    http://localhost:8080/cluster-control

別PCから:

    http://<2台目の現在のIP>:8080/cluster-control

確認項目:

- Primary-PCとBackup-PCの2台が表示される。
- 両方がオンライン。
- 現在の主系がprimary-pc。
- 2台目は待機系・点呼停止中。
- NAS判定が正常。
- 最終通信が約5秒ごとに更新される。
- 同期件数が表示される。

## 9. 同期対象

自動同期するもの:

- 完了した点呼
- 点呼に記録された車番・氏名
- 回答
- 要確認フラグ
- 会社伝言の原文、要約、分類

現時点で同期しないもの:

- 録音音声ファイル本体
- 進行中の未完了点呼
- Chromeログイン状態
- Whisperモデル
- GPU生成キャッシュ
- 管理設定全般
- シナリオ定義の変更
- 車番・氏名マスターの手動編集

シナリオやマスターを変更する場合は、当面は両PCで同じ変更を行う。

## 10. 主系切替試験

点呼を行っていない時間帯に実施する。

手動切替:

1. 制御画面で優先主系にBackup-PCを選ぶ。
2. 最大10秒待つ。
3. Backup-PCが主系・点呼実行中になることを確認する。
4. Primary-PCが待機系になることを確認する。
5. 優先主系をPrimary-PCへ戻す。

障害引継ぎ:

1. 点呼中でないことを確認する。
2. 1台目PCまたはcluster-coordinatorを停止する。
3. 約45秒後、2台目が主系になることを確認する。
4. 1台目を復旧する。
5. 両方がオンラインになることを確認する。
6. 優先主系がPrimary-PCならPrimary-PCへ戻ることを確認する。

両方が主系になった場合は、直ちに両PCを待機・保守状態にしてNAS接続を確認する。

## 11. 通常運用と保守

- 通常の優先主系はPrimary-PC。
- Backup-PCも常時起動する。
- 両PCのDocker DesktopとMeetログインを維持する。
- 制御画面で両ノードのオンラインを確認する。

PCを停止・更新する前:

1. 制御画面で、そのPCを待機・保守状態にする。
2. 反対側が主系であることを確認する。
3. 保守対象PCを停止する。

保守後:

1. Dockerを起動する。
2. NAS判定が正常になることを確認する。
3. このPCをクラスタへ復帰させる。
4. 主系が1台だけであることを確認する。

## 12. NAS障害時

NAS停止やNAS通信断では両PCの点呼が安全停止する。これは仕様上の正常動作である。

復旧:

1. NASとLANを復旧する。
2. 両PCからNAS共有へ接続できることを確認する。
3. cluster-coordinatorのエラーが消えることを確認する。
4. 制御画面でNAS判定が正常になることを確認する。
5. 主系が1台だけ選出されることを確認する。

NAS停止中に点呼ワーカーを強制起動しない。

## 13. 確認コマンド

    docker compose ps
    docker compose logs --tail=100 cluster-coordinator
    docker compose logs --tail=100 app
    docker compose logs --tail=100 meet-browser
    docker compose logs --tail=100 response-worker
    docker compose logs --tail=100 health-monitor

DBロック確認:

    docker compose logs app response-worker cluster-coordinator | Select-String 'database is locked'

NASボリューム確認:

    docker volume inspect viertialtenko_cluster-shared
    docker compose exec -T cluster-coordinator python -c "from pathlib import Path; print(Path('/cluster-shared').exists()); print(list(Path('/cluster-shared/nodes').glob('*.json')))"

クラスタAPI:

    Invoke-RestMethod http://localhost:8080/api/cluster | ConvertTo-Json -Depth 8

## 14. 起動停止

起動:

    docker compose up -d

再構築:

    docker compose up -d --build

停止:

    docker compose down

注意: docker compose down -vはDockerボリュームを削除するため使用禁止。

## 15. セキュリティ

- .envにはNAS、Meet画面、メール等の認証情報が含まれる。
- .envをGitへコミットしない。
- 画面共有やログ採取時に.envを表示しない。
- NASアカウントは必要最小限の権限にする。
- 制御UIはLAN内だけで使い、インターネットへ公開しない。
- 8080、3001、3002番ポートを外部公開しない。
- パスワード変更時は両PCの.envを更新する。

## 16. 完了チェックリスト

- [ ] プロジェクトを2台目のローカルディスクへコピー
- [ ] Docker Desktopを導入・起動
- [ ] CLUSTER_NODE_IDをbackup-pcへ変更
- [ ] CLUSTER_NODE_NAMEをBackup-PCへ変更
- [ ] CLUSTER_NODE_PRIORITYを50へ変更
- [ ] NAS認証情報を設定
- [ ] docker compose config --quiet成功
- [ ] docker compose up -d --build成功
- [ ] 主要コンテナがUp
- [ ] ChromeでGoogle Meetへログイン
- [ ] カメラ・マイクを許可
- [ ] 制御画面に2台を表示
- [ ] Primary-PCが主系、Backup-PCが待機系
- [ ] 完了済み点呼を2台目へ同期
- [ ] 手動主系切替と復帰に成功
- [ ] DBロックなし
- [ ] .envがGit管理対象外

## 17. 関連資料

- CLUSTER_SETUP.md
- RESUME.md
- DEVELOPMENT_CHANGE_MEMO_2026-08-27.md
- README.md
- NATURAL_DIALOGUE.md
- REALTIME_DIALOGUE.md
- GPU_REALTIME.md
- RECORDINGS.md

## 18. 現在の確認済み状態

2026-09-01時点:

- Primary-PCが主系として稼働中。
- NAS判定正常。
- 既存完了点呼62件をNASへ初回同期済み。
- 模擬待機系でPrimary-PCからBackup-PC、Primary-PCへの切替成功。
- 全39テスト成功。
- DBロックなし。
- システム監視healthy。