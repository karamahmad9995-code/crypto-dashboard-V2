import fs from "node:fs";
import path from "node:path";

const BINANCE = "https://api.binance.com";
const SYMBOLS = (process.env.SYMBOLS || "BTCUSDT,ETHUSDT,XRPUSDT,BNBUSDT,SOLUSDT,DOGEUSDT,ADAUSDT,LTCUSDT,SHIBUSDT,PUMPUSDT")
  .split(",").map(s=>s.trim()).filter(Boolean);

async function fetchKlinesRange(symbol, startTs, endTs){
  const u = `${BINANCE}/api/v3/klines?symbol=${symbol}&interval=1m&startTime=${startTs}&endTime=${endTs}&limit=2`;
  const r = await fetch(u);
  if(!r.ok) throw new Error(`HTTP ${r.status} for ${symbol}`);
  return await r.json();
}

function parseJSONL(file){
  if(!fs.existsSync(file)) return [];
  const txt = fs.readFileSync(file, "utf8").trim();
  if(!txt) return [];
  return txt.split("\\n").map(x=>JSON.parse(x));
}
function writeJSONL(file, rows){
  const dir = path.dirname(file);
  fs.mkdirSync(dir, {recursive:true});
  fs.writeFileSync(file, rows.map(o=>JSON.stringify(o)).join("\\n")+"\\n", "utf8");
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

    let close = null;
    try{
      const a = await fetchKlinesRange(sym, due, due + 120000);
      if(a && a.length>0){
        close = Number(a[0][4]);
      }
    }catch(e){}

    if(close==null){
      try{
        const r=await fetch(`${BINANCE}/api/v3/ticker/price?symbol=${sym}`);
        if(r.ok){ const j=await r.json(); close = Number(j.price); }
      }catch(e){}
    }
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
