import os, json, base64
from datetime import datetime, timezone
from typing import Optional
import httpx
from fastapi import FastAPI, HTTPException, UploadFile, File
from fastapi.responses import HTMLResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from pymongo import MongoClient, DESCENDING
ROOT=os.path.dirname(__file__)
app=FastAPI(title="Terra Sphere")
app.mount("/static",StaticFiles(directory=os.path.join(ROOT,"static")),name="static")
OW=os.getenv("OPENWEATHER_API_KEY",""); GROQ=os.getenv("GROQ_API_KEY","")
CF_ID=os.getenv("CLOUDFLARE_ACCOUNT_ID",""); CF_KEY=os.getenv("CLOUDFLARE_API_KEY",os.getenv("CLOUDFLARE_API_TOKEN",""))
db=None
if os.getenv("MONGODB_URI"):
 try:
  client=MongoClient(os.getenv("MONGODB_URI"),serverSelectionTimeoutMS=3500); client.admin.command("ping"); db=client["terra_sphere"]
 except Exception: pass
class Point(BaseModel):
 lat:float; lon:float; altitude:Optional[float]=None
class Search(BaseModel):
 query:str=Field(min_length=1,max_length=180)
def coords(lat,lon):
 if not(-90<=lat<=90 and -180<=lon<=180): raise HTTPException(400,"Invalid coordinates.")
async def ow(path,params):
 if not OW: raise HTTPException(503,"Set OPENWEATHER_API_KEY in Render Environment.")
 async with httpx.AsyncClient(timeout=20) as c:r=await c.get("https://api.openweathermap.org"+path,params={**params,"appid":OW})
 if r.status_code>=400: raise HTTPException(502,r.json().get("message","OpenWeather request failed"))
 return r.json()
async def geocode(q):
 import re
 m=re.fullmatch(r"\s*(-?\d{1,2}(?:\.\d+)?)\s*[, ]\s*(-?\d{1,3}(?:\.\d+)?)\s*",q.strip())
 if m:
  lat,lon=map(float,m.groups());coords(lat,lon);return lat,lon
 rows=await ow("/geo/1.0/direct",{"q":q,"limit":1})
 if not rows:raise HTTPException(404,"Place not found.")
 return float(rows[0]["lat"]),float(rows[0]["lon"])
async def name(lat,lon):
 try:
  r=await ow("/geo/1.0/reverse",{"lat":lat,"lon":lon,"limit":1})
  if r:return ", ".join(str(x) for x in [r[0].get("name"),r[0].get("state"),r[0].get("country")] if x)
 except Exception:pass
 return f"{lat:.4f}, {lon:.4f}"
@app.get("/",response_class=HTMLResponse)
async def home():return open(os.path.join(ROOT,"index.html"),encoding="utf-8").read()
@app.get("/api/health")
async def health():return {"status":"ok","mongodb":db is not None,"openweather":bool(OW),"groq":bool(GROQ),"cloudflare":bool(CF_ID and CF_KEY)}
@app.post("/api/search")
async def search(q:Search):
 lat,lon=await geocode(q.query);return {"lat":lat,"lon":lon,"place":await name(lat,lon)}
@app.post("/api/weather")
async def weather(p:Point):
 coords(p.lat,p.lon)
 cur=await ow("/data/2.5/weather",{"lat":p.lat,"lon":p.lon,"units":"metric","lang":"en"})
 fc=await ow("/data/2.5/forecast",{"lat":p.lat,"lon":p.lon,"units":"metric","lang":"en"})
 w=(cur.get("weather") or [{}])[0]; place=await name(p.lat,p.lon)
 data={"lat":p.lat,"lon":p.lon,"place":place,"dt":cur.get("dt"),"timezone":cur.get("timezone",0),"temp":round(cur["main"]["temp"]),"feels_like":round(cur["main"]["feels_like"]),"humidity":cur["main"].get("humidity"),"wind":cur.get("wind",{}).get("speed",0),"clouds":cur.get("clouds",{}).get("all",0),"pressure":cur["main"].get("pressure"),"visibility":cur.get("visibility"),"condition":w.get("main","Clear"),"description":w.get("description",""),"icon":w.get("icon","01d"),"sunrise":cur.get("sys",{}).get("sunrise"),"sunset":cur.get("sys",{}).get("sunset"),"forecast":[{"dt":x.get("dt"),"temp":round(x.get("main",{}).get("temp",0)),"icon":(x.get("weather") or [{}])[0].get("icon","01d"),"description":(x.get("weather") or [{}])[0].get("description","")} for x in fc.get("list",[])[:8]]}
 if db is not None:
  try:db.explorations.insert_one({"lat":p.lat,"lon":p.lon,"place":place,"altitude":p.altitude,"created_at":datetime.now(timezone.utc)})
  except Exception:pass
 return data
@app.post("/api/sky")
async def sky(p:Point):
 coords(p.lat,p.lon)
 if not(CF_ID and CF_KEY):raise HTTPException(503,"Set CLOUDFLARE_ACCOUNT_ID and CLOUDFLARE_API_KEY in Render Environment.")
 place=await name(p.lat,p.lon); season,month=("winter",datetime.now(timezone.utc).month)
 if p.lat<0: season={"winter":"summer","spring":"autumn","summer":"winter","autumn":"spring"}[({12:"winter",1:"winter",2:"winter",3:"spring",4:"spring",5:"spring",6:"summer",7:"summer",8:"summer",9:"autumn",10:"autumn",11:"autumn"}).get(month,"summer")]
 else:season=({12:"winter",1:"winter",2:"winter",3:"spring",4:"spring",5:"spring",6:"summer",7:"summer",8:"summer",9:"autumn",10:"autumn",11:"autumn"}).get(month,"summer")
 desc="current local weather"
 if OW:
  try:
   cw=await ow("/data/2.5/weather",{"lat":p.lat,"lon":p.lon,"units":"metric"});desc=(cw.get("weather") or [{}])[0].get("description",desc); hour=__import__("datetime").datetime.fromtimestamp(cw["dt"]+cw.get("timezone",0),timezone.utc).hour
  except Exception:hour=datetime.now(timezone.utc).hour
 else:hour=datetime.now(timezone.utc).hour
 tod="night" if hour<5 or hour>=21 else "dawn" if hour<7 else "morning" if hour<11 else "daylight" if hour<16 else "golden hour" if hour<19 else "sunset"
 prompt=f"Ultra realistic cinematic panoramic sky background above terrain near {place}, season {season}, local time {tod}, weather {desc}; physically plausible lighting, atmospheric perspective, realistic clouds, wide 16:9, sky only, no land, no text."
 url=f"https://api.cloudflare.com/client/v4/accounts/{CF_ID}/ai/run/@cf/black-forest-labs/flux-1-schnell"
 async with httpx.AsyncClient(timeout=90) as c:r=await c.post(url,headers={"Authorization":f"Bearer {CF_KEY}","Content-Type":"application/json"},json={"prompt":prompt})
 if r.status_code>=400:raise HTTPException(502,"Cloudflare image generation failed.")
 if "application/json" in r.headers.get("content-type",""):
  image=(r.json().get("result") or {}).get("image")
  if not image:raise HTTPException(502,"Cloudflare returned no image.")
 else:image=base64.b64encode(r.content).decode()
 return {"image":"data:image/png;base64,"+image,"place":place,"season":season,"time_of_day":tod,"weather":desc}
@app.post("/api/assistant")
async def assistant(q:Search):
 if not GROQ:raise HTTPException(503,"Set GROQ_API_KEY in Render Environment.")
 prompt='Extract a location from the user message. Return only JSON: {"location":"place or coordinates or empty","reply":"short response"}. Do not invent weather. Message: '+q.query
 async with httpx.AsyncClient(timeout=25) as c:r=await c.post("https://api.groq.com/openai/v1/chat/completions",headers={"Authorization":f"Bearer {GROQ}"},json={"model":"llama-3.3-70b-versatile","messages":[{"role":"user","content":prompt}],"temperature":0.2,"response_format":{"type":"json_object"}})
 if r.status_code>=400:raise HTTPException(502,"Groq request failed.")
 try:o=json.loads(r.json()["choices"][0]["message"]["content"])
 except Exception:raise HTTPException(502,"Could not parse assistant response.")
 loc=(o.get("location") or "").strip()
 if not loc:return {"reply":o.get("reply","Which place should we explore?"),"weather":None}
 lat,lon=await geocode(loc);w=await weather(Point(lat=lat,lon=lon));return {"reply":o.get("reply") or "Here is the weather.","weather":w,"location":{"lat":lat,"lon":lon,"place":w["place"]}}
@app.post("/api/transcribe")
async def transcribe(file:UploadFile=File(...)):
 if not GROQ:raise HTTPException(503,"Set GROQ_API_KEY in Render Environment.")
 async with httpx.AsyncClient(timeout=45) as c:r=await c.post("https://api.groq.com/openai/v1/audio/transcriptions",headers={"Authorization":f"Bearer {GROQ}"},files={"file":(file.filename or "audio.webm",await file.read(),file.content_type or "audio/webm")},data={"model":"whisper-large-v3-turbo","response_format":"json"})
 if r.status_code>=400:raise HTTPException(502,"Groq transcription failed.")
 return {"text":r.json().get("text","")}
@app.post("/api/speak")
async def speak(q:Search):
 if not GROQ:raise HTTPException(503,"Set GROQ_API_KEY in Render Environment.")
 async with httpx.AsyncClient(timeout=45) as c:r=await c.post("https://api.groq.com/openai/v1/audio/speech",headers={"Authorization":f"Bearer {GROQ}"},json={"model":"playai-tts","voice":"Fritz-PlayAI","input":q.query,"response_format":"wav"})
 if r.status_code>=400:raise HTTPException(502,"Groq TTS failed.")
 return Response(r.content,media_type="audio/wav")
@app.get("/api/history")
async def history():
 if db is None:return {"items":[],"available":False}
 try:
  rows=list(db.explorations.find({},{"_id":0,"lat":1,"lon":1,"place":1,"created_at":1}).sort("created_at",DESCENDING).limit(12))
  for x in rows:
   if x.get("created_at"):x["created_at"]=x["created_at"].isoformat()
  return {"items":rows,"available":True}
 except Exception:return {"items":[],"available":False}
