import fs from "node:fs";
import path from "node:path";

const SYMBOLS = (process.env.SYMBOLS || "BTCUSDT,ETHUSDT,XRPUSDT,BNBUSDT,SOLUSDT,DOGEUSDT,ADAUSDT,LTCUSDT,SHIBUSDT,PUMPUSDT")
  .split(",").map(s=>s.trim()).filter(Boolean);

function parseJSONL(file){
  if(!fs.existsSync(file)) return [];
  const txt = fs.readFileSync(file, "utf8").trim();
  if(!txt) return [];
  return txt.split("\\n").map(x=>JSON.parse(x));
}
function writeJSON(file, obj){
  const dir = path.dirname(file);
  fs.mkdirSync(dir, {recursive:true});
  fs.writeFileSync(file, JSON.stringify(obj, null, 2), "utf8");
}

function windowStats(rows, sinceMs){
  const recent = rows.filter(r => r.t >= sinceMs);
  const byH = {15:[], 60:[]};
  recent.forEach(r => { if(r.horizon===15) byH[15].push(r); else if(r.horizon===60) byH[60].push(r); });
  function stats(arr){
    const c = arr.length;
    const corr = arr.filter(r=>r.outcome==='Correct').length;
    const wrong = arr.filter(r=>r.outcome==='Wrong').length;
    const no = arr.filter(r=>r.outcome==='No-Trade').length;
    const denom = Math.max(1, corr+wrong);
    const hit = Math.round(100 * (corr/denom));
    return {count:c, correct:corr, wrong:wrong, noTrade:no, hit:hit};
  }
  return { h15: stats(byH[15]), h60: stats(byH[60]) };
}

async function run(){
  const now = Date.now();
  const d1 = now - 24*60*60*1000;
  const d7 = now - 7*24*60*60*1000;
  const d30 = now - 30*24*60*60*1000;

  const globalSummary = {};

  for(const sym of SYMBOLS){
    const f15 = parseJSONL(`data/${sym}/15m.jsonl`);
    const f60 = parseJSONL(`data/${sym}/60m.jsonl`);
    const all = f15.concat(f60);

    const s24 = windowStats(all, d1);
    const s7  = windowStats(all, d7);
    const s30 = windowStats(all, d30);

    const symSummary = {
      h24: { hit15: s24.h15.hit, hit60: s24.h60.hit, counts: s24 },
      h7:  { hit15: s7.h15.hit,  hit60: s7.h60.hit,  counts: s7  },
      h30: { hit15: s30.h15.hit, hit60: s30.h60.hit, counts: s30 }
    };
    writeJSON(`data/${sym}/summary.json`, symSummary);
    globalSummary[sym] = symSummary;
  }
  writeJSON(`data/summary.json`, globalSummary);
}
run().catch(e=>{ console.error(e); process.exit(1); });
