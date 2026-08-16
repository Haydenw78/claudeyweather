/* Capture golden outputs from the CURRENT app, before any refactor.
   Synthetic but realistic hourly blocks, spanning the branches. */
const fs=require('fs'), {JSDOM}=require('jsdom');
const dom=new JSDOM(fs.readFileSync('index.html','utf8'),
  {runScripts:'dangerously',pretendToBeVisual:true,resources:'usable',
   url:'file://'+process.cwd()+'/index.html'});
dom.window.addEventListener('load',()=>setTimeout(()=>{
 const ev=s=>dom.window.eval(s);
 if(dom.window.eval("typeof ForecastCore")==='undefined'){
  console.log('FATAL: forecast-core.js did not load.');
  console.log('  index.html expects <script src="forecast-core.js"> beside it.');
  console.log('  Check the file exists in this directory and the name matches.');
  process.exit(1);
 }
 // deterministic pseudo-random so fixtures are reproducible
 // three regimes so the fixtures span bands and reason strings rather than
 // piling up in one corner: calm, moderate, energetic
 const REG={calm:{h:[0.1,0.5],k:[1,8],g:[2,11],rn:0.05,rm:3},
            mod :{h:[0.4,1.4],k:[5,16],g:[8,22],rn:0.25,rm:10},
            big :{h:[1.2,3.0],k:[14,32],g:[20,48],rn:0.45,rm:25}};
 const mk=(n,seed,reg,month)=>{let s=seed;const r=()=>(s=(s*1103515245+12345)&0x7fffffff)/0x7fffffff;
  const R=REG[reg], lo=(a)=>a[0]+(a[1]-a[0])*r();
  const out=[];const t0=Date.UTC(2026,month,1,0,0,0);
  for(let i=0;i<n;i++){
   out.push({dt:new Date(t0+i*3*3600e3),
    kt:lo(R.k), gust:lo(R.g), dir:Math.floor(360*r()), sst:20+6*r(),
    rainMm:r()<R.rn?R.rm*r():0, hrsToHigh:-6+12*r(),
    trains:[{h:lo(R.h),p:4+12*r()},{h:lo(R.h)*0.4,p:3+7*r()}]});
  }
  out.step=3; return out;};
 const spots=[
  {id:'ninemile',type:'shelf',offshoreKm:5.8,vMin:3,vMax:25,dMin:10,shelter:1},
  {id:'fidos',type:'shelf',offshoreKm:1.8,vMin:2,vMax:18,dMin:5,shelter:0.9},
  {id:'tweedbar',type:'estuarine',offshoreKm:0.3,vMin:1,vMax:6,dMin:5,shelter:0.5},
  {id:'seawaybar',type:'estuarine',offshoreKm:0.2,vMin:1,vMax:8,dMin:5,shelter:0.4},
  {id:'sykes',type:'oceanic',offshoreKm:80,vMin:10,vMax:35,dMin:10,shelter:1},
  {id:'smiths',type:'shelf',offshoreKm:13,vMin:5,vMax:30,dMin:18,shelter:1}];
 const fixtures=[];
 let k=0;
 spots.forEach((spot,si)=>{
 ['calm','mod','big'].forEach((reg,ri)=>{
 [1,7].forEach(month=>{
  const out=mk(40, 7919+(k++)*131, reg, month);
  dom.window.__out=out; dom.window.__spot=spot;
  ev('computeVis(__out,__spot,null,3)');
  out.forEach((b,i)=>{ if(i%9) return;
   fixtures.push({spot:spot.id,regimeKey:reg+month,i,
    in:{kt:b.kt,gust:b.gust,dir:b.dir,sst:b.sst,rainMm:b.rainMm,
        hrsToHigh:b.hrsToHigh,trains:b.trains},
    out:{ub:b.ub,stir:b.stir,ekman:b.ekman,sstAnom:b.sstAnom,rain72:b.rain72,
         plume:b.plume,vis:b.vis,visM:b.visM,visWhy:b.visWhy,
         visBand:b.visBand&&b.visBand.label},
    regime:reg, month});
  });
 });});});
 const path=process.argv[2]||'fixtures.json';
 if(process.argv[2]==='--check'){
  // Tolerance, not string equality. Math.tanh/sinh/pow are not required by the
  // spec to be correctly rounded, so V8 versions differ by a unit in the last
  // place. An exact check fails on a Node upgrade and gets ignored thereafter.
  // 1e-9 relative is far below anything the model could care about and far
  // above floating-point noise.
  const TOL=1e-9;
  const want=JSON.parse(fs.readFileSync('fixtures.json','utf8'));
  if(want.length!==fixtures.length){
   console.log(`REGRESSION FAILED: ${want.length} fixtures recorded, ${fixtures.length} produced`);
   process.exit(1);
  }
  const bad=[]; let maxRel=0;
  want.forEach((w,i)=>{
   const g=fixtures[i];
   if(w.spot!==g.spot||w.regimeKey!==g.regimeKey||w.i!==g.i){
    bad.push(`row ${i}: fixture identity changed`); return;
   }
   for(const k of Object.keys(w.out)){
    const a=w.out[k], b=g.out[k];
    if(typeof a==='number'&&typeof b==='number'){
     const rel=Math.abs(a-b)/Math.max(1e-12,Math.abs(a));
     if(rel>maxRel) maxRel=rel;
     if(rel>TOL) bad.push(`${w.spot} ${w.regimeKey} row ${w.i}: ${k} ${a} -> ${b} (rel ${rel.toExponential(2)})`);
    } else if(a!==b){
     bad.push(`${w.spot} ${w.regimeKey} row ${w.i}: ${k} "${a}" -> "${b}"`);
    }
   }
  });
  if(bad.length){
   console.log(`REGRESSION FAILED: ${bad.length} value(s) beyond ${TOL}`);
   bad.slice(0,15).forEach(l=>console.log('  '+l));
   if(bad.length>15) console.log(`  ...and ${bad.length-15} more`);
   process.exit(1);
  }
  console.log(`REGRESSION OK: ${fixtures.length} fixtures unchanged `
   +`(largest relative drift ${maxRel.toExponential(2)}, tolerance ${TOL})`);
  process.exit(0);
 }
 fs.writeFileSync(path,JSON.stringify(fixtures,null,1));
 console.log('captured',fixtures.length,'fixtures across',spots.length,'spots');
 const v=fixtures.map(f=>f.out.vis).filter(x=>x!=null);
 console.log('vis range',Math.min(...v).toFixed(2),'to',Math.max(...v).toFixed(2));
 const bands={}; fixtures.forEach(f=>bands[f.out.visBand]=(bands[f.out.visBand]||0)+1);
 console.log('bands',JSON.stringify(bands));
 const whys={}; fixtures.forEach(f=>whys[f.out.visWhy]=(whys[f.out.visWhy]||0)+1);
 console.log('reasons',JSON.stringify(whys));
 process.exit(0);
},600));
