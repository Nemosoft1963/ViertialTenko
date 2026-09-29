# 待機モーション付きアバター

## 動作

仮想カメラは既定で `512x288 / 30fps` を生成する。

1. `/data/idle-base.mp4` が存在する場合、動画を音声の長さまでシームレスにループする。
2. 発話中は音量包絡に応じた口の動きを各フレームへ重ねる。
3. 無音中も待機動画、瞬き、視線を継続する。
4. 待機動画がない場合、静止画へ呼吸と緩やかな上下左右の動きを加える。

## 実写の待機動画を使用する

正面向きで口を閉じた短い動画を `idle-base.mp4` としてデータボリュームへ配置する。
継ぎ目が目立たない5～10秒程度の動画を推奨する。

```powershell
docker compose cp .\idle-base.mp4 app:/data/idle-base.mp4
```

仮想カメラを再生成する。

```powershell
docker compose exec -T app python -m app.build_virtual_avatar --source /data/virtual-checkin-operator.png --audio /meet-config/virtual-mic.wav --output /meet-config/virtual-camera.y4m --width 512 --height 288 --fps 30
docker compose restart meet-browser
```

生成結果のJSONで `idle_mode` が `video_loop` なら待機動画、`procedural` なら静止画フォールバックが使用されている。

## シナリオ音声ごと再生成する

```powershell
docker compose exec -T app python -m app.build_meet_scenario_audio --avatar-fps 30
docker compose restart meet-browser
```

## 無効化・調整

- 待機動画を使わず静止画モーションにする: `--skip-idle-video`
- 別の待機動画を使う: `--idle-video /data/別の動画.mp4`
- シナリオ音声生成側では `--avatar-idle-video` または `--skip-idle-video` を使用する。
