#!/usr/bin/env python3
import json, os, sqlite3, sys
from datetime import datetime
from itertools import product

PR=os.path.abspath(os.path.join(os.path.dirname(__file__),"..",".."))
sys.path.insert(0,os.path.join(PR,"multi_agent"))
DB=os.path.join(PR,"multi_agent","data","llm_predictions.db")
OUT=os.path.join(PR,"multi_agent","config","predictor_params.json")
WG={"technical":[0.20,0.25,0.30,0.35],"fundamental":[0.15,0.20,0.25],"sentiment":[0.05,0.10,0.15],"debate":[0.15,0.20,0.25,0.30]}
TG={"bull":list(range(50,66,2)),"bear":list(range(35,48,2))}
def cw(tf,fd,s,dn,w):return max(0,min(100,tf*w["technical"]+fd*w["fundamental"]+s*w["sentiment"]+(50+dn*8)*w["debate"]))
def sg(w,b,be):return "bullish" if w>=b else "bearish" if w<=be else "neutral"
def cr(s,r):return s=="neutral" or(r>0.5 if s=="bullish" else r<-0.5)
def ld(cat=None):
 co=sqlite3.connect(DB);co.row_factory=sqlite3.Row
 q="SELECT pred_date,category,component_scores,horizon_1d_return FROM agentic_predictions WHERE component_scores IS NOT NULL AND horizon_1d_return IS NOT NULL"
 if cat:q+=" AND category='%s'"%cat
 rr=co.execute(q).fetchall();co.close();rs=[]
 for r in rr:
  try:sc=json.loads(r["component_scores"]);rt=float(r["horizon_1d_return"])
  except:continue
  if not isinstance(sc,dict):continue
  t=sc.get("technical",50)
  if isinstance(t,dict):t=50
  rs.append({"d":r["pred_date"],"t":float(t),"f":float(sc.get("fundamental_score",50)),"s":float(sc.get("sentiment",50)),"dn":float(sc.get("debate_net",0)),"r":rt,"cat":r["category"]})
 return rs
def ev(rs,w,b,be):
 c=t=0
 for r in rs:
  ww=cw(r["t"],r["f"],r["s"],r["dn"],w);ss=sg(ww,b,be)
  if ss=="neutral":continue
  t+=1
  if cr(ss,r["r"]):c+=1
 return {"a":c/t*100 if t else 0,"n":t}
def opt(cat,lb):
 rs=ld(cat)
 if len(rs)<20:print("  %s: %d recs (skip)"%(lb,len(rs)));return None
 ds=sorted(set(r["d"] for r in rs))
 tr=[r for r in rs if r["d"] in ds[:-1]];vr=[r for r in rs if r["d"] in ds[-1:]]
 print("  %s: %d recs | train=%d val=%d"%(lb,len(rs),len(tr),len(vr)))
 wcs=[]
 for a,b,c,d in product(*WG.values()):
  if abs(a+b+c+d-1)<0.01:wcs.append(dict(zip(WG.keys(),(a,b,c,d))))
 best=None
 for w in wcs:
  for b in TG["bull"]:
   for be in TG["bear"]:
    if b<=be+5:continue
    te=ev(tr,w,b,be)
    if te["n"]<5:continue
    ve=ev(vr,w,b,be)
    if ve["n"]<2:continue
    co=te["a"]*0.6+ve["a"]*0.4
    if best is None or co>best["score"]:best={"score":co,"w":w,"b":b,"be":be,"tr":te,"vr":ve}
 if not best:return None
 nl,nh=best["be"]+2,best["b"]-3
 w=best["w"]
 return {"weights":{"technical":w["technical"],"fundamental":w["fundamental"],"sentiment":w["sentiment"],"macro":0.0,"debate":w["debate"]},"threshold":{"strong_bull":best["b"]+5,"bull":best["b"],"neutral_high":nh,"neutral_low":nl,"bear":best["be"],"strong_bear":best["be"]-5},"stats":"train=%.1f%%(%d) val=%.1f%%(%d)"%(best["tr"]["a"],best["tr"]["n"],best["vr"]["a"],best["vr"]["n"]),"score":round(best["score"],1)}
def main():
 print("=%s="%("*"*48));print(" Optimizer V2 - cat-specific");print("=%s="%("*"*48))
 rs={"_version":2,"updated_at":datetime.now().strftime("%Y-%m-%dT%H:%M:%S")}
 for cat,lb in[("个股","Stk"),("ETF","ETF"),("期货","Fut"),("US","US")]:
  r=opt(cat,lb)
  if r:rs[cat]=r
 print();print(" Global");r=opt(None,"All")
 if r:rs["_default"]=r
 rs["updated_by"]="param_opt_v2"
 with open(OUT,"w")as f:json.dump(rs,f,ensure_ascii=False,indent=2)
 print();print("="*50);print("Results:")
 for k in["个股","ETF","期货","US","_default"]:
  if k in rs:print("  %s: %s"%(k,rs[k].get("stats","N/A")))
 print("Saved: %s"%OUT)
if __name__=="__main__":main()
