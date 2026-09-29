# 2台冗長化・NAS連携セットアップ

## 構成

- 2台のPCは動的IPのままでよい。ノード間通信にIPアドレスは使用しない。
- NASを第三の判定点として利用し、各PCは5秒周期で生存情報を記録する。
- 優先主系が45秒以上更新されない場合、待機系が自動的に主系となる。
- NASへ接続できないPCは二重点呼防止のため点呼を停止する。
- 完了点呼、回答、会社伝言はNAS経由で双方向同期し、発生ノードIDと点呼IDで重複を防止する。

## 1台目（現在のPC）

    CLUSTER_NODE_ID=primary-pc
    CLUSTER_NODE_NAME=Primary-PC
    CLUSTER_NODE_PRIORITY=100

## 2台目

プロジェクトをコピーした後、.envのクラスタ部分を次に変更する。NAS認証値は1台目と同じ値を設定する。

    CLUSTER_NODE_ID=backup-pc
    CLUSTER_NODE_NAME=Backup-PC
    CLUSTER_NODE_PRIORITY=50
    NAS_CIFS_USERNAME=<NASユーザー>
    NAS_CIFS_PASSWORD=<NASパスワード>

同じCLUSTER_NODE_IDを2台で使用してはいけない。設定後に次を実行する。

    docker compose up -d --build

各PCのChrome画面でMeetへログインし、カメラ・マイク許可を済ませておく。待機系では点呼処理だけが停止し、Chromeと監視処理は起動したままになる。

## 制御画面

- URL: http://<各PCのアドレス>:8080/cluster-control
- 現在の主系、NAS接続、ノードのオンライン状態、最終通信、同期件数を表示する。
- 「優先主系を変更」でオンラインのPCへ主系を切り替えられる。
- 「待機・保守状態」でそのPCを手動でクラスタ対象外にできる。

## 障害時

- 主系PC停止: 最大約45秒で待機PCへ切り替わる。
- NAS停止・NAS通信断: 二重点呼防止のため両PCとも点呼を安全停止する。
- NAS復旧: ハートビート確認後、自動的に主系を再選出する。
- 優先主系復旧: 選択された優先主系がオンラインになるとそちらへ戻る。

## 同期対象

点呼本体、回答、警告状態、会社伝言を同期する。録音音声ファイル自体は14か月保持のローカル保存のままであり、NASへ複製しない。