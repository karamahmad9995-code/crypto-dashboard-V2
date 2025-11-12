import fs from "node:fs";
import path from "node:path";

const BINANCE = "https://api.binance.com";
const SYMBOLS = (process.env.SYMBOLS || "BTCUSDT,ETHUSDT,XRPUSDT,BNBUSDT,SOLUSDT,DOGEUSDT,ADAUSDT,LTCUSDT,SHIBUSDT,PUMPUSDT")
  .split(",").map(s=>s.trim()).filter(Boolean);

const sleep = (ms)=> new Promise(r=>setTimeout(r,ms));
const nowMs = ()=> Date.now();
const last = (arr, n=1)=> arr[arr.length-n];

function ema(arr, p){ const k = 2/(p+1); let prev = arr[0]; const out=[prev]; for(let i=1;i<arr.length;i++){ prev = arr[i]*k + prev*(1-k); out.push(prev);} return out; }
function stddev(a){ const m=a.reduce((x,y)=>x+y,0)/a.length; const v=a.reduce((x,y)=>x+(y-m)*(y-m),0)/a.length; return Math.sqrt(v); }
function rsi14(cl){ let g=0,l=0; for(let i=1;i<=14;i++){ const d=cl[i]-cl[i-1]; if(d>=0) g+=d; else l-=d;} const avgG=g/14, avgL=(l/14)||1e-6; const rs=avgG/avgL; return 100-(100/(1+rs)); }
function buildFeatures(closes){
  const ema5=ema(closes,5), ema15=ema(closes,15);
  const momentum=(last(closes)/closes[0])-1;
  const rets=[]; for(let i=1;i<closes.length;i++) rets.push((closes[i]/closes[i-1])-1);
  const sigma=stddev(rets), rsi=rsi14(closes);
  const s5=last(ema5)-ema5[ema5.length-2], s15=last(ema15)-ema15[ema15.length-2];
  return {rsi,s5,s15,momentum,lastRet:last(rets),sigma};
}
const sigmoid=(z)=>1/(1+Math.exp(-z));

function predictWithWeights(feat, model){
  if(!model){ // fallback v1
    const mu=[50,0,0,0,0,0.003], sd=[12,0.5,0.3,0.01,0.005,0.002];
    const W=[0.35,0.45,0.25,0.80,0.30,-0.15], b=0;
    const x=[feat.rsi,feat.s5,feat.s15,feat.momentum,feat.lastRet,feat.sigma].map((v,i)=>(v-mu[i])/(sd[i]||1));
    const z=W.reduce((s,w,i)=>s+w*x[i],b), pUp=sigmoid(z);
    const confidence=Math.max(0.55,Math.min(0.95,Math.max(pUp,1-pUp)));
    const rng=Math.max(0.2,Math.min(2.0,0.8*(feat.sigma*100)+0.6*(Math.abs(feat.momentum)*100)));
    return {pUp,confidence,rangePct:[Math.max(0.10, rng*0.55), rng]};
  }
  const featsList = model.features;
  const xraw = featsList.map(name => {
    if(name==="rsi") return feat.rsi;
    if(name==="ema5_slope") return feat.s5;
    if(name==="ema15_slope") return feat.s15;
    if(name==="momentum") return feat.momentum;
    if(name==="lastRet") return feat.lastRet;
    if(name==="sigma") return feat.sigma;
    return 0;
  });
  const mu = model.scaler.mu, sd = model.scaler.sd;
  const x = xraw.map((v,i)=>(v-(mu[i]||0))/((sd[i]||1)||1));
  const z = model.W.reduce((s,w,i)=>s+w*x[i], model.b||0);
  const pUp = sigmoid(z);
  const confidence = Math.max(0.55, Math.min(0.98, Math.max(pUp,1-pUp)));
  const rng = Math.max(0.2, Math.min(2.5, 0.8*(Math.max(0.0005,feat.sigma)*100) + 0.6*(Math.abs(feat.momentum)*100)));
  return {pUp, confidence, rangePct:[Math.max(0.10, rng*0.55), rng]};
}

function rangePrice(base, loPct, hiPct, dir){
  const b=Number(base);
  const lo = (dir==='Up') ? b*(1+loPct/100) : b*(1-hiPct/100);
  const hi = (dir==='Up') ? b*(1+hiPct/100) : b*(1-loPct/100);
  return {priceLo:lo, priceHi:hi};
}

async function fetchKlines(symbol, interval, limit){
  const u = `${BINANCE}/api/v3/klines?symbol=${symbol}&interval=${interval}&limit=${limit}`;
  const r = await fetch(u);
  if(!r.ok) throw new Error(`HTTP ${r.status} for ${symbol}`);
  const a = await r.json();
  return a.map(k => ({ t:k[0], c:Number(k[4]) }));
}

function ensureDir(p){ fs.mkdirSync(p, {recursive:true}); }
function appendJSONL(file, obj){ fs.appendFileSync(file, JSON.stringify(obj) + "\\n", "utf8"); }
function rotateIfLarge(file, maxLines=20000){
  if(!fs.existsSync(file)) return;
  const buf = fs.readFileSync(file, "utf8").trim();
  const lines = buf ? buf.split("\\n") : [];
  if(lines.length > maxLines){
    const slice = lines.slice(-Math.floor(maxLines*0.8));
    fs.writeFileSync(file, slice.join("\\n")+"\\n", "utf8");
  }
}

function loadModelIfAny(symbol, horizon){
  try{
    const p = path.join("data","models",symbol, `${horizon}m.json`);
    if(fs.existsSync(p)){
      const txt = fs.readFileSync(p, "utf8");
      return JSON.parse(txt);
    }
  }catch(e){}
  return null;
}

async function run(){
  const now = new Date();
  const m = now.getUTCMinutes();
  const do15 = (m % 15) === 0;
  const do60 = m === 0;
  const FORCE = process.env.FORCE === '1';

  if(!FORCE && !do15 && !do60){
    console.log(`[SKIP] Not at boundary (UTC ${now.toISOString()})`);
    return;
  }
  console.log(`[START] ${now.toISOString()} do15=${do15} do60=${do60} force=${FORCE}`);

  for(const sym of SYMBOLS){
    try{
      const kl = await fetchKlines(sym, "1m", 61);
      const closes = kl.slice(0, -1).map(p=>p.c);
      const base = closes[closes.length-1];
      const feat = buildFeatures(closes);

      const saveOne = async (h)=>{
        const model = loadModelIfAny(sym, h);
        const pred = predictWithWeights(feat, model);
        const dir = pred.pUp>=0.5 ? "Up" : "Down";
        const ts = Date.now();
        const {priceLo, priceHi} = rangePrice(base, pred.rangePct[0], pred.rangePct[1], dir);
        const row = {
          id: `${ts}-${h}`, t: ts, src: "auto",
          dir, conf: pred.confidence, range: pred.rangePct,
          priceLo, priceHi, base, horizon: h,
          outcome: (pred.confidence<0.62 || pred.rangePct[1]<0.30) ? "No-Trade" : "Pending"
        };
        const folder = path.join("data", sym);
        ensureDir(folder);
        const file = path.join(folder, `${h}m.jsonl`);
        appendJSONL(file, row);
        rotateIfLarge(file);
        console.log(`[OK] ${sym} H${h} -> ${file}`);
      };

      if(FORCE || do15) await saveOne(15);
      if(FORCE || do60) await saveOne(60);

      await new Promise(r=>setTimeout(r,150));
    }catch(e){
      console.error(`[ERR] ${sym}:`, e.message);
      await new Promise(r=>setTimeout(r,250));
    }
  }
  console.log("[DONE]");
}
run().catch(e=>{ console.error(e); process.exit(1); });
