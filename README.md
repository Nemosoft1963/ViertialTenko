# 仮想点呼システム

Google Meet に参加した運転者を対象に、参加時刻・車番・氏名・点呼質問と回答を記録する Docker アプリです。

## 起動

```powershell
docker compose up -d --build
```

管理画面: http://localhost:8080

初回起動時に「標準点呼」シナリオ（免許証、体調確認）が自動作成されます。設定画面で Meet URL、表示名、使用シナリオを登録してください。

## 動作モード

- `simulation`（初期値）: 管理画面から参加者を入力し、点呼・記録を一通り確認できます。
- `meet`: Playwright ワーカーが Google Meet の会議室を監視します。Google アカウント認証、Meet の入室許可、マイク/スピーカー用の仮想オーディオ設定が必要です。

## 点呼BOTのアニメーション

仮想カメラ映像は、点呼音声から事前生成します。BOT画像に次の動きを加えます。

- 音声波形に同期した口の開閉
- 自然なまばたき
- 背景を動かさない控えめな口元・まばたきの動作
- 回答待ち中の待機動作

シナリオ音声を再生成すると、リップシンク映像も同時に再生成されます。

```powershell
docker compose exec -T app python -m app.build_meet_scenario_audio
```

生成映像は `512x288 / 15fps` の Y4M ファイルとして Meet の仮想カメラへ渡されます。参加者を検出してMeetページを再接続すると、音声と映像が同時に先頭から開始します。


## MuseTalk 1.5 AIリップシンク

描画式より自然な口元にする場合は、GPU対応のMuseTalkレンダラーをオンデマンドで使用します。モデルと生成物はDockerボリュームに保存され、通常の `docker compose up` ではGPUサービスを起動しません。

初回のみ、レンダラーの構築と公式モデルの取得を実行します。

```powershell
docker compose --profile render build musetalk-renderer
docker compose --profile render run --rm musetalk-renderer /usr/local/bin/download-musetalk-models
```

点呼音声からAIリップシンク映像を生成します。

```powershell
docker compose --profile render run --rm musetalk-renderer /usr/local/bin/render-tenko-avatar
```

入力は `/data/idle-base.mp4` を優先し、存在しない場合は `/data/virtual-checkin-operator.png` の静止画を使用します。自然な頭部・姿勢の動きも使う場合は、正面を向いた無音の待機動画を登録してから再生成します。

```powershell
docker compose cp .\idle-base.mp4 app:/data/idle-base.mp4
docker compose --profile render run --rm musetalk-renderer /usr/local/bin/render-tenko-avatar
```

本システムは運行管理者の判断を支援するもので、法令上必要な点呼や本人確認を自動的に代替するものではありません。異常回答は必ず管理者が確認してください。

## API

- `GET /api/health`
- `GET/POST /api/scenarios`
- `POST /api/checkins/start`
- `POST /api/checkins/{id}/answer`
- `POST /api/checkins/{id}/complete`
- `GET /api/checkins`

## Meet 運用上の注意

Google Meet には一般的な「参加者を自動操作する公式Bot API」はありません。そのため Meet 参加部分はブラウザ自動操作で、Google 側の画面変更や組織ポリシーの影響を受けます。まず simulation で業務フローを確定し、専用Googleアカウントとテスト会議室で `meet` モードを検証してください。

## 参加者退出時の処理

Google MeetでBOT以外の参加者が0人になった状態を5秒間継続して検知すると、進行中の点呼を自動的に中断します。

## 本人・車両確認

参加者を検出すると、通常の点呼質問より前に次の順番で音声確認します。

1. 車番
2. 氏名（フルネーム）


## 日次・月次点呼CSVメール

管理画面の「日次・月次CSVメール」で次を設定できます。

- SMTPサーバ、ポート、ログインユーザー、パスワード
- 送信元・送信先メールアドレス
- 毎日1:00（日本時間）の自動送信
- 毎月1日2:00（日本時間）の前月分自動送信
- 日付を指定した手動送信
- 月を指定した月次CSVの手動送信

CSVには点呼日時、車番、氏名、シナリオ、状態、質問、回答、判定を出力します。
自動送信は前日の0:00から23:59までに開始した点呼を対象とし、同じ対象日の
二重送信を防止します。月次送信は前月1日0:00から当月1日0:00直前までに
開始した点呼を1ファイルへまとめ、同じ対象月の二重送信を防止します。
パスワードは管理画面へ再表示されず、パスワード欄を
空欄のまま保存すると現在の値を維持します。

日次・月次送信ワーカーはDocker Composeの `report-mailer` サービスとして常時稼働します。
車番と氏名を記録した後、免許証・体調確認など選択中のシナリオを開始します。各回答の標準待ち時間は5秒です。

- 点呼状態を `cancelled`、要確認を有効、完了時刻を退出検知時刻として記録
- 回答・イベント履歴へ「参加者退出」と退出理由を記録
- 参加者監視と回答ワーカーを `waiting_for_participant` へリセット
- 退出後の音声を中断済み点呼へ追加しない
- 次の新規参加者を通常どおり受付
