# 軽量3Dデジタルヒューマン

元オペレーター画像を浅い立体メッシュへ投影してThree.js/WebGLで描画する。
正面の自然さを優先した2.5D方式であり、横顔を再現する完全な3D人物ではない。
960×540、30fpsを目標に上限制御。写真の陰影をそのまま使い、追加照明で不自然に暗くしない。
RTX 4060 Ti 8GBの待機系を想定し、大型GPU推論モデルを追加ロードしない。
実機の性能はブラウザのWebGLアクセラレーションに依存し、8GB機での実測は別途必要。

## 操作

- /avatar-3d でプレビュー。口の動作テストは無音のデモ。
- 設定の「3Dデジタルヒューマン（軽量・自然会話）」を保存。
- モード値 realtime_3d は既存の主系基準設定同期に含まれる。
- 氏名挨拶・会社伝言整理は自然会話処理を利用する。
- 発話の音量で口を開閉する簡易同期。音素別の口形状推定は未実装。
- 瞬き、視線、首の動き、呼吸を連続描画。TTS完了後に発話開始。
- WebGL初期化に失敗した場合は従来画像へフォールバック。

## 待機系への反映

両PCに下記ファイルを反映してから、現在の主系側でモードを選択する。
旧版の待機系は realtime_3d を処理できないため、片系のみの更新では切り替えない。

- app/avatar3d.js
- app/avatar-portrait.png
- app/three.min.js
- app/THREE-LICENSE.txt
- app/realtime_browser.py
- app/realtime_dialogue.py
- app/main.py
- app/templates/index.html
- Dockerfile.chrome
- tests/test_avatar3d_mode.py
- tests/test_avatar3d_browser.py

docker compose build app meet-browser response-worker meet-bot
docker compose up -d --no-deps app meet-browser response-worker meet-bot

.env、DB、Chrome認証プロファイルはコピー対象外。
Three.js r160.1はMITライセンス。ライセンス本文を同梱。

## 正面表示の更新

元画像の顔立ち・髪型・紺のジャケット・白シャツを保持。
口の開閉は小さなテクスチャ変形で表現し、音素別の口形状推定ではない。
横向き・大きなうなずきは対象外。写真の立体化のため見えない面は生成しない。
待機系には app/avatar-portrait.png を含めて全変更ファイルを配置する。
実写人物を復元した、または8GB実機で性能保証済みという意味ではない。
