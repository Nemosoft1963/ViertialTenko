# VirtualTenko（仮想点呼システム）

Google Meetへ参加した運転者に対し、車番・氏名・点呼項目を音声で確認し、回答、録音、会社への伝達事項を記録するセルフホスト型システムです。GPU常駐の日本語音声認識、自然会話支援、デジタルヒューマン表示、主系・待機系の協調運用を備えています。

> 本システムは運行管理者の判断を支援するもので、法令上必要な点呼、本人確認、酒気帯び確認を自動的に代替するものではありません。異常回答や認識結果は必ず管理者が確認してください。

## 主な機能

- Google Meet参加者の検出と順次点呼
- 車番、氏名、Meet参加ID、過去履歴を組み合わせた本人候補照合
- 日本語Whisperによる音声認識と物流用語補正
- 固定進行、リアルタイム、GPU、自然会話、3D、Open WebUIモード
- 会社への伝達事項の整理、復唱、記録
- 複数参加者を対象者ごとに固定して順番に処理
- 録音確認UI、14か月保持、月単位削除
- 日次・月次CSVメール
- 主系・待機系、NAS同期、ハートビート、実行ノード優先
- ヘルス監視と監査ログ

詳細な構成と自立運用機能は [SYSTEM_INTRODUCTION.md](SYSTEM_INTRODUCTION.md) を参照してください。

運用前に確認する資料:

- [本番導入チェックリスト](docs/DEPLOYMENT_CHECKLIST.md)
- [データとプライバシー](docs/DATA_AND_PRIVACY.md)
- [セキュリティポリシー](SECURITY.md)
- [開発参加手順](CONTRIBUTING.md)
- [変更履歴](CHANGELOG.md)

## システム構成

```text
Google Meet
  ├─ Meetブラウザ / 仮想カメラ / 仮想マイク
  ├─ 参加者監視
  └─ 回答音声
       ↓
GPU常駐 Whisper（日本語・物流用語）
       ↓
点呼状態機械 ── Open WebUI / Ollama（自然な文面のみ）
       ↓
SQLite・録音・管理UI
       ↓
NAS共有領域 ⇄ 主系 / 待機系
```

LLMは点呼の質問順、回答確定、DB更新を直接制御しません。失敗時は決定論的な既存文面へフォールバックします。

## 必要環境

現在のDocker Compose構成はNVIDIA GPUを使用します。

- Windows 11またはDocker Desktopが動作するWindows環境
- Docker Desktop（WSL2バックエンド）
- Docker Compose v2
- NVIDIA GPU、対応ドライバー、Dockerから利用可能なCUDA
- 主系の目安: RTX 4070 Ti SUPER 16GB
- 待機系の目安: RTX 4060 Ti 8GB
- Google Meet用の専用Googleアカウント
- 主系・待機系運用時は両PCから接続できるNAS共有
- 初回イメージ取得、Edge TTS、Google Meetを使うためのネットワーク

Open WebUIを使わない基本モードでも、現行ComposeではGPU音声認識サービスが起動します。

## 初期構築

### 1. 取得

```powershell
git clone --recurse-submodules https://github.com/Nemosoft1963/ViertialTenko.git
cd ViertialTenko
Copy-Item .env.example .env
```

すでに通常の `git clone` を行った場合:

```powershell
git submodule update --init --recursive
```

### 2. 環境設定

`.env` を編集します。実際のパスワード、APIキー、NAS認証情報をGitへ登録しないでください。

最低限確認する値:

- `APP_MODE`: `simulation` または `meet`
- `MEET_URL`: 使用するGoogle Meet URL
- `ADMIN_TOKEN`: 推測困難なランダム値
- `CHROME_GUI_PASSWORD`: Meetブラウザ画面用のランダム値
- `CLUSTER_NODE_ID`: PCごとに一意なID
- `CLUSTER_NODE_NAME`: 管理画面に表示するPC名
- `CLUSTER_NODE_PRIORITY`: 主系100、待機系50を目安
- `NAS_CIFS_USERNAME` / `NAS_CIFS_PASSWORD`: NAS共有の接続情報
- `LOCAL_LLM_MODEL`: 主系 `qwen3:8b`、8GB待機系 `qwen3:4b`
- `WHISPER_COMPUTE_TYPE`: 主系 `float16`、8GB待機系 `int8_float16`

PowerShellでランダム値を生成する例:

```powershell
[guid]::NewGuid().ToString("N")
```

NASを使わない単体評価では、`docker-compose.yml` の `cluster-shared` ボリュームをローカルボリュームへ変更する必要があります。既定構成はCIFS NASを前提としています。

### 3. 基本サービス起動

```powershell
docker compose up -d --build
docker compose ps
```

管理画面:

- 管理UI: http://localhost:8080
- Meet画面プロキシ: http://localhost:3002
- Meetブラウザ直接画面: http://localhost:3001

初回起動時に標準点呼シナリオが作成されます。管理UIでMeet URL、BOT表示名、使用シナリオ、動作モードを設定してください。

### 4. Open WebUI自然会話モード

```powershell
docker compose --profile openwebui up -d --build
docker compose exec ollama ollama pull qwen3:8b
```

待機系8GBでは最後のモデル名を `qwen3:4b` にします。

Open WebUI: http://localhost:3000

初回管理者を作成し、APIキー機能から専用キーを発行して `.env` の `OPENWEBUI_API_KEY` に設定します。キーや管理者パスワードをソース、共有資料、スクリーンショットへ記録しないでください。

詳細: [OPENWEBUI_NATURAL_DIALOGUE.md](OPENWEBUI_NATURAL_DIALOGUE.md)

## 主系・待機系

二台は同じソース構成を使い、PC固有値だけを `.env` で分けます。

| 項目 | 主系 | 待機系 |
|---|---|---|
| `CLUSTER_NODE_ID` | `primary-pc` | `backup-pc` |
| `CLUSTER_NODE_PRIORITY` | `100` | `50` |
| GPU例 | 4070 Ti SUPER 16GB | 4060 Ti 8GB |
| LLM | `qwen3:8b` | `qwen3:4b` |
| Whisper計算型 | `float16` | `int8_float16` |

現在点呼を実行しているノードを主系として扱い、設定された優先ノードは待機時の選択に利用します。NAS共有領域を介して車番・氏名、点呼シナリオ、設定、点呼結果を同期します。モデルと推論キャッシュは各PCのローカルDockerボリュームへ保存してください。

- 構築手順: [CLUSTER_SETUP.md](CLUSTER_SETUP.md)
- 2台目引継ぎ: [SECOND_PC_HANDOFF.md](SECOND_PC_HANDOFF.md)
- 同期監査: [STANDBY_SYNC_AUDIT_CHANGE_INSTRUCTION.md](STANDBY_SYNC_AUDIT_CHANGE_INSTRUCTION.md)

## 動作モード

管理UIから切り替えます。

- `legacy`: 固定タイムライン
- `realtime`: 回答認識後に次の質問を生成
- `realtime_gpu`: GPU映像推論
- `realtime_natural`: 氏名呼びかけと自然会話
- `realtime_3d`: 軽量3D人物
- `realtime_metahuman`: 外部MetaHuman連携用の実験モード
- `realtime_openwebui`: ローカルLLMによる自然化

関連資料:

- [REALTIME_DIALOGUE.md](REALTIME_DIALOGUE.md)
- [NATURAL_DIALOGUE.md](NATURAL_DIALOGUE.md)
- [DIGITAL_HUMAN_3D.md](DIGITAL_HUMAN_3D.md)
- [GPU_REALTIME.md](GPU_REALTIME.md)

## 録音とデータ保持

録音は管理UIから確認できます。既定方針は14か月保持し、削除は月単位で実施します。録音、SQLite DB、DockerボリュームはGit管理対象外です。

詳細: [RECORDINGS.md](RECORDINGS.md)

## MuseTalk 1.5

より自然な口元を生成する任意機能です。モデルと生成物はDockerボリュームに保存します。

```powershell
docker compose --profile render build musetalk-renderer
docker compose --profile render run --rm musetalk-renderer /usr/local/bin/download-musetalk-models
docker compose --profile render run --rm musetalk-renderer /usr/local/bin/render-tenko-avatar
```

モデルライセンス、肖像権、生成物の利用条件を導入者側で確認してください。

## API

- `GET /api/health`
- `GET /api/health-monitor`
- `GET /api/cluster`
- `GET/POST /api/scenarios`
- `POST /api/checkins/start`
- `POST /api/checkins/{id}/answer`
- `POST /api/checkins/{id}/complete`
- `GET /api/checkins`
- `GET /api/worker-status`

## テスト

```powershell
python -m pip install -r requirements.txt pytest
python -m pytest -q
```

Meetの実地テストにはGoogleログイン、BOT入室許可、別端末の参加者が必要です。

## 停止

```powershell
docker compose down
```

この操作では名前付きDockerボリュームは削除されません。`docker compose down -v` はDB、モデル、ブラウザ状態などを削除するため、バックアップなしで実行しないでください。

## セキュリティと公開時の注意

- `.env`、録音、DB、Dockerボリューム、Googleプロファイルをコミットしない
- 管理UIをインターネットへ直接公開しない
- NAS認証情報をソースやComposeファイルへ直書きしない
- Open WebUIとMeetブラウザのポートは原則localhostまたは信頼できるLAN内に限定する
- 録音と点呼結果は個人情報としてアクセス制御、保存期間、削除記録を管理する
- 公開前に `git status` とシークレットスキャンを実施する

脆弱性や認証情報の誤公開を発見した場合は、公開Issueへ秘密情報を書かず、リポジトリ所有者へ非公開経路で連絡してください。

## 既知の制約

- Google Meetに一般用途の参加者自動操作APIはなく、ブラウザ画面変更や組織ポリシーの影響を受けます。
- 音声認識は環境音、マイク、話し方、通信品質で変動します。
- GPUメモリ不足時はLLMよりWhisperを優先してください。
- MetaHumanとLiveKitは検討・試験資料を含みますが、標準運用には含まれません。
- Windowsホスト監視コードは保存されていますが、自動タスクは既定で登録されません。
- MuseTalkは公式リポジトリをGitサブモジュールとして固定し、静止画入力用の小さな互換パッチをDockerビルド時に適用します。

## ライセンス

本リポジトリ独自コードの利用許諾条件は現時点で明示されていません。第三者ライブラリ、モデル、音声、画像にはそれぞれのライセンスが適用されます。利用・再配布前に権利者の条件を確認してください。

公開ライセンスを設定する場合は、著作権者が利用、改変、再配布、商用利用、保証、特許条項を確認したうえで別途 `LICENSE` を追加してください。
