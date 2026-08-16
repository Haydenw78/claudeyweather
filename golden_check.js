/* Prove the extracted module reproduces index.html exactly, by running BOTH
   inside the same jsdom page and comparing component by component. */
const fs=require('fs'), {JSDOM}=require('jsdom');
const core=require('./forecast-core.js');
const dom=new JSDOM(fs.readFileSync('index.html','utf8'),
  {runScripts:'dangerously',pretendToBeVisual:true,url:'https://example.com/'});
setTimeout(()=>{
 const ev=s=>dom.window.eval(s);
 let n=0,bad=0,worst=0,worstCase=null;
 const rec=(a,b,label,c)=>{const d=Math.abs(a-b); n++; if(d>1e-12){bad++;}
   if(d>worst){worst=d;worstCase=label+' '+JSON.stringify(c);} };

 // orbitalVel across the supported domain
 for(const H of [0,0.5,1.2,2,3]) for(const T of [4,8,12,18]) for(const d of [5,10,20,35,200])
   rec(ev(`orbitalVel(${H},${T},${d})`), core.orbitalVel(H,T,d), 'orbitalVel', {H,T,d});

 // windMix: app inlines it, so rebuild the app expression verbatim
 for(const g of [0,8,11,11.0001,15,20,25,34,45,60,120])
   rec(ev(`Math.min(1,Math.pow(Math.max(0,${g}-11)/34,1.15))`), core.windMix(g), 'windMix', {g});

 // bedStir against the app's inline reduce
 for(const type of ['oceanic','shelf','estuarine'])
  for(const shel of [1,0.7,0.25]) for(const d of [5,10,25])
   for(const trains of [[{h:1,p:8}],[{h:2,p:12},{h:0.6,p:5}],[]]){
    const js=ev(`(function(){const tr=${JSON.stringify(trains)};
      const e=tr.reduce((a,t)=>a+Math.pow(orbitalVel(t.h*${shel},t.p,${d}),2),0);
      const ub=Math.sqrt(e); const bed=BED['${type}']||BED.shelf;
      return [ub, Math.min(3,bed.supply*ub/bed.uCrit)];})()`);
    const me=core.bedStir(trains,type,shel,d);
    rec(js[0],me.ub,'bedStir.ub',{type,shel,d}); rec(js[1],me.stir,'bedStir.stir',{type,shel,d});
   }

 // ceiling + branches + metres, rebuilt verbatim from index.html lines 862-937
 const appPredict=(f,spot)=>ev(`(function(){
   const type=${JSON.stringify(spot.type)}, spot=${JSON.stringify(spot)};
   const vMax=spot.vMax!=null?spot.vMax:(type==='oceanic'?32:type==='estuarine'?10:20);
   const vMin=spot.vMin!=null?spot.vMin:(type==='oceanic'?9:type==='estuarine'?1:3);
   const stirLag=${f.stirLag}, mixKmh=${f.mixKmh};
   const windMix=Math.min(1,Math.pow(Math.max(0,mixKmh-11)/34,1.15));
   const offKm=spot.offshoreKm!=null?spot.offshoreKm:(type==='estuarine'?0.5:type==='oceanic'?60:6);
   const rainReach=Math.exp(-offKm/7);
   const ceiling=Math.max(0,Math.min(100, 76 - 34*Math.min(1.6,stirLag) - 22*windMix
     - 10*${f.ekman} + 6*Math.max(-2,Math.min(2,${f.sstAnom}))
     - rainReach*Math.min(18,${f.rain72}*0.9) + ${f.season}));
   let vis;
   if(type==='oceanic') vis=Math.max(0,Math.min(100, 94 - 30*Math.min(1.6,stirLag) - 10*windMix));
   else if(type==='shelf') vis=ceiling;
   else vis=Math.max(0,Math.min(100, ceiling*Math.max(0.08,${f.tideQ})*(1-0.45*${f.plume})));
   return [ceiling, vis, vMin+(vMax-vMin)*Math.pow(Math.max(0,vis)/100,2.5), visBand(vis).label];
 })()`);

 const spots=[{type:'shelf',offshoreKm:5.8,vMin:3,vMax:25},
              {type:'shelf',offshoreKm:1.8,vMin:2,vMax:18},
              {type:'oceanic',offshoreKm:80,vMin:10,vMax:35},
              {type:'estuarine',offshoreKm:0.3,vMin:1,vMax:6},
              {type:'shelf'},{type:'oceanic'},{type:'estuarine'}];
 for(const spot of spots)
  for(const stirLag of [0,0.5,1.6,3])
   for(const mixKmh of [0,11,20,34,60])
    for(const ekman of [-0.5,0,0.4])
     for(const sstAnom of [-3,0,1.5,3])
      for(const rain72 of [0,10,60])
       for(const tideQ of [0,0.06,0.5,1])
        for(const plume of [0,0.5,1]){
         const f={stirLag,mixKmh,ekman,sstAnom,rain72,season:0,tideQ,plume};
         const a=appPredict(f,spot), m=core.predict(f,spot);
         rec(a[0],m.offshoreCeiling,'ceiling',{spot:spot.type,stirLag,mixKmh});
         rec(a[1],m.visibilityIndex,'vis',{spot:spot.type,stirLag,mixKmh,tideQ,plume});
         rec(a[2],m.visibilityMetres,'visM',{spot:spot.type,stirLag,mixKmh});
         if(a[3]!==m.visibilityBand){bad++;n++;worstCase='visBand '+JSON.stringify({spot:spot.type,vis:a[1]});}
         else n++;
        }
 console.log(`comparisons: ${n}`);
 console.log(`disagreements beyond 1e-12: ${bad}`);
 console.log(`worst absolute difference: ${worst.toExponential(3)}`);
 if(worstCase) console.log(`worst case: ${worstCase}`);
 console.log(bad===0 ? 'EQUIVALENT' : 'NOT EQUIVALENT');
 process.exit(0);
},2500);
