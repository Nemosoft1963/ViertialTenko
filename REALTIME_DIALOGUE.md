# リアルタイム対話モード

## モード切替

管理UI `http://localhost:8080` の「Meet設定」から選択する。

- 従来モード（固定タイムライン）: 事前生成した音声・映像を既定の待ち時間で再生する。
- リアルタイム対話モード: 参加者の回答を認識してから次の質問を合成・送信する。

保存後、Meetブラウザは必要に応じて一度だけページを再読み込みし、選択した音声・映像経路へ切り替える。

## リアルタイム対話の流れ

1. Meet参加者を検出する。
2. 開始案内と車番確認をEdge TTSで生成してMeetのマイクトラックへ送る。
3. Whisperで車番を認識後、氏名確認を送る。
4. 氏名を認識後、選択中のシナリオの最初の質問を送る。
5. 「はい」「いいえ」を認識・記録してから次の質問へ進む。
6. 認識できない場合は再回答を依頼し、記録や質問番号を進めない。
7. 全質問完了後、終了案内を送って点呼を完了する。

## 映像・音声

リアルタイムモードではWeb AudioでTTSをMeetの送信マイクへ直接流す。ローカルスピーカーへは流さないため、回答認識ワーカーがBOT自身の声を拾いにくい構成。

映像は仮想カメラの先頭フレームを基に30fpsの待機モーションを描画し、TTS再生中だけ口の動きを重ねる。

## 状態と設定

主なDB設定キー:

- `operation_mode`: `legacy` または `realtime`
- `realtime_prompt_id`: 最新プロンプト番号
- `realtime_prompt_played_id`: 再生済み番号
- `realtime_prompt_state`: `queued` / `synthesizing` / `played` / `error`
- `realtime_prompt_error`: 直近の音声送信エラー

回答認識は既定で3秒単位。`response-worker` の環境変数 `WHISPER_CHUNK_SECONDS` で調整可能。
音声は `REALTIME_TTS_VOICE` で変更でき、既定値は `ja-JP-NanamiNeural`。

## 注意

Edge TTSの音声合成には外部ネットワーク接続が必要。通信できない場合は `realtime_prompt_state=error` と `realtime_prompt_error` に記録される。
実際のMeet往復確認には、Meet URL、Googleログイン、BOT入室許可、別端末の参加者が必要。
