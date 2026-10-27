#!/usr/bin/env python3
"""NBA 2026-27 ESPN public endpoint updater. No third-party dependencies."""
import csv, io, json, os, pathlib, time, urllib.request
from datetime import date, timedelta, datetime, timezone
ROOT=pathlib.Path(__file__).resolve().parents[1]
OUT=ROOT/'data'; OUT.mkdir(exist_ok=True)
START=date(2026,10,21)
END=date.today()-timedelta(days=1) # never ingest today's partial games
URL='https://site.api.espn.com/apis/site/v2/sports/basketball/nba/'
HEADERS={'User-Agent':'SharpHandicapperResearch/1.2 (NBA scores updater)'}
MAP={'GS':'GSW','NY':'NYK','NO':'NOP','SA':'SAS','UTAH':'UTA','PHO':'PHX','WSH':'WAS'}
def fetch(path):
    req=urllib.request.Request(URL+path,headers=HEADERS)
    with urllib.request.urlopen(req,timeout=30) as res: return json.load(res)
def code(c): return MAP.get(c,c)
def stat(team, names):
    stats=team.get('statistics') or []
    lookup={str(s.get('name','')).lower():str(s.get('displayValue',s.get('value',''))) for s in stats}
    for n in names:
        v=lookup.get(n.lower())
        if v is not None:
            try:return float(v.replace(',','').replace('%',''))
            except ValueError:pass
    return None
def possessions(team):
    # FGA - OREB + TO + .44*FTA is standard estimated team possessions.
    fg=stat(team,['fieldGoalsAttempted','fieldGoalAttempts'])
    if fg is None:
        made_attempt=next((str(x.get('displayValue')) for x in team.get('statistics',[]) if x.get('name')=='fieldGoalsMade-fieldGoalsAttempted'),None)
        if made_attempt and '-' in made_attempt: fg=float(made_attempt.split('-')[-1])
    fta=stat(team,['freeThrowsAttempted','freeThrowAttempts'])
    orb=stat(team,['offensiveRebounds'])
    to=stat(team,['turnovers','totalTurnovers'])
    if None in (fg,fta,orb,to):raise ValueError('missing possession component')
    p=fg-orb+to+0.44*fta
    if not 65<=p<=130:raise ValueError('implausible possessions '+str(p))
    return round(p,2)
def extract(event):
    if not event.get('status',{}).get('type',{}).get('completed'):return None
    comp=event['competitions'][0]
    if event.get('season',{}).get('type') not in (None,2):return None
    competitors={c['homeAway']:c for c in comp['competitors']}
    away,home=competitors['away'],competitors['home']
    ab, hb=code(away['team']['abbreviation']),code(home['team']['abbreviation'])
    if ab==hb:raise ValueError('duplicate teams')
    details=fetch('summary?event='+event['id'])
    teams=details.get('boxscore',{}).get('teams',[])
    st={code(x['team']['abbreviation']):x for x in teams}
    ap=possessions(st[ab]);hp=possessions(st[hb])
    return dict(date=event['date'][:10],away=ab,home=hb,away_score=int(float(away['score'])),home_score=int(float(home['score'])),away_possessions=ap,home_possessions=hp)
def main():
    previous=OUT/'nba_games_2026_27.csv'; allgames={}
    if previous.exists():
        with previous.open(newline='') as f:
            for r in csv.DictReader(f):allgames[(r['date'],r['away'],r['home'])]=r
    missing=[]; days=0; found=0; d=START
    while d<=END:
        try:
            board=fetch('scoreboard?dates='+d.strftime('%Y%m%d')+'&seasontype=2&limit=30')
            for ev in board.get('events',[]):
                eid=str(ev.get('id',''))
                if not ev.get('status',{}).get('type',{}).get('completed'):continue
                try:
                    comp=ev['competitions'][0]['competitors'];a=code(next(x for x in comp if x['homeAway']=='away')['team']['abbreviation']);h=code(next(x for x in comp if x['homeAway']=='home')['team']['abbreviation']);key=(ev['date'][:10],a,h)
                    if key in allgames:continue
                    r=extract(ev)
                    if r:allgames[key]=r; found+=1
                except Exception as ex:missing.append({'date':str(d),'event':eid,'error':str(ex)})
                time.sleep(.4)
            days+=1
        except Exception as ex:missing.append({'date':str(d),'error':'scoreboard: '+str(ex)})
        d+=timedelta(days=1);time.sleep(.2)
    fields=['date','away','home','away_score','home_score','away_possessions','home_possessions']
    if not allgames:raise RuntimeError('No valid games collected; refusing to publish empty ratings')
    buf=io.StringIO();w=csv.DictWriter(buf,fieldnames=fields);w.writeheader();w.writerows(sorted(allgames.values(),key=lambda r:(r['date'],r['away'],r['home'])))
    temp=previous.with_suffix('.tmp');temp.write_text(buf.getvalue());temp.replace(previous)
    meta={'generated_at':datetime.now(timezone.utc).isoformat(),'source':'ESPN site NBA scoreboard and per-game boxscore (unofficial)','games':len(allgames),'new_games':found,'days_checked':days,'errors':missing[:30],'error_count':len(missing),'possessions':'estimated: FGA - OREB + TO + 0.44*FTA','last_game_date':max(k[0] for k in allgames)}
    (OUT/'nba_update_status.json').write_text(json.dumps(meta,indent=2)+'\n')
    print(json.dumps({k:v for k,v in meta.items() if k!='errors'}))
if __name__=='__main__':main()
