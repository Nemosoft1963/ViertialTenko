# 仮想点呼システム 開発修正メモ

- 対象プロジェクト: `ViertialTenko`
- 記録日: 2026-08-27
- 用途: 現行PJからブランチしたシステムを改修する際の引継ぎ・作業基準
- 現行動作モード: `realtime_natural`（自然会話点呼）

## 1. 本日の変更概要

本日は、点呼の重複開始、会社伝言時の映像崩れ、Chrome復元通知、SQLiteロック、複数参加者の順番点呼を中心に改修した。

最終的な主要機能は次のとおり。

1. 同一参加中の点呼ループを防止
2. 「会社に伝えることは？」の固定質問を高品質GPU映像へ変更
3. Chromeの「Restore pages?」通知を非表示化
4. SQLiteロック発生時の復旧と不要なDB書込み削減
5. 複数参加者の待ち行列、対象者固定、名前呼び、完了後の自動切替
6. Dockerボリューム一式の移植用バックアップ作成

## 2. 点呼ループ防止

### 発生していた問題

点呼完了後も相手がMeetに残っていると、参加人数フォールバックが同じ相手について新しい点呼を作成していた。

実例では点呼 `#63` の完了後、同じ連続参加中に `#64` が自動作成されていた。

### 対応

設定 `participant_worker_presence_started` を追加した。

- 点呼開始時に `1`
- Meet参加人数が1人以下へ戻った場合に `0`
- 同じ連続参加中は人数フォールバックによる点呼再作成を禁止
- 不在中は値が既に `0` の場合、毎秒UPDATEしない

### 関連ファイル

- `app/meet_participant_worker_v2.py`
- `tests/test_participant_departure.py`

## 3. 会社伝言質問の映像品質

### 発生していた問題

`awaiting_company_message` を速度優先にしていたため、MuseTalk映像を使わず、簡易口パク画像へ切り替わっていた。これにより「会社に伝えることは？」の場面だけ口元が崩れて見えた。

### 対応

- 固定質問はGPU高品質経路へ戻した
- 毎回内容が変わる伝言復唱・確認のみ高速経路を使用
- 固定質問のGPUキャッシュを確認済み

キャッシュキー:

```text
77cd4c399da8dd59daa406af.mp4
```

固定文:

```text
最後に、会社へ伝えておきたいことはありますか。なければ、特にありません、とお答えください。
```

### 関連ファイル

- `app/realtime_dialogue.py`
- `app/realtime_browser.py`
- `tests/test_realtime_dialogue.py`

## 4. Chrome復元通知の非表示

### 発生していた問題

Meet画面にChromeの「Restore pages?／ページを復元しますか？」通知が表示されていた。

### 対応

`CHROME_CLI` へ次のオプションを追加した。

```text
--hide-crash-restore-bubble
--disable-session-crashed-bubble
```

Chromeプロセスへ実際に渡っていることを確認済み。

### 関連ファイル

- `docker-compose.yml`

## 5. SQLiteロック問題

### 発生していた問題

次のワーカーで `sqlite3.OperationalError: database is locked` が連続し、参加人数が2人でも点呼レコードを作成できなかった。

- Meet参加監視
- 音声認識
- GPU映像
- 帳票メール
- 旧Meet Bot

### 実施した復旧

- 関連ワーカーを一時停止
- `PRAGMA wal_checkpoint(TRUNCATE)` を実行
- `PRAGMA integrity_check` が `ok` であることを確認
- DB書込みテスト成功
- ワーカーを順番に再起動して切り分け
- 不在時の不要な毎秒書込みを削減

### 特定した主な競合元

GPU映像ワーカーを停止するとDB書込みが即時成功したため、現時点ではGPUワーカーを主なロック保持元と判断している。

### 現在の暫定運用

- `gpu-avatar-worker`: 停止
- `meet-bot`: 停止（simulation時は現在のMeet監視と重複する旧ワーカー）
- `report-mailer`: 停止
- 自然会話点呼は音声フォールバックで稼働
- `gpu_pipeline_state=error` を設定し、Meet側がGPU待ちで停止せず音声再生へ進むようにしている

設定されている説明:

```text
DBロック回避のためGPU映像を一時停止。音声対話へ自動フォールバック中。
```

### ブランチ側で優先すべき恒久対策

1. SQLite接続処理を共通化する
2. 全ワーカーで `busy_timeout`、WAL、短いトランザクションを統一する
3. DB接続中にモデルロード、TTS、GPU推論、sleepを行わない
4. GPUワーカーはDB読取り後すぐ接続を閉じ、推論終了後に短時間だけ再接続する
5. ステータス更新を毎ポーリングで書かず、値が変化した時だけUPDATEする
6. `database is locked` に対する指数バックオフ付きリトライを共通実装する
7. 帳票メールと旧Meet Botを含めて同時起動耐久テストを行う

## 6. 複数参加者の順番点呼

### 要件

複数人が点呼中にMeetへ入った場合でも、現在の相手を指定して点呼を続ける。現在対象の点呼が終わったら、次の参加者へ自動で切り替える。

### 実装内容

`participant_queue` テーブルを追加した。

保持項目:

- Meet表示名
- Meet参加者ID
- 待機状態
- 入室日時
- 点呼開始日時
- 作成されたcheckin ID

動作:

1. 最初の参加者について点呼を開始
2. 点呼中に追加参加者が入った場合は `waiting` として登録
3. 現在の点呼対象を完了まで固定
4. 対象者名が取得できる場合は名前を呼ぶ
5. 「ほかの方は順番にお呼びしますので、そのままお待ちください」と案内
6. 現在の点呼完了時、待ち行列先頭から新しいcheckinを作成
7. 「続いて、○○さん」と案内し、車番質問から開始
8. Meet参加通知を取りこぼした場合は参加人数増加分を匿名参加者として待ち行列へ登録
9. Meet参加者IDにより同一参加者の重複登録を防止

### 制約

Google Meetの受信音声は全員分が混合される。そのため、技術的に特定参加者の音声だけを分離して認識しているわけではない。

現在は名前を呼び、ほかの参加者へ待機を依頼する会話制御で対象を限定している。将来、厳密な話者分離が必要な場合は話者ダイアライゼーションまたは参加者別音声トラック取得の検討が必要。

### 関連ファイル

- `app/participant_queue.py`（新規）
- `app/db.py`
- `app/meet_participant_worker_v2.py`
- `app/meet_response_worker_v2.py`
- `Dockerfile.chrome`
- `tests/test_participant_departure.py`

## 7. DBスキーマ追加

次のテーブルとインデックスを追加した。

```sql
CREATE TABLE IF NOT EXISTS participant_queue (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    display_name TEXT NOT NULL,
    meet_participant_id TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'waiting',
    joined_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    started_at TEXT,
    checkin_id INTEGER
);

CREATE INDEX IF NOT EXISTS idx_participant_queue_status
ON participant_queue(status,id);
```

既存DBは `init_db()` 実行時または待ち行列処理の初回呼出し時に自動作成される。

## 8. Dockerボリュームバックアップ

移植用として4つのDockerボリュームをPJフォルダ内へ保存した。

保存先:

```text
docker-volumes-backup/20260827-141501/
```

内容:

- `tenko-data.tar.gz` — 点呼DB、録音、Whisperモデルなど
- `meet-browser-config.tar.gz` — Chromeプロファイル、Meetログイン状態、仮想カメラ、生成映像
- `meet_profile.tar.gz` — 旧Meet Botプロファイル（現状ほぼ空）
- `musetalk-models.tar.gz` — MuseTalkモデル
- `SHA256SUMS.txt` — 破損確認用
- `RESTORE.md` — 復元手順

注意:

```text
docker compose down -v
```

はDockerボリュームを削除するため、移植・検証中は実行しないこと。

バックアップ作成後にもソースコード改修を行っている。ボリュームバックアップはデータ移植用であり、最新ソースはPJフォルダ本体をコピーすること。

## 9. テスト結果

最終テスト結果:

```text
Ran 39 tests
OK
```

追加した主な回帰テスト:

- 点呼完了後、同じ連続参加中に再点呼しない
- 人数が1人へ戻ると次の点呼を許可する
- 2人目を待ち行列へ登録する
- 1人目の完了後、2人目のcheckinを自動作成する
- 2人目の名前を開始プロンプトに含める
- 匿名参加者が複数でも入室順に待機できる
- 会社伝言質問はGPU高品質経路を使用する
- 動的な会社伝言復唱は高速経路を維持する

実行コマンド:

```powershell
docker compose exec -T app python -m unittest discover -s tests -v
```

## 10. 現在の稼働状態

稼働中:

- `app`
- `meet-browser`
- `meet-browser-proxy`
- `response-worker`

停止中:

- `gpu-avatar-worker`
- `meet-bot`
- `report-mailer`

管理UI:

```text
http://localhost:8080
```

Meet画面:

```text
https://localhost:3001
```

ヘルスチェック:

```json
{"ok":true,"mode":"simulation","operation_mode":"realtime_natural"}
```

## 11. ブランチ側への推奨作業順

1. PJフォルダを隠しファイル込みでコピーする
2. `.env` は安全な方法で移す
3. 必要ならDockerボリュームを復元する
4. `RESUME.md` と本メモを読む
5. GPUワーカーを停止した状態でコア点呼を起動する
6. 1人で車番から会社伝言終了まで確認する
7. 3人以上で順番点呼を確認する
8. 対象外参加者が発話した場合の誤認識を評価する
9. SQLite共通接続・リトライ処理を実装する
10. GPUワーカーを単独起動してDBロック耐久試験を行う
11. 帳票メールを戻して同時起動試験を行う
12. 全39テストを実行する

推奨ブランチ名例:

```text
feature/multi-participant-checkin-v2
```

## 12. 受入確認項目

- [ ] Meet参加人数が1人の場合は待機する
- [ ] 2人になった時に点呼が1回だけ始まる
- [ ] 点呼中に3人目が入ると待ち行列へ登録される
- [ ] 現在対象者の名前を呼ぶ
- [ ] 現在対象者の点呼が途中参加で中断されない
- [ ] 完了後に次参加者を名前で呼ぶ
- [ ] 匿名参加者も人数増加順に処理される
- [ ] 最後の参加者完了後は新規入室待機へ戻る
- [ ] 同じ参加者について点呼がループしない
- [ ] DBロックが発生しない
- [ ] ChromeのRestore pages通知が表示されない
- [ ] 録音がcheckin単位で保存される
- [ ] 会社伝言が整理・復唱・確認される
- [ ] 全39テストが成功する

## 13. Gitに関する注意

現在の共有フォルダでは、コマンド実行環境から `.git` を通常のGitリポジトリとして認識できなかった。

```text
fatal: not a git repository
```

ブランチ作成前に、新PCまたはローカルドライブへコピーした後で次を確認すること。

```powershell
git status
git branch --show-current
git log -1 --oneline
```

`.git` が不完全な場合は、正しいリモートからcloneし、PJファイルを重ねる方法を推奨する。現状の共有フォルダ上で履歴が確認できないままcommitしないこと。

## 14. 参照資料

- `RESUME.md`
- `README.md`
- `NATURAL_DIALOGUE.md`
- `REALTIME_DIALOGUE.md`
- `GPU_REALTIME.md`
- `RECORDINGS.md`
- `docker-volumes-backup/20260827-141501/RESTORE.md`
## 2026-08-27 システム監視・ログ
- health-monitor サービスを追加。30秒周期で管理API、SQLite読取、進行中点呼、参加者待ち行列、Meet監視更新、音声入力更新、対話状態、GPU状態、ディスク空き容量を確認する。
- DBは読み取り専用URIで参照し、監視処理によるDBロックを避ける。
- ログは /data/health-logs/YYYY-MM-DD.jsonl、最新状態は current.json。保持期間は90日。
- 管理画面 /health-monitor とAPI /api/health-monitor を追加。自動復旧・コンテナ再起動は行わず、検知と記録のみ。
- 起動直後の初回はapp準備前のためcriticalを記録したが、次周期でhealthyへ自動復帰。DBロックなし。

## 2026-09-01 2台冗長化・NAS連携
- 動的IPに依存しないNASハートビート方式の主系・待機系制御を追加。
- primary-pcを初期優先主系として設定。優先主系は /cluster-control から変更可能。
- 5秒周期、45秒タイムアウト。NAS接続不可時は二重点呼防止のため安全停止。
- 完了点呼、回答、警告、会社伝言をNASへ書き出し、相手DBへ重複なしで取り込む。
- 既存完了点呼62件を初回NAS同期。以後差分同期。
- 待機系を模擬し primary-pc → backup-pc → primary-pc の主系切替に成功。
- 全39テスト成功。DBロックなし。
- 詳細は CLUSTER_SETUP.md。

## 2026-09-02 同期完全性確認機能
- 各ノードがNAS同期原本とローカルDBを点呼単位で照合するsync_auditを追加。
- 点呼本体、回答一覧、会社伝言を比較し、source_total、verified、missing、mismatch、corruptをNAS状態ファイルへ報告。
- /cluster-controlへノード別の同期原本、完全一致、欠落、不一致、破損、監査時刻を表示。
- /api/clusterのnodesへsync_auditを追加。
- 正常一致、欠落、不一致、JSON破損の4テストを追加。全43テスト成功。
- 主系実データは77/77件完全一致、欠落0、不一致0、破損0。
- 待機系は現在version 1で監査未報告。STANDBY_SYNC_AUDIT_CHANGE_INSTRUCTION.mdの適用が必要。

## 2026-09-02 車番・氏名マスター双方向同期
- driver_identitiesをNASへノード別スナップショット出力し、車番をキーに氏名、Meet参加者ID、確認日時、利用回数、確認済み状態を同期。
- 確認済みを優先し、同条件は最終確認日時が新しい内容を採用。
- identity_tombstonesを追加し、UIからの削除も他系へ伝播。
- /cluster-controlへ共有マスター、完全一致、欠落、余分、不一致、削除履歴、破損を表示。
- マスター追加、競合更新、削除伝播の3テストを追加。全46テスト成功。
- Primary-PCは24/24件完全一致、欠落0、余分0、不一致0、破損0。
- 対応版はversion 3。Backup-PCは現在version 2のため変更指示書の適用が必要。

## 2026-09-02 点呼シナリオ共有
主系基準のシナリオ・安全設定同期（プロトコルv4）と管理UI監査を追加。余分なローカルシナリオは参照保護のため削除しない。
