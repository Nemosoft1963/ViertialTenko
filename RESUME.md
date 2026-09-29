# 仮想点呼システム 再開メモ

最終更新: 2026-08-24（Asia/Tokyo）

## 現在の状態

- Docker Composeでシステムを起動済み。
- `GET /api/health` は `200 {"ok":true,"mode":"simulation"}` を返している。
- アプリ本体のコンテナは `healthy`。
- 現在は `simulation` モード。

起動中のサービス:

- `app`
- `meet-bot`
- `response-worker`
- `report-mailer`
- `meet-browser`
- `meet-browser-proxy`

## URL

- 管理UI: http://localhost:8080
- ヘルスチェック: http://localhost:8080/api/health
- Meetブラウザ画面: http://localhost:3001
- Meetブラウザプロキシ: http://localhost:3002

管理UIにはログイン認証なし。Meetブラウザ画面のユーザー名は `tenko`。
パスワードは `.env` の `CHROME_GUI_PASSWORD` を参照すること（このメモには秘密情報を複製しない）。

## システム概要

Google Meetに参加した運転者を対象に、車両番号・氏名の確認、体調確認、シナリオ質問、回答・判定の記録を行う仮想点呼システム。

主な機能:

- シミュレーションモードとGoogle Meetモード
- 音声による車両番号・氏名の確認
- シナリオに基づく質問と回答記録
- 参加者退出時の点呼中断と次参加者の待機
- 仮想カメラ・仮想マイク
- MuseTalk 1.5によるリップシンク映像生成
- 日次・月次点呼CSVのメール送信

## 直近の実装内容

直近では日次・月次点呼CSVメール機能を中心に実装している。

- SMTP設定UI
- 日次CSVの自動・手動送信
- 月次CSVの自動・手動送信
- 同一対象期間の重複送信防止
- `report-mailer` 常駐ワーカー
- CSVへの日時、車両、氏名、シナリオ、状態、質問、回答、判定の出力

関連する主なファイル:

- `app/main.py`
- `app/report_mailer.py`
- `app/daily_report_worker.py`
- `app/templates/index.html`
- `tests/test_report_mailer.py`

その他のテスト:

- `tests/test_vehicle_identity.py`
- `tests/test_participant_departure.py`
- `tests/test_bot_rejoin.py`

## 再開時の確認手順

```powershell
docker compose ps
Invoke-WebRequest -UseBasicParsing http://localhost:8080/api/health
```

停止している場合:

```powershell
docker compose up -d
```

ログ確認:

```powershell
docker compose logs --tail=100 app
docker compose logs --tail=100 meet-bot
docker compose logs --tail=100 response-worker
docker compose logs --tail=100 report-mailer
```

停止:

```powershell
docker compose down
```

## 注意事項

- `README.md` は現在、日本語が文字化けして見える状態。内容を編集するときは文字コードを確認する。
- `.env` には認証情報が含まれるため、内容をチャットやコミットへ不用意に転載しない。
- この共有フォルダでは通常の `git status` が `.git` をリポジトリとして認識できなかった。Git操作を始める前に、共有ドライブ側の `.git` の状態を確認する。
- MeetモードはGoogle側の画面変更やポリシーの影響を受けるため、まずsimulationモードで業務フローを確認する。
## 2026-08-25 待機モーション対応

- 仮想カメラの既定値を `512x288 / 30fps` に変更。
- `/data/idle-base.mp4` があれば音声全体へループし、発話中だけ口の動きを重ねる。
- 動画がない場合は静止画へ呼吸・揺れ・瞬きを加えるフォールバックを使用する。
- 30fpsの `virtual-camera.y4m` を生成し、Meetブラウザを再起動済み。
- Docker内の全20テストが成功。
- 詳細手順は `IDLE_AVATAR.md` を参照。
## 2026-08-25 リアルタイム対話モード

- 管理UIのMeet設定へ `従来モード` / `リアルタイム対話モード` の切替を追加。
- リアルタイム時は車番、氏名、各質問を回答認識後に順次進行する。
- Edge TTSをWeb Audio経由でMeetの送信マイクへ直接流す。
- リアルタイム映像は30fps待機モーションと発話時の口の動きを使用する。
- 認識失敗時は質問を進めず再回答を依頼する。
- Whisperの認識チャンクを5秒から3秒へ短縮。
- Docker内の全22テストが成功。
- UIのモード保存とヘルスAPI反映、TTS生成、録音ファイル更新を確認済み。
- 現在の選択モードは `legacy`（従来動作）。
- 詳細は `REALTIME_DIALOGUE.md` を参照。

## 2026-08-26 録音音声UI・14か月保持

- 点呼中に認識された回答音声を3秒単位のWAVとして保存する機能を追加。
- 点呼一覧に録音件数、点呼詳細に録音日時・長さ・容量・認識文・音声プレーヤーを追加。
- 録音は当月を含む14暦月保持し、毎月1日03:00（JST）に期限切れのDB情報とファイルを削除。
- 月次削除の最終処理日時と削除件数を管理UIに表示。
- Dockerを再構築して起動済み。全25テスト成功。
- 管理UI: http://localhost:8080
- 詳細仕様と確認手順: `RECORDINGS.md`
## 2026-08-26 リアルタイム映像・車番認識の品質改善

- リアルタイム映像の固定楕円リップシンクを廃止。
- TTSの実音量をWeb Audio Analyserで取得し、元画像の口周辺を滑らかに変形する方式へ変更。
- 呼吸に近い全体移動、微小ズーム、周期的な瞬きを追加。
- Whisperをbaseからsmallへ変更（モデル取得済み、約972MB）。
- beam_size/best_ofを5へ上げ、数字・車番向けinitial_promptとVAD調整を追加。
- 全角数字、漢数字、ひらがなの数字読み、ハイフン・ダッシュ・「の」の表記ゆれを正規化。
- Docker再構築・全25テスト成功・全サービス起動確認済み。
- 新しい映像ブリッジはMeetページの再読み込み後に有効になる。コンテナ再起動済み。
## 2026-08-26 GPU高品質リアルタイムモード

- 設定UIに `realtime_gpu`（GPU高品質リアルタイム／MuseTalk 1.5）を追加。
- `gpu-avatar-worker` を通常起動サービスとして追加。
- Edge TTS → MuseTalk GPU推論 → H.264/AAC MP4 → Meetカメラ・マイク再生を実装。
- 生成待機中は軽量待機モーションを継続し、生成失敗時は軽量方式へ自動フォールバック。
- 質問文SHA-256単位の生成映像キャッシュを実装。
- MuseTalk公式モデル取得済み。RTX 4070 Ti SUPER 16GBとCUDA 11.8をコンテナ内で確認。
- 実GPU生成確認済み: 512x288、25fps、H.264 + AAC。
- SQLite待機ロック問題を修正し、全ワーカー継続稼働を確認。
- 全26テスト成功。詳細は `GPU_REALTIME.md`。
## 2026-08-26 GPU映像再生パス修正

- GPUワーカーは `/meet-config/gpu-prompts`、Meetブラウザは `/config` を参照していたため、生成済み映像を再生できなかった。
- `meet-browser` に `meet-browser-config:/meet-config` を追加し、生成側と再生側の共有パスを一致させた。
- GPU映像再生成功時に `realtime_prompt_error` を空へ戻す処理を追加。
- 保留されていたGPU映像ID 12と、その後のID 14がMeetで正常再生された。
- 最終状態: GPU `ready`、生成ID=14、再生ID=14、GPU/再生エラーなし、参加者待機中。
- 全26テスト成功、全7サービス起動中。
## 2026-08-26 自然会話点呼モード

- `realtime_natural`を設定UIへ追加。現在の選択は変更せず`realtime_gpu`のまま。
- 車番・氏名履歴`driver_identities`と会社伝言`company_messages`を追加。
- 確認済み履歴なら「○○さん、お疲れ様です。点呼を始めます」と名前で挨拶。
- 未登録の車番は今回聞いた氏名を確認済みとして記憶し、次回から利用。
- 過去履歴は同一車番・同一氏名が完了点呼で2回以上一致した場合だけ確認済みにする。現時点の確認済み過去候補は0件。
- 最終質問後に会社への伝言を聞き、ローカルで整理・重要度分類・復唱して保存。
- 事故・故障・遅延・危険・緊急・体調等は要確認として記録。
- 伝言対応のためWhisper認識窓を3秒から5秒へ変更。
- 自然会話モードもMuseTalk GPU映像とキャッシュ、軽量フォールバックを利用。
- 全28テスト成功。詳細は`NATURAL_DIALOGUE.md`。
## 2026-08-26 車番・氏名マスターUI
- 管理URL: http://localhost:8080/driver-identities
- 車番・氏名の一覧、新規登録、更新、削除を実装。UI操作は verified=1 として自然会話点呼で利用。
- 削除は driver_identities のみで、点呼履歴は保持。
- 既存28テスト成功。一時DBで追加→更新→削除成功。app health healthy。

## 2026-08-26 Meet参加者ID併用
- checkins / driver_identities に meet_participant_id を安全に追加。
- Meet参加通知DOMから内部IDを取得し、取得不能時は display:<Meet表示名> を代替IDとして保存。
- 自然会話の氏名照合は車番＋Meet IDを併用。一致時は氏名採用、不一致時は氏名を再質問して誤認を防止。
- 車番・氏名マスターUIにMeet参加者ID欄を追加。
- 全30テスト成功、DB移行・UI表示確認済み。

## 2026-08-27 自然会話不成立の見直し
- 原因: Whisperが全状態で車番向けプロンプトを使用、会社伝言に単独の「はい」を確定、GPUプロンプト待ち中の音声混入、BOT自身（Meet名: 関東ロジ管理）の参加者誤登録。
- 修正: 状態別Whisper指示、会社伝言の妥当性確認と再質問、生成・再生中入力破棄、長大車番/会話文氏名拒否、Meet BOTアカウント名の分離設定。
- 誤った display:関東ロジ管理 の紐付け1件を解除（車番・氏名は保持）。
- operation_mode を realtime_natural に設定。全34テスト成功。関連サービス稼働。

## 2026-08-27 接続済み人数の開始漏れ修正
- 原因: participant_worker_last_count=2 のまま監視再起動し、現在人数も2だったため従来の count > last_count 条件に入らなかった。参加通知も再起動中に取り逃して開始不能。
- 修正: 現在人数が2以上、参加通知未処理、アクティブ点呼なしなら、前回人数に関係なく Meet participant として点呼開始。
- 全35テスト成功。現在は参加者数1で待機中。次の接続時に開始する状態。

## 2026-08-27 会社伝言の二段階確認
- 伝言を即時確定せず、仮登録→要約復唱→はい/いいえ確認に変更。
- はい: 確定・必要なら要確認フラグ・終了。いいえ: 仮伝言を削除し最初から聞き直し。判別不能: 復唱確認を再実行。
- 特にありません/いいえ（最初の伝言質問への回答）は伝言なしで明確に終了。
- awaiting_company_message_confirmation を録音・退出・UI・帳票のアクティブ状態へ追加。全36テスト成功。

## 2026-08-27 伝言認識精度・速度改善
- 実績: checkin 62で録音文字起こしが「程度ダンプの不良があります」となり、想定される「テールランプ」を誤認。復唱prompt 47はGPU rendering待ち。
- Whisper base→small、固定チャンク5秒→4秒、会社伝言向け運送語彙プロンプトを追加。
- 程度ダンプ/テールダンプ→テールランプ等の業務語彙補正を追加。
- 会社伝言質問・復唱・確認・終了は realtime_prompt_fast=1 とし、MuseTalk生成を待たずedge-tts音声で即時応答。通常質問はGPU映像を維持。
- 全37テスト成功。GPU worker構文確認、gpu_pipeline_state=ready、health ok。

## 2026-08-27 点呼ループ修正
- 原因: 点呼完了後もMeet参加人数が2人のままだと、人数フォールバックが新規点呼を再作成していた。
- 対応: `participant_worker_presence_started` を追加し、同一の連続参加中は点呼を1回だけ開始するよう修正。
- ロック解除: Meet参加人数が1人以下に戻った時のみ解除。次の入室で再び開始できる。
- 検証: 全37テスト成功。app healthy、meet-browser稼働中。現在は参加人数1、ロック解除済み、待機状態。
## 2026-08-27 会社伝言質問の映像品質修正
- 原因: `awaiting_company_message` を速度優先扱いにしていたため、MuseTalk映像を迂回し、簡易的な口元引き伸ばし表示になっていた。
- 対応: 固定質問「最後に、会社へ伝えておきたいことはありますか…」はGPU高品質映像を使用。毎回内容が変わる伝言復唱だけ高速経路を維持。
- 固定質問のGPUキャッシュ `77cd4c399da8dd59daa406af.mp4` を確認済みのため、通常は再生成待ちなし。
- 検証: 全37テスト成功。app healthy、meet-browser・gpu-avatar-worker稼働中。
## 2026-08-27 複数参加者の順番点呼
- `participant_queue` テーブルを追加。点呼中に入室した参加者を氏名・Meet参加者ID付きで待ち行列へ保存する。
- 現在の点呼対象は完了まで固定。追加参加者の回答を現在対象の回答として扱わないよう、対象者名を呼び「ほかの方は順番に待機」と案内する。
- 点呼完了時は待ち行列の先頭を自動で新規checkinにし、「続いて、○○さん」と呼んで車番質問から開始する。
- Meet参加通知を取得できない場合も、参加人数増加分を匿名参加者として順番待ちへ登録する。
- 全39テスト成功。app healthy、meet-browser・response-worker稼働、DBロックなし。
- GPUワーカーはDBロック原因として一時停止中。自然会話点呼は音声フォールバック。旧meet-botとreport-mailerも競合回避のため停止中。
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

## 2026-09-01 2台目PCセットアップ引継ぎ
- フォルダ全体コピーによる2台目構築手順をSECOND_PC_HANDOFF.mdへ記録。
- ノードID変更、NAS、Meetログイン、同期、主系切替試験、保守、障害対応、完了チェックを記載。

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

- 2026-09-02: クラスタ同期 v4。主系の点呼シナリオ、選択中シナリオ、Meet/BOT/動作モードの安全設定をNAS共有。秘密情報は対象外。管理UIに設定監査を追加。
