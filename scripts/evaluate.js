// ====================================================
//  evaluate.js — marks Pending -> Correct/Wrong (CoinCap)
// ====================================================
import fs from "node:fs";
import path from "node:path";

const COINCAP = "https://api.coincap.io/v2";
const SYMBOLS = (process.env.SYMBOLS || "BTCUSDT,ETHUSDT,XRPUSDT,BNBUSDT,SOLUSDT,DOGEUSDT,ADAUSDT,LTCUSDT,SHIBUSDT,PUMPUSDT")
  .split(",").map(s=>s.trim()).filter(Boolean);

function splitSymbol(sym){ return { base: sym.replace(/USDT$/,"").toLowerCase(), quote: "usdt" }; }

async function fetchCoinCap(u){
  const r = await fetch(u, {cache:"no-store"});
  if(!r.ok) throw new Error(`HTTP ${r.status}: ${u}`);
  return await r.json();
}

// شمعة دقيقة واحدة تغطي اللحظة بعد الاستحقاق
async function fetchCloseAfterDue(sym, dueMs){
  const { base, quote } = splitSymbol(sym);
  const start = dueMs;
  const end   = dueMs + 120000; // +120s
  const url = `${COINCAP}/candles?exchange=binance&interval=m1&base=${base}&quote=${quote}&start=${start}&end=${end}`;
  try{
    const j = await fetchCoinCap(url);
    if(j.data && j.data.length>0) return Number(j.data[0].close);
  }catch(e){}
  // Fallback: السعر الحالي
  try{
    const j = await fetchCoinCap(`${COINCAP}/rates/${base}`);
    return Number(j.data.rateUsd);
  }catch(e){}
  return null;
}

function parseJSONL(file){
  if(!fs.existsSync(file)) return [];
  const txt = fs.readFileSync(file, "utf8").trim();
  if(!txt) return [];
  return txt.split("\n").map(x=>JSON.parse(x));
}
function writeJSONL(file, rows){
  const dir = path.dirname(file);
  fs.mkdirSync(dir, {recursive:true});
  fs.writeFileSync(file, rows.map(o=>JSON.stringify(o)).join("\n")+"\n", "utf8");
}

async function evaluateFile(sym, horizon){
  const file = path.join("data", sym, `${horizon}m.jsonl`);
  const rows = parseJSONL(file);
  if(rows.length===0) return;

  let changed=false;
  const now = Date.now();
  for(const it of rows){
    if(it.outcome!=="Pending") continue;
    const due = it.t + horizon*60*1000;
    if(now < due) continue;

    const close = await fetchCloseAfterDue(sym, due);
    if(close==null) continue;

    const delta = (close/it.base)-1;
    const ok = (delta>0 && it.dir==='Up') || (delta<=0 && it.dir==='Down');
    it.outcome = ok? 'Correct':'Wrong';
    changed=true;
  }
  if(changed) writeJSONL(file, rows);
}

async function run(){
  for(const sym of SYMBOLS){
    for(const h of [15,60]){
      try{ await evaluateFile(sym, h); }catch(e){ console.error(`[ERR evaluate] ${sym} H${h}:`, e.message); }
    }
  }
}
run().catch(e=>{ console.error(e); process.exit(1); });
