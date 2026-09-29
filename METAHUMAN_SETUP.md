# MetaHumanモード導入・引継ぎ（2026-09-10）

## 現在の到達点

**MetaHuman本体の導入は未完了。実装済みなのは外部描画との接続部分です。**
標準インストール先、Unreal登録レジストリ、プロジェクト内ではUnreal Editor／既存の .uproject を確認できませんでした。
人物アセット・Unrealシーンは含まれません。実際のMetaHumanの見た目、Live Link、日本語の口の動き、FPS、遅延は未検証です。
既存 realtime_3d と口の倍率は変更していません。現在の運用モードも自動変更しません。

実装済み：

- `realtime_metahuman` 別モード。氏名挨拶・会社伝言確認など既存自然会話を利用。MuseTalk動画生成を待ちません。
- Windows上の外部描画を仮想カメラから取得し、認証付きHTTPブリッジ経由でMeetの映像トラックに接続。
- TTSをPCM16モノラルWAVに変換しWindows音声デバイスへ送信。同じ音声をMeetへ送信。
- 映像は1280×720。取得・描画は最大30fpsを目標とした簡易JPEG転送。実効FPS保証なし。
- 管理UIから接続確認。未接続時のモード保存を拒否し既存設定を保持。
- 映像断では未接続表示。従来写真へ黙って切り替えません。新しい発話も映像未準備なら失敗として記録。
- トークンはサーバ側のみ。ブラウザへ渡さず、リダイレクト・環境プロキシにも転送しません。

未完了：

- Epic Games／Unreal／MetaHumanの導入、人物の制作・アセンブル、Live Linkの設定。
- 自然な待機・うなずき・視線などUnreal内のアニメーション。会話状態に応じた身体ジェスチャーの接続。
- キャプチャ経路と音声ループバックの実機確認、リップシンク遅延調整、長時間点呼試験。
- Unreal停止の自動検出・自動再起動。ブリッジの接続済み表示はUnrealプロセスの健全性保証ではありません。

## 構成

点呼TTS → Windowsブリッジ → 仮想音声出力 → UnrealのMetaHuman Audio Live Link → 顔アニメーション

Unreal人物画面 → OBS仮想カメラ → Windowsブリッジ → Meetブラウザ映像

点呼TTS → 調整可能な音声遅延 → Meetブラウザ音声

音声から顔を動かすのはEpicのMetaHumanです。本ブリッジ自体は画像生成・顔推論をしません。

## Windows側の準備（各PCで個別に必要）

1. Epic Games LauncherからUE 5.6以降の対応版を導入。Epicアカウント・利用条件の確認は利用者が行います。
2. MetaHumanを作成・アセンブルし正面のカメラ、照明、背景を設定。まず一人・上半身に限定してください。
3. MetaHuman Live Linkを有効化し、MetaHuman Audioソースを作成。人物の顔に該当Live Link Subjectを割り当てます。
4. 音声ループバックデバイスを用意します。例：VB-CABLEの再生側 `CABLE Input` に本ブリッジを出力し、Unrealは録音側 `CABLE Output` を入力にします。**Meetで受信した参加者音声を混ぜないこと。**
5. OBSでUnrealの人物画面だけを取り込み仮想カメラを開始。デスクトップ全体や物理カメラを選ばないでください。
6. Python仮想環境を各PCのローカルディスクに作成し、下記requirementsを導入します。NAS上のvenvはPC間で共有しません。

```powershell
python -m venv C:\TenkoMetaHuman\venv
C:\TenkoMetaHuman\venv\Scripts\python.exe -m pip install -r .\metahuman\requirements.txt
C:\TenkoMetaHuman\venv\Scripts\python.exe -m sounddevice
```

一覧から音声の**再生側デバイス番号**を確認。OpenCVのカメラ番号は環境で変わるためOBS仮想カメラを実際にプレビューして確認してください。
ブリッジは確認フラグなしには起動しませんが、画像内容を自動識別できません。

## ブリッジの起動と接続

各PCごとにランダムな32文字以上の `METAHUMAN_BRIDGE_TOKEN` を作成し、Windows起動環境とそのPCの `.env` に同じ値を設定します。
トークンは本資料・共有ZIP・ログへ記録しないでください。

```powershell
# トークンは実際の値を環境変数として設定してから実行。
# カメラ番号と音声番号は例ではなく実機で確認した番号に置換。
C:\TenkoMetaHuman\venv\Scripts\python.exe .\metahuman\host_bridge.py --camera-index <OBSカメラ番号> --audio-device <再生番号> --confirm-metahuman-source --bind 0.0.0.0
```

標準bindは127.0.0.1です。Dockerから接続する場合、Windowsの必要なインターフェイスでlistenし、ファイアウォールでDockerホスト経路のみに制限してください。
TCP8765をインターネットへ公開しないでください。HTTPは暗号化しません。別PCを跨ぐ転送にはTLSプロキシなど追加保護が必要です。
本構成は各PC内で完結し、動的IPを設定値に持たせない構成を推奨します。

各PCの `.env`（値はNAS同期対象にしない）：

```dotenv
METAHUMAN_BRIDGE_URL=http://host.docker.internal:8765
METAHUMAN_BRIDGE_TOKEN=<各PC固有のランダムな32文字以上の値>
METAHUMAN_AUDIO_DELAY_MS=200
```

Meet側の音声を0〜2000ms遅らせられます。200msは仮値です。Live Link・OBS・JPEG転送の実測に合わせて調整します。
現状は音声と映像の厳密なタイムスタンプ同期ではありません。品質確認前に本番へ切り替えないでください。
ブリッジは音声をファイル保存せずメモリ上で再生します。既存点呼録音の保持設定は変更しません。

## Docker反映

環境変数変更にはコンテナ再作成が必要です。既存の点呼が完了し、対象ノードを保守状態にして実施します。
NAS同期中にSQLiteロックが発生しやすいため、既知の起動順を使用します。

```powershell
docker compose build app meet-browser meet-bot
docker compose stop cluster-coordinator meet-browser meet-bot response-worker report-mailer gpu-avatar-worker
docker compose up -d --no-deps app
# http://localhost:8080/api/health の応答を確認してから次へ
docker compose up -d --no-deps meet-browser meet-bot
docker compose start cluster-coordinator response-worker report-mailer gpu-avatar-worker
```

管理UIの `/metahuman` で接続確認 → UnrealとOBSで本物の人物映像・口の追従を確認 → 管理UIでMetaHuman保存 → Meetを再接続。
未接続で保存すると409となり、ほかの入力も含め設定変更しません。
接続URL／トークンを変更した場合はappとMeetコンテナの両方へ反映すること。

## 主系・待機系

- 両PCへコードを配布し、各々にUnreal／人物アセット／音声デバイス／OBS／ブリッジを用意します。
- 人物アセットはライセンスに従って共有できますが、ハードウェア番号・トークン・venvは共有しません。
- 待機系RTX4060 Ti 8GBの性能は未測定。まず低いLOD、軽い髪、簡素な照明、720pで測定してください。30fpsを保証しません。
- 自動フェイルオーバー前に両方で独立して通話試験。片側だけ導入済みのまま両系をMetaHuman運用にしないでください。
- この改修で主系／待機系の優先度・保守状態・リーダーを変更しません。

## 試験・既知の制限

自動試験はHTTP認証、映像断、WAV検証、未接続保存拒否、自然会話モード判定、実Chromium内の映像トラック／音声経路を対象とします。
テスト映像は人工的な色画像でありMetaHumanの品質試験ではありません。
JPEG転送は初期接続向け。高負荷・遅延が許容できなければPixel Streaming等への置換を別途検討してください。
固定カメラでも正常フレームとして届けば接続済みです。Unreal停止でOBSが最後の画面を出し続けるケースは自動判定できません。
発話開始後の映像断は音声を途中停止しません。異常時は管理UIで点呼を停止し、接続復旧後に再開してください。

## 公式参考

- [MetaHumanリアルタイムアニメーション](https://dev.epicgames.com/documentation/metahuman/realtime-animation-for-metahumans-in-unreal-engine?lang=en-US)
- [MetaHuman Audio Source](https://dev.epicgames.com/documentation/en-us/metahuman/using-a-metahuman-audio-source)
- [ハードウェア要件](https://dev.epicgames.com/documentation/metahuman/metahuman-hardware-requirements-in-unreal-engine?lang=en-US)
