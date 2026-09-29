/* Real rendered frames only. No portrait deformation in this mode. */
window.createTenkoMetaHuman = async function () {
  const canvas = document.createElement('canvas'); canvas.width=1280;canvas.height=720;
  const ctx=canvas.getContext('2d'); let stopped=false, last=0;
  function unavailable(){ctx.fillStyle='#15202c';ctx.fillRect(0,0,1280,720);ctx.fillStyle='white';ctx.font='32px sans-serif';ctx.fillText('MetaHuman: video unavailable',60,360);window.__tenkoMetaHumanReady=false;}
  async function update(){
    const encoded=await window.__tenkoMetaHumanFrame();
    const bitmap=await createImageBitmap(await (await fetch('data:image/jpeg;base64,'+encoded)).blob());
    if(!stopped){ctx.drawImage(bitmap,0,0,1280,720);last=performance.now();window.__tenkoMetaHumanReady=true;}
    bitmap.close();
  }
  try { await update(); } catch(e) { unavailable(); throw new Error('MetaHuman video is not ready'); }
  const timer=setInterval(()=>{if(performance.now()-last>1500)unavailable();},250);
  (async()=>{while(!stopped){try{await update();}catch(e){unavailable();}await new Promise(r=>setTimeout(r,33));}})();
  const stream=canvas.captureStream(30);
  const dispose=()=>{stopped=true;clearInterval(timer);window.__tenkoMetaHumanReady=false;};
  stream.getVideoTracks()[0].addEventListener('ended',dispose);
  return {stream,dispose};
};

window.tenkoPCMToWav = function(buffer){
  const count=buffer.length, data=new ArrayBuffer(44+count*2), v=new DataView(data);
  const str=(offset,s)=>{for(let i=0;i<s.length;i++)v.setUint8(offset+i,s.charCodeAt(i));};
  str(0,'RIFF');v.setUint32(4,36+count*2,true);str(8,'WAVE');str(12,'fmt ');
  v.setUint32(16,16,true);v.setUint16(20,1,true);v.setUint16(22,1,true);
  v.setUint32(24,buffer.sampleRate,true);v.setUint32(28,buffer.sampleRate*2,true);
  v.setUint16(32,2,true);v.setUint16(34,16,true);str(36,'data');v.setUint32(40,count*2,true);
  const channels=Array.from({length:buffer.numberOfChannels},(_,i)=>buffer.getChannelData(i));
  for(let i=0;i<count;i++){let s=0;for(const c of channels)s+=c[i]/channels.length;s=Math.max(-1,Math.min(1,s));v.setInt16(44+i*2,s<0?s*32768:s*32767,true);}
  let result='';const bytes=new Uint8Array(data);for(let i=0;i<bytes.length;i+=8192)result+=String.fromCharCode(...bytes.subarray(i,i+8192));
  return btoa(result);
};
