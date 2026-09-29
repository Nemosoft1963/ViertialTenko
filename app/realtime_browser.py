import base64
import asyncio
import os
import sqlite3
import tempfile
from pathlib import Path

import edge_tts

try:
    from app.realtime_dialogue import MODE_REALTIME, MODE_REALTIME_GPU, MODE_REALTIME_NATURAL, MODE_REALTIME_3D, MODE_METAHUMAN, setting
    from app import metahuman_bridge
except ImportError:
    from realtime_dialogue import MODE_REALTIME, MODE_REALTIME_GPU, MODE_REALTIME_NATURAL, MODE_REALTIME_3D, MODE_METAHUMAN, setting
    import metahuman_bridge

DB_PATH = os.getenv("DATABASE_PATH", "/data/tenko.db")
VOICE = os.getenv("REALTIME_TTS_VOICE", "ja-JP-NanamiNeural")

MEDIA_BRIDGE_SCRIPT = r"""
(() => {
  if (window.__tenkoBridgeVersion) return;
  window.__tenkoBridgeVersion = 2;
  const original = navigator.mediaDevices.getUserMedia.bind(navigator.mediaDevices);
  navigator.mediaDevices.getUserMedia = async (constraints) => {
    const stream = await original(constraints);
    if (!(localStorage.getItem('tenkoOperationMode') || '').startsWith('realtime')) return stream;
    if (constraints && constraints.audio) {
      const AudioCtx = window.AudioContext || window.webkitAudioContext;
      const context = window.__tenkoAudioContext || new AudioCtx();
      const destination = window.__tenkoAudioDestination || context.createMediaStreamDestination();
      const analyser = window.__tenkoAudioAnalyser || context.createAnalyser();
      analyser.fftSize = 256; analyser.smoothingTimeConstant = 0.82;
      window.__tenkoAudioContext = context;
      window.__tenkoAudioDestination = destination;
      window.__tenkoAudioAnalyser = analyser;
      for (const track of stream.getAudioTracks()) { stream.removeTrack(track); track.stop(); }
      stream.addTrack(destination.stream.getAudioTracks()[0]);
    }
    if (constraints && constraints.video && localStorage.getItem('tenkoOperationMode') === 'realtime_metahuman') {
      try {
        const avatar=await window.createTenkoMetaHuman();
        for(const track of stream.getVideoTracks()){stream.removeTrack(track);track.stop();}
        stream.addTrack(avatar.stream.getVideoTracks()[0]);
        const track=avatar.stream.getVideoTracks()[0], stop=track.stop.bind(track);
        track.stop=()=>{avatar.dispose();stop();};
        return stream;
      } catch(e) {for(const track of stream.getTracks())track.stop();throw e;}
    }
    if (constraints && constraints.video && stream.getVideoTracks().length) {
      const sourceStream = new MediaStream([stream.getVideoTracks()[0]]);
      const video = document.createElement('video');
      video.muted = true; video.playsInline = true; video.srcObject = sourceStream;
      await video.play();
      if (!video.videoWidth) await new Promise(resolve => video.addEventListener('loadeddata', resolve, {once:true}));
      const width = video.videoWidth || 512, height = video.videoHeight || 288;
      const snapshot = document.createElement('canvas'); snapshot.width = width; snapshot.height = height;
      snapshot.getContext('2d').drawImage(video, 0, 0, width, height);
      const canvas = document.createElement('canvas'); canvas.width = width; canvas.height = height;
      const ctx = canvas.getContext('2d');
      let avatar = null, avatarCanvas = null;
      if (localStorage.getItem('tenkoOperationMode') === 'realtime_3d') {
        try {
          avatarCanvas = document.createElement('canvas');
          avatar = window.createTenkoAvatar3D(avatarCanvas);
          await avatar.ready;
          canvas.width=960;canvas.height=540;
          window.__tenkoAvatar3DStatus='ready';
        } catch(e) { window.__tenkoAvatar3DStatus='error';window.__tenkoAvatar3DError=String(e); }
      }
      const started = performance.now();
      const audioData = new Uint8Array(128);
      let mouthLevel = 0;
      let previousFrame = 0, stopped = false;
      const draw = (now) => {
        if (stopped) return;
        if (avatar && now-previousFrame < 1000/30) {requestAnimationFrame(draw);return;}
        previousFrame=now;
        const t = (now - started) / 1000;
        if (avatar) {
          let level=0;
          const analyser=window.__tenkoAudioAnalyser;
          if(window.__tenkoSpeaking && analyser){analyser.getByteTimeDomainData(audioData);for(const b of audioData)level+=((b-128)/128)**2;level=Math.min(1,Math.sqrt(level/audioData.length)*5.5);}
          avatar.render(t,level,!!window.__tenkoSpeaking);
          ctx.drawImage(avatarCanvas,0,0,960,540);
          requestAnimationFrame(draw);return;
        }
        const scale = 1.012 + 0.005 * (1 - Math.cos(t * Math.PI * 2 / 5.8)) / 2;
        const x = Math.sin(t * Math.PI * 2 / 7.1) * width * 0.0035;
        const y = Math.sin(t * Math.PI * 2 / 5.8 + 0.8) * height * 0.0045;
        ctx.clearRect(0, 0, width, height);
        const gpuVideo = window.__tenkoGpuVideo;
        const gpuActive = !!(gpuVideo && !gpuVideo.ended && gpuVideo.readyState >= 2);
        if (gpuActive) ctx.drawImage(gpuVideo, 0, 0, width, height);
        else ctx.drawImage(snapshot, x - width*(scale-1)/2, y - height*(scale-1)/2, width*scale, height*scale);
        let target = 0;
        const analyser = window.__tenkoAudioAnalyser;
        if (window.__tenkoSpeaking && analyser && !gpuActive) {
          analyser.getByteTimeDomainData(audioData);
          let energy = 0;
          for (let i=0; i<audioData.length; i++) { const v=(audioData[i]-128)/128; energy += v*v; }
          target = Math.min(1, Math.sqrt(energy/audioData.length) * 5.5);
        }
        mouthLevel += (target - mouthLevel) * (target > mouthLevel ? 0.48 : 0.22);
        if (mouthLevel > 0.025) {
          const mx=width*0.5, my=height*0.444, mw=width*0.072, mh=height*0.048;
          const stretch=1 + mouthLevel*0.72;
          ctx.save();
          ctx.beginPath(); ctx.ellipse(mx,my,mw*0.47,mh*0.62*stretch,0,0,Math.PI*2); ctx.clip();
          ctx.drawImage(snapshot,mx-mw/2,my-mh/2,mw,mh,mx-mw/2,my-mh*stretch/2,mw,mh*stretch);
          ctx.restore();
          ctx.fillStyle=`rgba(45,20,27,${0.10+mouthLevel*0.24})`;
          ctx.beginPath(); ctx.ellipse(mx,my+mh*0.12,mw*0.19,mh*(0.035+mouthLevel*0.16),0,0,Math.PI*2); ctx.fill();
        }
        const blinkPhase = t % 4.7;
        if (blinkPhase > 4.55) {
          const blink = Math.sin((blinkPhase-4.55)/0.15*Math.PI);
          ctx.fillStyle=`rgba(55,38,34,${0.28*blink})`;
          for (const ex of [0.463,0.537]) { ctx.beginPath(); ctx.ellipse(width*ex,height*0.365,width*0.018,height*0.004,0,0,Math.PI*2); ctx.fill(); }
        }
        requestAnimationFrame(draw);
      };
      requestAnimationFrame(draw);
      const animated = canvas.captureStream(30);
      animated.getVideoTracks()[0].addEventListener('ended',()=>{stopped=true;if(avatar)avatar.dispose();video.pause();video.srcObject=null;});
      for (const track of stream.getVideoTracks()) { stream.removeTrack(track); track.stop(); }
      stream.addTrack(animated.getVideoTracks()[0]);
    }
    return stream;
  };
  window.__tenkoPlayVideo = async (encoded) => {
    const context = window.__tenkoAudioContext, destination = window.__tenkoAudioDestination;
    if (!context || !destination) throw new Error('media bridge is not ready');
    await context.resume();
    const binary=atob(encoded), bytes=new Uint8Array(binary.length);
    for(let i=0;i<binary.length;i++) bytes[i]=binary.charCodeAt(i);
    const url=URL.createObjectURL(new Blob([bytes],{type:'video/mp4'}));
    const video=document.createElement('video'); video.playsInline=true; video.preload='auto'; video.src=url;
    const source=context.createMediaElementSource(video), analyser=window.__tenkoAudioAnalyser;
    if(analyser){source.connect(analyser); analyser.connect(destination);}else source.connect(destination);
    window.__tenkoGpuVideo=video; window.__tenkoSpeaking=true;
    try { await video.play(); await new Promise((resolve,reject)=>{video.onended=resolve;video.onerror=reject;}); }
    finally { window.__tenkoSpeaking=false; window.__tenkoGpuVideo=null; URL.revokeObjectURL(url); }
  };
  window.__tenkoPlayAudio = async (encoded) => {
    const context = window.__tenkoAudioContext;
    const destination = window.__tenkoAudioDestination;
    if (!context || !destination) throw new Error('audio bridge is not ready');
    await context.resume();
    const binary = atob(encoded); const bytes = new Uint8Array(binary.length);
    for (let i=0; i<binary.length; i++) bytes[i] = binary.charCodeAt(i);
    const buffer = await context.decodeAudioData(bytes.buffer);
    if(localStorage.getItem('tenkoOperationMode')==='realtime_metahuman'){
      if(!window.__tenkoMetaHumanReady)throw new Error('MetaHuman video unavailable');
      await window.__tenkoMetaHumanAudio(window.tenkoPCMToWav(buffer));
      // Configurable calibration delay: host audio drives Live Link before Meet output.
      await new Promise(r=>setTimeout(r,window.__tenkoMetaHumanAudioDelayMs||0));
    }
    const source = context.createBufferSource(); source.buffer = buffer; const analyser = window.__tenkoAudioAnalyser; if (analyser) { source.connect(analyser); analyser.connect(destination); } else { source.connect(destination); }
    window.__tenkoSpeaking = true;
    await new Promise((resolve, reject) => { source.onended=resolve; try { source.start(); } catch(e) { reject(e); } });
    window.__tenkoSpeaking = false;
  };
})();
"""


def save_setting(con, key, value):
    con.execute(
        "INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (key, value),
    )


async def install_media_bridge(context):
    directory = Path(__file__).parent
    from urllib.parse import urlsplit

    def require_meet(source):
        parsed = urlsplit(source["frame"].url)
        if parsed.scheme != "https" or parsed.hostname != "meet.google.com":
            raise PermissionError("MetaHuman bindings are restricted to Google Meet")

    async def get_frame(source):
        require_meet(source)
        return await asyncio.to_thread(metahuman_bridge.frame)

    async def send_audio(source, encoded):
        require_meet(source)
        return await asyncio.to_thread(metahuman_bridge.audio, encoded)

    await context.expose_binding("__tenkoMetaHumanFrame", get_frame)
    await context.expose_binding("__tenkoMetaHumanAudio", send_audio)
    scripts = [directory.joinpath(name).read_text(encoding="utf-8") for name in ("three.min.js", "avatar3d.js", "metahuman.js")]
    delay = max(0, min(2000, int(os.getenv("METAHUMAN_AUDIO_DELAY_MS", "200"))))
    scripts.insert(0, f"window.__tenkoMetaHumanAudioDelayMs={delay};")
    portrait = base64.b64encode(directory.joinpath("avatar-portrait.png").read_bytes()).decode("ascii")
    scripts.insert(0, "window.__tenkoPortraitData='data:image/png;base64," + portrait + "';")
    await context.add_init_script(script=";\n".join(scripts + [MEDIA_BRIDGE_SCRIPT]))


async def sync_media_mode(page):
    with sqlite3.connect(DB_PATH, timeout=30) as con:
        desired = setting(con, "operation_mode", "legacy")
    state = await page.evaluate("() => ({mode: localStorage.getItem('tenkoOperationMode') || 'legacy', bridge: !!window.__tenkoBridgeVersion})")
    if state["mode"] == desired and (desired not in (MODE_REALTIME, MODE_REALTIME_GPU, MODE_REALTIME_NATURAL, MODE_REALTIME_3D, MODE_METAHUMAN, "realtime_openwebui") or state["bridge"]):
        return True
    await page.evaluate("mode => localStorage.setItem('tenkoOperationMode', mode)", desired)
    await page.reload(wait_until="domcontentloaded")
    await page.wait_for_timeout(3000)
    return False


async def play_pending_prompt(page):
    with sqlite3.connect(DB_PATH, timeout=30) as con:
        if setting(con, "operation_mode", "legacy") not in (MODE_REALTIME, MODE_REALTIME_GPU, MODE_REALTIME_NATURAL, MODE_REALTIME_3D, MODE_METAHUMAN, "realtime_openwebui"):
            return False
        prompt_id = int(setting(con, "realtime_prompt_id", "0"))
        played_id = int(setting(con, "realtime_prompt_played_id", "0"))
        text = setting(con, "realtime_prompt_text")
        status_after = setting(con, "realtime_prompt_status_after", "listening")
        gpu_mode = setting(con, "operation_mode", "legacy") in (MODE_REALTIME_GPU, MODE_REALTIME_NATURAL) and setting(con, "realtime_prompt_fast", "0") != "1"
        gpu_ready_id = int(setting(con, "gpu_prompt_ready_id", "0"))
        gpu_state = setting(con, "gpu_pipeline_state", "standby")
        gpu_video = setting(con, "gpu_prompt_video")
        if not text or prompt_id <= played_id:
            return False
        if gpu_mode and gpu_ready_id < prompt_id and gpu_state != "error":
            return False
        save_setting(con, "realtime_prompt_playing", "1")
        save_setting(con, "realtime_prompt_state", "synthesizing")
    try:
        if gpu_mode and gpu_ready_id >= prompt_id and gpu_video:
            video_path = Path(gpu_video)
            allowed_root = Path("/meet-config/gpu-prompts").resolve()
            if allowed_root not in video_path.resolve().parents or not video_path.is_file():
                raise FileNotFoundError("GPU prompt video is unavailable")
            encoded = base64.b64encode(video_path.read_bytes()).decode("ascii")
            await page.evaluate("video => window.__tenkoPlayVideo(video)", encoded)
        else:
            with tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / "prompt.mp3"
                await edge_tts.Communicate(text, VOICE, rate="-5%").save(str(path))
                encoded = base64.b64encode(path.read_bytes()).decode("ascii")
            await page.evaluate("audio => window.__tenkoPlayAudio(audio)", encoded)
        with sqlite3.connect(DB_PATH, timeout=30) as con:
            save_setting(con, "realtime_prompt_played_id", str(prompt_id))
            save_setting(con, "realtime_prompt_state", "played")
            save_setting(con, "realtime_prompt_error", "")
            save_setting(con, "response_worker_status", status_after)
        return True
    except Exception as exc:
        with sqlite3.connect(DB_PATH, timeout=30) as con:
            save_setting(con, "realtime_prompt_state", "error")
            save_setting(con, "realtime_prompt_error", f"{type(exc).__name__}: {exc}"[:500])
        return False
    finally:
        with sqlite3.connect(DB_PATH, timeout=30) as con:
            save_setting(con, "realtime_prompt_playing", "0")
