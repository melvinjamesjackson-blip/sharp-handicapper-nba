#!/usr/bin/env python3
"""NBA 1.6A.1 independent historical CSV fallback (test-only).
Requires no packages. Strict fail-closed schema/date/team/game validation.
Outputs artifacts ONLY. Source CC BY 4.0, BoxScore Lab.
"""
import argparse,csv,datetime as dt,io,json,urllib.request,urllib.error,hashlib,re,sys
from collections import defaultdict
from pathlib import Path
SOURCE='https://www.boxscorelab.com/downloads/'
TEAMS=set('ATL BOS BKN CHA CHI CLE DAL DEN DET GSW HOU IND LAC LAL MEM MIA MIL MIN NOP NYK OKC ORL PHI PHX POR SAC SAS TOR UTA WAS'.split())
ALIAS={'BRK':'BKN','BKN':'BKN','PHO':'PHX','PHX':'PHX','CHO':'CHA','CHA':'CHA','GS':'GSW','GSW':'GSW','NY':'NYK','NYK':'NYK','NO':'NOP','NOK':'NOP','NOP':'NOP','SA':'SAS','SAS':'SAS','UTAH':'UTA','WSH':'WAS'}
GAMES_COLS=['date','away','home','away_score','home_score','away_possessions','home_possessions']
PRIOR_COLS=['team','off_rtg','def_rtg','pace','games','as_of','sos_off_adj','sos_def_adj']

def norm(k):return re.sub(r'[^a-z0-9]','',str(k).lower())
def field(row,names):
 keys={norm(k):k for k in row}
 for key in names:
  if norm(key) in keys:return row[keys[norm(key)]]
 return None

def need(row,names,context):
 v=field(row,names)
 if v is None or str(v).strip()=='': raise ValueError(f'Missing {names} at {context}; available columns: {list(row)}')
 return v

def team(v):
 t=ALIAS.get(str(v).upper().strip(),str(v).upper().strip())
 if t not in TEAMS:raise ValueError(f'Unrecognized team identifier: {v!r}')
 return t

def number(v):
 try:return float(str(v).replace(',',''))
 except Exception:raise ValueError(f'Invalid numeric data: {v!r}')

def download(name,timeout):
 url=SOURCE+name
 req=urllib.request.Request(url,headers={'User-Agent':'SharpHandicapper-HistoryValidation/1.6A.1 (public CSV archival downloader)','Accept':'text/csv,*/*'})
 with urllib.request.urlopen(req,timeout=timeout) as resp:
  data=resp.read(6_000_000)
 if not data:raise ValueError('Empty downloaded CSV')
 txt=data.decode('utf-8-sig'); r=csv.DictReader(io.StringIO(txt))
 if not r.fieldnames:raise ValueError('CSV has no headers')
 rows=list(r)
 if not rows:raise ValueError('CSV has no rows')
 return rows,hashlib.sha256(data).hexdigest(),r.fieldnames

def date_of(v):
 v=str(v).strip()
 for s in (v[:10],v):
  try:return dt.date.fromisoformat(s).isoformat()
  except ValueError:pass
  for fmt in ('%m/%d/%Y','%m/%d/%y','%b %d, %Y'):
   try:return dt.datetime.strptime(s,fmt).date().isoformat()
   except ValueError:pass
 raise ValueError(f'Unrecognized game date {v!r}')

def write(path,rows,headers):
 path.parent.mkdir(parents=True,exist_ok=True)
 with path.open('w',newline='',encoding='utf-8') as f:
  w=csv.DictWriter(f,fieldnames=headers);w.writeheader();w.writerows(rows)

def process_games(rows,season):
 games=defaultdict(list)
 for idx,r in enumerate(rows,2):
  ctx=f'game CSV row {idx}'
  gid=need(r,['game_id','gameid','id'],ctx)
  day=date_of(need(r,['game_date','date','game_day','gamedate'],ctx))
  t=team(need(r,['team_abbr','team_abbreviation','team','team_code','abbr'],ctx))
  pts=round(number(need(r,['pts','points','team_pts','team_points'],ctx)))
  # A strict home flag is necessary to avoid accidentally reversing teams.
  hv=field(r,['is_home','home','home_away','venue','ha','matchup'])
  if hv is None:raise ValueError(f'No home/away flag at {ctx}')
  value=str(hv).strip().lower()
  if value in ('1','true','h','home','vs','vs.'): ishome=True
  elif value in ('0','false','a','away','@'): ishome=False
  elif ' vs. ' in value or ' vs ' in value:ishome=True
  elif ' @ ' in value:ishome=False
  else:raise ValueError(f'Ambiguous home/away {hv!r} at {ctx}')
  # Possession field can be either game estimate or team estimate. Do not silently use zero.
  poss=field(r,['poss_est','estimated_possessions','est_possessions','possessions','poss'])
  if poss is None:
   fga=number(need(r,['fga','fg_attempts'],ctx));fta=number(need(r,['fta','ft_attempts'],ctx));oreb=number(need(r,['oreb','off_reb','offensive_rebounds'],ctx)); tov=number(need(r,['tov','turnovers','to'],ctx))
   poss=fga+0.44*fta-oreb+tov
  poss=number(poss)
  if not 55<=poss<=150:raise ValueError(f'Impossible possessions {poss} at {ctx}')
  if not 40<=pts<=200:raise ValueError(f'Implausible points {pts} at {ctx}')
  games[str(gid)].append(dict(date=day,team=t,home=ishome,points=pts,poss=round(poss,3)))
 out=[]
 for gid,pair in games.items():
  if len(pair)!=2 or sum(x['home'] for x in pair)!=1:raise ValueError(f'Game {gid} lacks exactly one home and away row: {pair}')
  a=next(x for x in pair if not x['home']);h=next(x for x in pair if x['home'])
  if a['team']==h['team'] or a['date']!=h['date']:raise ValueError(f'Game {gid} team/date mismatch: {pair}')
  out.append(dict(date=h['date'],away=a['team'],home=h['team'],away_score=a['points'],home_score=h['points'],away_possessions=a['poss'],home_possessions=h['poss']))
 out.sort(key=lambda x:(x['date'],x['home'],x['away']))
 seen=set()
 for g in out:
  for t in (g['home'],g['away']):
   k=(g['date'],t)
   if k in seen:raise ValueError(f'Multiple games for {k}; possible wrong date or double-recording')
   seen.add(k)
 if not 1000<=len(out)<=1300:raise ValueError(f'Unexpected game count {len(out)}; check regular-season coverage and schema')
 expected_start=int('20'+season[:2]);expected_end=expected_start+1
 if any(not (g['date'].startswith(str(expected_start)+'-') or g['date'].startswith(str(expected_end)+'-')) for g in out):raise ValueError('Rows appear outside expected NBA season')
 return out

def priors_from_games(games,season):
 stats=defaultdict(lambda:[0,0,0,0,0])
 for g in games:
  h=stats[g['home']];a=stats[g['away']]
  for s,p,allow,poss,opp in ((h,g['home_score'],g['away_score'],g['home_possessions'],g['away_possessions']),(a,g['away_score'],g['home_score'],g['away_possessions'],g['home_possessions'])):
   s[0]+=p;s[1]+=allow;s[2]+=poss;s[3]+=opp;s[4]+=1
 if len(stats)!=30:raise ValueError(f'Expected 30 teams in prior season, got {len(stats)}')
 out=[];end='20'+season[-2:]+'-06-30'
 for t,s in sorted(stats.items()):
  if s[4]<70:raise ValueError(f'Insufficient games for prior {t}: {s[4]}')
  out.append(dict(team=t,off_rtg=round(100*s[0]/s[2],3),def_rtg=round(100*s[1]/s[3],3),pace=round(s[2]/s[4],3),games=s[4],as_of=end,sos_off_adj=0,sos_def_adj=0))
 return out

def main():
 p=argparse.ArgumentParser();p.add_argument('--season',required=True,choices=['2022_23','2023_24','2024_25','2025_26']);p.add_argument('--out-dir',default='generated/nba_history');p.add_argument('--timeout',type=int,default=35);a=p.parse_args()
 out=Path(a.out_dir);out.mkdir(parents=True,exist_ok=True)
 year=a.season.replace('_','-'); diagnostic={'phase':'NBA 1.6A.1','season':a.season,'source':SOURCE,'source_license':'CC BY 4.0','state':'FAILED','produced':[]}
 try:
  game_name=f'{year}-team-game-results.csv';raw,sha,cols=download(game_name,a.timeout)
  diagnostic.update(file=game_name,raw_rows=len(raw),source_sha256=sha,source_columns=cols)
  games=process_games(raw,a.season);prior=priors_from_games(games,a.season)
  gp=out/f'nba_games_{a.season}.csv'; pp=out/f'nba_{a.season}_team_ratings_baseline.csv'
  write(gp,games,GAMES_COLS);write(pp,prior,PRIOR_COLS)
  diagnostic.update(state='VALIDATED',games=len(games),teams=len(prior),produced=[str(gp),str(pp)],limitations=['Estimated possessions, not possession-level tracked','Historical ratings are raw, SOS offset zero, not true adjusted priors','Data is a third-party republishing of NBA data; confirm date/game count with independent source'])
 except Exception as exc:
  diagnostic.update(error=type(exc).__name__+': '+str(exc))
  print(diagnostic['error'],file=sys.stderr)
 finally:
  (out/'nba_1_6a_1_diagnostic.json').write_text(json.dumps(diagnostic,indent=2),encoding='utf-8')
  print(json.dumps(diagnostic,indent=2))
 if diagnostic['state']!='VALIDATED':raise SystemExit(2)
if __name__=='__main__':main()
