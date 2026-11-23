import urllib.request
UAS={"bot":"Mozilla/5.0 (nofilmstudy data bot)","chrome":"Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36","none":None}
URLS=["https://site.api.espn.com/apis/site/v2/sports/basketball/nba/scoreboard",
"https://site.web.api.espn.com/apis/site/v2/sports/basketball/nba/scoreboard",
"https://site.api.espn.com/apis/site/v2/sports/basketball/nba/injuries",
"https://sports.core.api.espn.com/v2/sports/basketball/leagues/nba/events",
"https://cdn.espn.com/core/nba/scoreboard?xhr=1",
"https://site.api.espn.com/apis/v2/sports/basketball/nba/standings",
"https://api-web.nhle.com/v1/score/now",
"https://cdn.nba.com/static/json/liveData/scoreboard/todaysScoreboard_00.json",
"https://api.actionnetwork.com/web/v1/scoreboard/nba?period=game&bookIds=15",
"https://statsapi.mlb.com/api/v1/schedule?sportId=1",
"https://www.thesportsdb.com/api/v1/json/3/eventsday.php?d=2026-11-23&s=Basketball"]
for u in URLS:
  for k,ua in UAS.items():
    h={"Accept":"application/json"}
    if ua: h["User-Agent"]=ua
    try:
      with urllib.request.urlopen(urllib.request.Request(u,headers=h),timeout=15) as r: print("PROBE",r.status,k,len(r.read()),u)
    except Exception as e: print("PROBE ERR",k,str(e)[:60],u)
