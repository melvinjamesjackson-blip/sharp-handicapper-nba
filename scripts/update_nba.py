#!/usr/bin/env python3
"""NBA ESPN completed-game updater, robust to combined shooting-stat fields.

Run: python scripts/update_nba.py
Does not overwrite existing good data if a fetch produces zero valid games.
"""
import csv
import io
import json
import pathlib
import re
import time
import urllib.request
from datetime import date, datetime, timedelta, timezone

ROOT = pathlib.Path(__file__).resolve().parents[1]
OUT = ROOT / 'data'
OUT.mkdir(parents=True, exist_ok=True)
START = date(2026, 10, 21)
END = date.today() - timedelta(days=1)  # exclude same-day partial games
URL = 'https://site.api.espn.com/apis/site/v2/sports/basketball/nba/'
HEADERS = {'User-Agent': 'Mozilla/5.0 (compatible; SharpHandicapperNBA/1.2)'}
MAP = {'GS':'GSW', 'NY':'NYK', 'NO':'NOP', 'SA':'SAS', 'UTAH':'UTA', 'PHO':'PHX', 'WSH':'WAS'}
FIELDS = ['date','away','home','away_score','home_score','away_possessions','home_possessions']

def fetch(path):
    req = urllib.request.Request(URL + path, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=30) as response:
        return json.load(response)

def code(v):
    return MAP.get(str(v).upper(),str(v).upper())

def get_stat_map(team):
    items = team.get('statistics') or []
    if isinstance(items, dict):
        items = items.get('statistics', [])
    values = {}
    for item in items:
        if not isinstance(item, dict):
            continue
        value = item.get('displayValue', item.get('value'))
        for label in [item.get('name'),item.get('label'),item.get('abbreviation')]:
            if label and value is not None:
                values[re.sub(r'[^a-z0-9]', '', str(label).lower())] = str(value)
    return values

def numeric(values, *names):
    for name in names:
        v = values.get(re.sub(r'[^a-z0-9]', '', name.lower()))
        if v is None:
            continue
        try:
            return float(str(v).replace(',','').replace('%',''))
        except ValueError:
            continue
    return None

def attempts(values, component_names, paired_names):
    v = numeric(values, *component_names)
    if v is not None:
        return v
    for name in paired_names:
        key = re.sub(r'[^a-z0-9]', '', name.lower())
        raw = values.get(key)
        if raw:
            parts = re.match(r'^\s*(\d+)\s*[-/]\s*(\d+)\s*$', raw)
            if parts:
                return float(parts.group(2))
    return None

def possessions(team):
    v = get_stat_map(team)
    fga = attempts(v, ['fieldGoalsAttempted','fieldGoalAttempts','fga'], ['fieldGoalsMade-fieldGoalsAttempted','fieldGoalsMadeAttempted','FG'])
    fta = attempts(v, ['freeThrowsAttempted','freeThrowAttempts','fta'], ['freeThrowsMade-freeThrowsAttempted','freeThrowsMadeAttempted','FT'])
    oreb = numeric(v,'offensiveRebounds','reboundsOffensive','OREB')
    tov = numeric(v,'turnovers','totalTurnovers','TO')
    missing = [k for k,n in {'FGA':fga,'FTA':fta,'OREB':oreb,'TO':tov}.items() if n is None]
    if missing:
        raise ValueError('missing '+','.join(missing)+'; statistic keys: '+','.join(sorted(v.keys())[:20]))
    p = fga-oreb+tov+0.44*fta
    if not (65 <= p <= 140):
        raise ValueError('implausible possessions '+str(p))
    return round(p,2)

def extract(event):
    if not event.get('status',{}).get('type',{}).get('completed'):
        return None
    stype = event.get('season',{}).get('type')
    if stype is not None and str(stype) != '2':
        return None
    comp = {c['homeAway']: c for c in event['competitions'][0]['competitors']}
    away,home = comp['away'],comp['home']
    ab,hb = code(away['team']['abbreviation']),code(home['team']['abbreviation'])
    summary = fetch('summary?event='+str(event['id']))
    teams = summary.get('boxscore',{}).get('teams',[])
    stats = {code(t['team']['abbreviation']):t for t in teams}
    if ab not in stats or hb not in stats:
        raise ValueError('summary missing team boxscore: expected '+ab+','+hb+', found '+','.join(stats))
    ap,hp=possessions(stats[ab]),possessions(stats[hb])
    return dict(date=event['date'][:10],away=ab,home=hb,away_score=int(float(away['score'])),home_score=int(float(home['score'])),away_possessions=ap,home_possessions=hp)

def main():
    previous = OUT/'nba_games_2026_27.csv'
    allgames = {}
    if previous.exists():
        with previous.open(newline='') as file:
            for r in csv.DictReader(file):
                if all(k in r for k in FIELDS):
                    allgames[(r['date'],r['away'],r['home'])] = r
    problems=[]
    days=0; completed=0; collected=0
    day=START
    while day<=END:
        try:
            board=fetch('scoreboard?dates='+day.strftime('%Y%m%d')+'&seasontype=2&limit=30')
            for event in board.get('events',[]):
                if not event.get('status',{}).get('type',{}).get('completed'):
                    continue
                completed += 1
                try:
                    result=extract(event)
                    if result:
                        key=(result['date'], result['away'], result['home'])
                        if key not in allgames:
                            allgames[key]=result; collected+=1
                except Exception as ex:
                    problems.append({'date':str(day),'event':str(event.get('id','')),'error':str(ex)})
                time.sleep(.3)
            days+=1
        except Exception as ex:
            problems.append({'date':str(day),'error':'scoreboard: '+str(ex)})
        day+=timedelta(days=1)
        time.sleep(.15)
    if not allgames:
        print(json.dumps({'days_checked':days,'completed_events_found':completed,'new_games':collected,'errors':problems[:12]},indent=2))
        raise RuntimeError('No valid games collected. See per-game rejection reasons above; existing files kept unchanged.')
    buf=io.StringIO()
    writer=csv.DictWriter(buf,fieldnames=FIELDS)
    writer.writeheader()
    writer.writerows(sorted(allgames.values(),key=lambda r:(r['date'],r['away'],r['home'])))
    tmp=previous.with_suffix('.tmp');tmp.write_text(buf.getvalue());tmp.replace(previous)
    metadata={'generated_at':datetime.now(timezone.utc).isoformat(),'source':'ESPN site public scoreboard and summary (unofficial)', 'games':len(allgames), 'new_games':collected, 'completed_events_found':completed,'days_checked':days,'errors':problems[:30], 'error_count':len(problems),'possessions':'estimated: FGA - OREB + TO + 0.44*FTA','last_game_date':max(k[0] for k in allgames)}
    (OUT/'nba_update_status.json').write_text(json.dumps(metadata,indent=2)+'\n')
    print(json.dumps({k:v for k,v in metadata.items() if k!='errors'},indent=2))

if __name__=='__main__': main()
