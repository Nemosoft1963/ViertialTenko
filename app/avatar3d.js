// Front-view portrait projected on a depth mesh, not a reconstructed full head.
window.createTenkoAvatar3D = function(canvas) {
 const T=window.THREE;
 const renderer=new T.WebGLRenderer({canvas,antialias:true,preserveDrawingBuffer:true});
 renderer.setSize(960,540,false);renderer.setPixelRatio(1);
 const scene=new T.Scene();scene.background=new T.Color('#e1ddd6');
 const camera=new T.OrthographicCamera(-16/9,16/9,1,-1,0.01,10);
 camera.position.z=3;
 const geometry=new T.PlaneGeometry(32/9,2,128,72);
 const position=geometry.attributes.position,uv=geometry.attributes.uv;
 const base=new Float32Array(position.array.length);
 const influence=new Float32Array(position.count);
 for(let i=0;i<position.count;i++){
   const x=uv.getX(i), y=1-uv.getY(i);
   const face=Math.exp(-(((x-.503)/.094)**4+((y-.335)/.22)**4)*1.8);
   const nose=Math.exp(-(((x-.503)/.021)**2+((y-.377)/.062)**2)*2);
   position.setZ(i,.09*face+.022*nose);
   influence[i]=face;
 }
 base.set(position.array);
 const uniforms={portrait:{value:null},mouth:{value:0},blink:{value:0}};
 const material=new T.ShaderMaterial({uniforms,
   vertexShader:`varying vec2 p;void main(){p=uv;gl_Position=projectionMatrix*modelViewMatrix*vec4(position,1.0);}`,
   fragmentShader:`
     uniform sampler2D portrait;uniform float mouth;uniform float blink;varying vec2 p;
     void main(){
       vec2 q=p;
       float mx=(p.x-.503)/.031;
       float line=.545;
       float horizontal=max(0.,1.-mx*mx);
       float gap=mouth*.081*horizontal;
       float lower=1.-smoothstep(line-.036,line,p.y);
       float zone=exp(-pow((p.x-.503)/.043,4.)-pow((p.y-line)/.052,4.));
       q.y+=mouth*.108*zone*lower;
       vec4 color=texture2D(portrait,q);
       // Small oral opening; avoid invented large teeth and lip stretching.
       float inside=(1.-smoothstep(gap*.55,gap+.0005,abs(p.y-line)))*horizontal*mouth;
       color.rgb=mix(color.rgb,vec3(.19,.085,.08),clamp(inside*.72,0.,.72));
       for(int i=0;i<2;i++){
         float ex=i==0?.468:.542;
         vec2 e=vec2((p.x-ex)/.022,(p.y-.692)/.010);
         float lid=(1.-smoothstep(.65,1.,dot(e,e)))*blink;
         vec3 skin=texture2D(portrait,vec2(p.x,.709)).rgb;
         color.rgb=mix(color.rgb,skin,lid*.92);
       }
       gl_FragColor=color;
     }`});
 const mesh=new T.Mesh(geometry,material);scene.add(mesh);
 let loaded=false,disposed=false,level=0,previous=null;
 let resolveReady,rejectReady;
 const ready=new Promise((resolve,reject)=>{resolveReady=resolve;rejectReady=reject;});
 const texture=new T.TextureLoader().load(
   window.__tenkoPortraitData || '/avatar-3d/asset/avatar-portrait.png',
   ()=>{if(disposed)return;uniforms.portrait.value=texture;loaded=true;resolveReady();},
   undefined,()=>{window.__tenkoAvatar3DError='Portrait texture could not be loaded';rejectReady(new Error(window.__tenkoAvatar3DError));});
 // The photo already contains lighting. Do not light it a second time.
 texture.minFilter=T.LinearFilter;texture.magFilter=T.LinearFilter;texture.generateMipmaps=false;
 const api={ready,render(t,a,speaking){
   if(disposed||!loaded)return;
   const dt=previous===null?1/30:Math.min(.1,Math.max(0,t-previous));previous=t;
   level+=(Math.min(.7,Math.max(0,a))-level)*(1-Math.exp(-dt*14));
   uniforms.mouth.value=speaking?level:0;
   const phase=t%5.7;
   uniforms.blink.value=phase>5.52?Math.sin((phase-5.52)/.18*Math.PI):0;
   // Sub-pixel breathing and shallow parallax keep the photo recognisable.
   const yaw=Math.sin(t*.47)*.018,nod=Math.sin(t*(speaking?1.5:.8))*.0015;
   for(let i=0;i<position.count;i++){
     const j=i*3,weight=influence[i];
     position.setXYZ(i,base[j]+weight*yaw*base[j+2],base[j+1]+weight*nod,base[j+2]);
   }
   position.needsUpdate=true;
   renderer.render(scene,camera);
 },dispose(){disposed=true;texture.dispose();geometry.dispose();material.dispose();renderer.dispose();}};
 return api;
};
