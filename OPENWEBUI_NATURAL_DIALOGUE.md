# Open WebUI自然会話モード

## 実装範囲

- 動作モード `realtime_openwebui`
- Open WebUI APIによる発話文の自然化
- 1.8秒タイムアウトと既存文面への自動フォールバック
- Qwenへの点呼状態変更・DB更新権限は与えない
- faster-whisper smallのCUDA常駐、日本語固定、状態別物流hotwords
- 録音WAV生成をSQLiteトランザクション外へ分離
- Open WebUI・Ollamaは `openwebui` Composeプロファイルで分離

## 系統別設定

主系 RTX 4070 Ti SUPER 16GB:

```env
LOCAL_LLM_MODEL=qwen3:8b
WHISPER_COMPUTE_TYPE=float16
```

待機系 RTX 4060 Ti 8GB:

```env
LOCAL_LLM_MODEL=qwen3:4b
WHISPER_COMPUTE_TYPE=int8_float16
```

両系統ともWhisperは `small`、`cuda`、日本語固定、ワーカー数1で動作する。

## 初期構築

1. `docker compose --profile openwebui up -d ollama open-webui`
2. 主系は `docker compose exec ollama ollama pull qwen3:8b`、待機系は `qwen3:4b` を取得する。
3. `http://localhost:3000` でOpen WebUI管理者を作成する。
4. APIキー機能を有効にして専用キーを発行し、`.env` の `OPENWEBUI_API_KEY` に設定する。
5. `docker compose --profile openwebui up -d --build` で反映する。
6. 管理UIの接続表示が「接続済み」になってからOpen WebUI自然会話モードを選ぶ。

APIキーや管理者パスワードはNAS共有資料、ソース、DB設定には保存しない。

## 障害時

Open WebUI未設定、タイムアウト、HTTPエラー、不正JSON、数字改変を検出した場合、LLM応答を破棄して既存の決定論的な点呼文面をそのまま再生する。点呼の質問順、終了条件、参加者切替は従来の状態機械が管理する。

## GPU競合

Open WebUI自然会話モードはMuseTalkを使用しない。待機系8GBではQwen3 4BとWhisper smallを優先し、GPU映像生成を同時実行しない。モデルは各PCのDockerボリュームへ保存し、NASから直接ロードしない。
