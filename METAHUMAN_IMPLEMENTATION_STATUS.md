# MetaHuman実装状況（2026-09-10）

## 引継ぎ要約

**全体は未完了。外部MetaHuman映像／音声接続モードの実装と管理UI反映まで完了。**
Unreal Engine／MetaHuman人物アセットの導入・作成・描画試験は未実施です。
標準インストール先、Unreal登録レジストリ、PATH、プロジェクト内で既存Unreal環境を確認できませんでした。
別ドライブ等に既存環境がある場合は、その場所を確認してから追加導入してください。

## 変更ファイル

- `app/metahuman_bridge.py`：認証付きホストブリッジのクライアント。タイムアウト・サイズ制限・リダイレクト拒否。
- `app/metahuman.js`：外部映像トラック、切断表示、PCM16モノラルWAV変換。
- `app/realtime_browser.py`：Meet映像／音声への接続。ブラウザバインディングはMeetのHTTPSオリジン限定。
- `app/realtime_dialogue.py`：新モードを自然会話として扱う。MuseTalkは利用しない。
- `app/main.py`、`app/templates/index.html`：接続確認ページとAPI、モード選択、未接続保存拒否。
- `metahuman/host_bridge.py`、`metahuman/requirements.txt`：Windows仮想カメラ取得・指定音声デバイスへの出力。Unrealレンダラー本体ではない。
- `Dockerfile`、`Dockerfile.chrome`、`docker-compose.yml`：新しいモジュール・環境変数の配布。
- `tests/test_metahuman.py`、`tests/test_metahuman_browser.py`：回帰テスト。
- `METAHUMAN_SETUP.md`：導入手順・安全な公開範囲・既知の制限。

## 確認結果

- 新規8件を含む全62件のunittest成功（新しいDockerイメージ内、実DBをマウントせず実行）。
- 実Chromiumで外部テスト映像の1280×720トラック・WAV経路・切断拒否を確認。
- 稼働中の `/metahuman` が表示され、未設定／未接続を表示。
- 稼働中の管理UIに新モードが1件表示され、選択中は従来どおり `realtime`。
- `/api/health` は `ok: true`、環境モードは `simulation`。
- アプリ・Meet関連を再起動し、対象Dockerサービス9つの起動を確認。
- `.env`、現在の動作モード、既存3Dの見た目／口倍率、主待機の優先度は変更していない。
- 待機系への直接配備、MetaHuman実機試験は実施していない。

## 再開時の順序

1. Epicアカウントを使用してUnreal／MetaHumanを準備（別のインストール先があれば先に確認）。
2. 人物アセットと正面シーンを用意。Live Link Audioソース・音声ループバック・OBSを設定。
3. `METAHUMAN_SETUP.md` に従って各PCのホストブリッジと環境変数を設定。
4. 実際の人物映像、最初の発話、連続発話、切断、再接続、日本語口形、音声遅延を確認。
5. 待機系4060 Ti 8GBで性能と独立動作を測定し、双方の準備完了後にモード変更。

アプリ起動にはSQLiteロックの既知問題があります。再反映時は導入資料に記載の起動順を守ってください。
