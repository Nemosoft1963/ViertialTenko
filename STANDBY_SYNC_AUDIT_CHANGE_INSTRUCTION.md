# 待機系 同期確認機能 変更指示書

作成日: 2026-09-02  
対象: Backup-PC および同一構成から派生した点呼システム

## 1. 変更目的

各ノードのローカルDBがNAS上の同期原本と完全一致しているかを、主系の冗長化制御UIから確認できるようにする。

確認対象:

- 完了点呼本体
- 回答一覧
- 会社伝言の原文、要約、分類
- NAS同期ファイルのJSON破損
- ローカルDBでの欠落
- NAS原本との内容不一致

## 2. 変更ファイル

次のファイルを主系の最新版から待機系へコピーする。

- app/cluster_coordinator.py
- app/main.py
- app/templates/cluster_control.html
- tests/test_cluster_sync.py

docker-compose.ymlは今回変更なし。既存のクラスタサービス設定を維持する。

## 3. 重要な事前確認

待機系の.envは次の値を維持する。主系の.envで上書きしてはいけない。

    CLUSTER_NODE_ID=backup-pc
    CLUSTER_NODE_NAME=Backup-PC
    CLUSTER_NODE_PRIORITY=50

NAS_CIFS_USERNAMEとNAS_CIFS_PASSWORDも待機系の既存値を維持する。

作業前に点呼中でないことを確認する。制御画面で主系がPrimary-PC、待機系がBackup-PCになっていることを確認する。

## 4. 適用手順

待機系PCでPowerShellを開く。

    Set-Location '<待機系のViertialTenkoフォルダ>'
    docker compose config --quiet
    python -m py_compile appcluster_coordinator.py appmain.py
    docker compose up -d --build app cluster-coordinator

待機系のMeet、response-workerを停止する必要はない。コード一式を同じイメージで揃える場合は次を実行してもよい。

    docker compose up -d --build

## 5. 自動テスト

待機系で実行する。

    docker compose exec -T app python -m unittest discover -s tests -v

期待結果:

- test_complete_match: OK
- test_missing_local_result: OK
- test_mismatch_is_detected: OK
- test_corrupt_json_is_detected: OK
- 既存テストを含め全件OK

## 6. 適用後確認

主系または待機系の制御画面を開く。

    http://localhost:8080/cluster-control

ノードと同期完全性の表で、Primary-PCとBackup-PCの両方について次を確認する。

- 同期原本: NAS上の総点呼数
- 完全一致: 同期原本と同じ件数
- 欠落: 0
- 不一致: 0
- 破損: 0
- 監査時刻: 約5秒から十数秒間隔で更新

正常条件:

    完全一致 = 同期原本
    欠落 = 0
    不一致 = 0
    破損 = 0

Backup-PCが未報告の場合、待機系のcluster-coordinatorが旧版のままか、再構築されていない。

## 7. API確認

    Invoke-RestMethod http://localhost:8080/api/cluster | ConvertTo-Json -Depth 10

各nodes要素のsync_auditに次が含まれること。

- timestamp
- source_total
- verified
- missing
- mismatch
- corrupt
- by_origin
- details

## 8. 異常時

欠落が1以上:

- 待機系cluster-coordinatorログを確認する。
- 約10秒待って再確認する。
- import failedがあれば該当点呼の取り込みエラーを調査する。

不一致が1以上:

- sync_audit.detailsのorigin_nodeとorigin_idを確認する。
- ローカルDBを直接上書きしない。
- NAS原本とDBの点呼、回答、会社伝言を比較する。

破損が1以上:

- NAS上の該当JSONを削除・編集せず退避する。
- 発生元ノードのDBから安全に再出力する。
- 作業前にバックアップを取得する。

## 9. ロールバック

問題が起きた場合は、変更前の4ファイルへ戻して次を実行する。

    docker compose up -d --build app cluster-coordinator

NAS上のresults、nodes、statusは削除しない。

## 10. 注意事項

- 主系と待機系でCLUSTER_NODE_IDを同じにしない。
- 主系の.envを待機系へコピーしない。
- docker compose down -vを実行しない。
- NAS同期JSONを手作業で修正しない。
- 録音音声、設定、シナリオ、氏名マスター、進行中点呼は今回の完全性監査対象外。
## 追加変更: 車番・氏名マスター同期（2026-09-02）

待機系へ次の最新版ファイルも適用する。

- app/cluster_coordinator.py
- app/db.py
- app/main.py
- app/templates/cluster_control.html
- tests/test_cluster_sync.py

追加内容:

- driver_identitiesをNASのidentitiesフォルダへノード別スナップショットとして出力。
- 車番をキーに氏名、Meet参加者ID、初回・最終確認日時、利用回数、確認済み状態を双方向同期。
- 確認済みを優先し、同条件では最終確認日時が新しい内容を採用。
- UI削除時にidentity_tombstonesへ削除履歴を保存し、他系でも削除。
- 制御UIへ共有マスター、完全一致、欠落、余分、不一致、削除履歴、破損を表示。

待機系で再構築:

    docker compose up -d --build app cluster-coordinator

確認条件:

- Primary-PCとBackup-PCの共有マスター件数が同じ。
- 完全一致＝共有マスター。
- 欠落、余分、不一致、破損がすべて0。
- 車番・氏名マスター画面で同じ組み合わせが表示される。

注意:

- 初回統合では、どちらか一方だけに存在する既存マスターは共有マスターへ統合される。
- 同じ車番で異なる氏名がある場合は、確認済み状態と最終確認日時で自動決定される。
- 初回同期後に制御UIとマスター一覧を目視確認する。

## 適用版の確認

車番・氏名マスター同期対応版のハートビートversionは3。
待機系適用後、/api/clusterでBackup-PCのversionが3になり、identity_auditが空でないことを確認する。

## 追加変更: 点呼シナリオ・動作設定の共有（同期プロトコル v4）

最新の完全互換バージョンは v4 です。v3 の車番・名前同期に加え、現在の主系を正として次をNAS経由で共有します。

- 点呼シナリオ名、質問JSON、作成・更新日時
- 選択中シナリオ（名前で対応付け、各PCのローカルID差を吸収）
- Meet URL、BOT表示名、Meet BOTアカウント名、動作モード

SMTPパスワードなどの秘密情報、管理トークン、ワーカーの一時状態は共有しません。待機系だけに存在するシナリオは、過去の点呼結果との参照を保護するため自動削除せず、管理UIで「余分」として表示します。

待機系へ反映するファイル:

- app/cluster_coordinator.py
- app/main.py
- app/templates/cluster_control.html
- tests/test_cluster_sync.py

反映後は `docker compose up -d --build app cluster-coordinator` を実行し、管理UIで両ノードの version が 4、設定同期状態が ok、シナリオの不足・不一致と設定不一致が 0 であることを確認してください。主系の v4 反映前は `awaiting_leader_snapshot` が正常な待機表示です。