#!/usr/bin/env python3
"""NBA 1.6A: generate historical games and PRIOR-SEASON team ratings with nba_api.
Offline/test-only. Requires pip install nba_api pandas. Does not touch website.
All date conversion uses game date from league log, not UTC ingestion timestamp.
"""
import argparse,csv,datetime,json,time
from collections import defaultdict
from pathlib import Path

SEASONS={'2022_23':'2022-23','2023_24':'2023-24','2024_25':'2024-25','2025_26':'2025-26'}
TEAMS={'ATL','BOS','BKN','CHA','CHI','CLE','DAL','DEN','DET','GSW','HOU','IND','LAC','LAL','MEM','MIA','MIL','MIN','NOP','NYK','OKC','ORL','PHI','PHX','POR','SAC','SAS','TOR','UTA','WAS'}
GAMES_COLS=['date','away','home','away_score','home_score','away_possessions','home_possessions']
PRIOR_COLS=['team','off_rtg','def_rtg','pace','games','as_of','sos_off_adj','sos_def_adj']
def write(path,rows,columns):
 path.parent.mkdir(parents=True,exist_ok=True)
 with path.open('w',newline='',encoding='utf8') as f:
  w=csv.DictWriter(f,fieldnames=columns);w.writeheader();w.writerows(rows)
def process(raw,season):
 # NBA API leaguegamelog provides one row per team per contest, with home/away marked in MATCHUP.
 by_game=defaultdict(list)
 for r in raw:
  gid=str(r['GAME_ID']); abbr=str(r['TEAM_ABBREVIATION']).upper(); matchup=str(r['MATCHUP'])
  if abbr not in TEAMS or not gid.startswith('002'): continue # regular season only
  date=datetime.datetime.strptime(str(r['GAME_DATE'])[:10],'%Y-%m-%d').date().isoformat()
  for k in ('PTS','FGA','FTA','OREB','TOV'):
   if r.get(k) is None or str(r[k]).strip()=='':raise ValueError(f'Missing {k} for {gid}, {abbr}')
  poss=float(r['FGA'])+0.44*float(r['FTA'])-float(r['OREB'])+float(r['TOV'])
  if not 55<=poss<=150:raise ValueError(f'Implausible possessions {gid} {abbr}: {poss}')
  home=('@' not in matchup)
  if not (' vs. ' in matchup or ' @ ' in matchup):raise ValueError(f'Unrecognized MATCHUP {matchup}')
  by_game[gid].append(dict(date=date,team=abbr,home=home,points=int(float(r['PTS'])),poss=round(poss,2)))
 games=[]
 for gid,rs in sorted(by_game.items()):
  if len(rs)!=2 or {x['home'] for x in rs}!={True,False}:raise ValueError(f'Invalid teams for {gid}: {rs}')
  a=next(x for x in rs if not x['home']);h=next(x for x in rs if x['home'])
  if a['date']!=h['date']:raise ValueError(f'Date mismatch {gid}')
  games.append(dict(date=h['date'],away=a['team'],home=h['team'],away_score=a['points'],home_score=h['points'],away_possessions=a['poss'],home_possessions=h['poss']))
 games.sort(key=lambda r:(r['date'],r['home'],r['away']))
 used=set()
 for g in games:
  for t in (g['home'],g['away']):
   key=(g['date'],t)
   if key in used:raise ValueError(f'Duplicate team on date {key}')
   used.add(key)
 if len(games)<500:raise ValueError(f'Too few regular-season games: {len(games)}; refusing to create priors')
 t=defaultdict(lambda:dict(pts=0.0,allowed=0.0,poss=0.0,opp_poss=0.0,games=0))
 for g in games:
  for team,pts,allowed,poss,opp_poss in ((g['home'],g['home_score'],g['away_score'],g['home_possessions'],g['away_possessions']),(g['away'],g['away_score'],g['home_score'],g['away_possessions'],g['home_possessions'])):
   s=t[team];s['pts']+=pts;s['allowed']+=allowed;s['poss']+=poss;s['opp_poss']+=opp_poss;s['games']+=1
 rows=[]
 end=f"20{season[-2:]}-06-30"
 for team,s in sorted(t.items()):
  rows.append(dict(team=team,off_rtg=round(100*s['pts']/s['poss'],3),def_rtg=round(100*s['allowed']/s['opp_poss'],3),pace=round(s['poss']/s['games'],3),games=s['games'],as_of=end,sos_off_adj=0,sos_def_adj=0))
 if len(rows)!=30:raise ValueError(f'Expected 30 teams, got {len(rows)}')
 return games,rows

def main():
 p=argparse.ArgumentParser();p.add_argument('--season',choices=list(SEASONS),required=True);p.add_argument('--out-dir',default='data');p.add_argument('--force',action='store_true');args=p.parse_args()
 out=Path(args.out_dir); gp=out/f'nba_games_{args.season}.csv'; pp=out/f'nba_{args.season}_team_ratings_baseline.csv'
 if not args.force and (gp.exists() or pp.exists()):raise SystemExit('Files exist: refuse overwrite; use --force explicitly for offline regeneration')
 from nba_api.stats.endpoints import leaguegamelog
 season=SEASONS[args.season]
 print('Fetching NBA official stats game logs:',season,flush=True)
 obj=leaguegamelog.LeagueGameLog(season=season,season_type_all_star='Regular Season',player_or_team_abbreviation='T',timeout=80)
 raw=obj.get_data_frames()[0].to_dict(orient='records')
 games,priors=process(raw,args.season)
 # build before moving any files; report provenance and formula limitations
 write(gp,games,GAMES_COLS);write(pp,priors,PRIOR_COLS)
 print(json.dumps({'season':args.season,'games':len(games),'teams':len(priors),'created':[str(gp),str(pp)],'note':'Priors are raw season efficiency derived from NBA official box-score four-factor possession estimate; SOS adjustments set to zero, not calculated.'},indent=2))
if __name__=='__main__':main()
