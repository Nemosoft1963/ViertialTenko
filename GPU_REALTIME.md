# GPU高品質リアルタイムモード

## 選択方法

管理UI `http://localhost:8080` の「Meet設定」→「動作モード」から「GPU高品質リアルタイム（MuseTalk 1.5）」を選び、保存します。

## 動作

1. 回答を認識すると次の質問をキューへ登録します。
2. `gpu-avatar-worker` がEdge TTS音声を生成します。
3. RTX GPU上のMuseTalk 1.5が、オペレーター画像と音声からリップシンク映像を生成します。
4. Meetブラウザが生成済みMP4をカメラ映像とマイク音声へ同時に送ります。
5. 同じ質問文はキャッシュ済み映像を再利用します。
6. GPU生成失敗時は軽量リアルタイム音声・映像へフォールバックします。

生成待ちの間は従来の待機モーションを表示します。初めての文章は顔検出・モデル読込・推論があるため数十秒程度かかる場合があります。同じ文章の2回目以降はキャッシュを使用します。

## 環境

- GPU: NVIDIA GeForce RTX 4070 Ti SUPER 16GB
- CUDA: 11.8
- MuseTalk: 1.5
- 出力: H.264 + AAC MP4、512x288、25fps
- モデルはDockerボリューム `musetalk-models` に保存済みです。

## 状態確認

管理UI右下の「GPU映像」で状態を確認できます。

- `standby`: GPUモード以外で待機
- `loading_models`: モデル準備中
- `rendering`: 映像生成中
- `ready`: 再生準備完了
- `error`: GPU生成失敗（軽量方式へフォールバック）

```powershell
docker compose ps
docker compose logs --tail=100 gpu-avatar-worker
```