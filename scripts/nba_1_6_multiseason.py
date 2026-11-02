#!/usr/bin/env python3
"""NBA 1.6 TEST ONLY: multi-season forward-only evaluation and coverage audit.
Requires season-specific previous-season priors; never borrows priors from future years.
Works with only current season, and visibly flags missing historical datasets.
"""
import argparse,csv,json,sys,hashlib
from pathlib import Path
from datetime import datetime,timezone
sys.path.insert(0,str(Path(__file__).resolve().parent))
from nba_1_4_backtest import load_priors,report
from nba_1_5c_integrity_rolling import examine
from nba_1_5d_refinement import evaluate,MODELS
SEASONS=['2023_24','2024_25','2025_26','2026_27']

def save_csv(p,records,headers):
    p.parent.mkdir(parents=True,exist_ok=True)
    with p.open('w',newline='',encoding='utf-8') as f:
        w=csv.DictWriter(f,fieldnames=headers,extrasaction='ignore');w.writeheader();w.writerows(records)

def metric(r):
    return {'games':r['games'],'margin_mae':r['margin_mae'],'total_mae':r['total_mae'],'winner_accuracy':r['winner_accuracy'],'margin_bias':r['margin_bias'],'total_bias':r['total_bias']}

def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--data-dir',default='data');ap.add_argument('--out',default='backtests/nba_1_6')
    ap.add_argument('--min-train-dates',type=int,default=4);ap.add_argument('--min-prior-games',type=int,default=0)
    ap.add_argument('--home-points',type=float,default=2.0)
    ap.add_argument('--strict-coverage',action='store_true',help='Exit nonzero unless all 4 seasons have both files and pass integrity')
    args=ap.parse_args()
    if args.min_train_dates<1 or args.min_prior_games<0:ap.error('Invalid parameters')
    data=Path(args.data_dir); out=Path(args.out);out.mkdir(parents=True,exist_ok=True)
    coverage=[];allrows=[];allfolds=[];season_metrics=[];allissues=[];hashes={}
    for season in SEASONS:
        prior_year=str(int(season[:4])-1)+'_'+str(int(season[5:])-1).zfill(2)
        gamespath=data/f'nba_games_{season}.csv'
        priorpath=data/f'nba_{prior_year}_team_ratings_baseline.csv'
        status={'season':season,'games_file':str(gamespath),'priors_file':str(priorpath),'games_found':gamespath.is_file(),'priors_found':priorpath.is_file(),'source_games':0,'clean_games':0,'integrity_issues':0,'evaluated_games':0,'folds':0,'status':''}
        if not gamespath.is_file() or not priorpath.is_file():
            missing=[str(p) for p in (gamespath,priorpath) if not p.is_file()]
            status['status']='MISSING: '+', '.join(missing);coverage.append(status);continue
        try:
            priors=load_priors(str(priorpath))
            games,issues,nrows,_=examine(str(gamespath),priors)
            status['source_games']=nrows;status['clean_games']=len(games);status['integrity_issues']=len(issues)
            for iss in issues:allissues.append({'season':season,**iss})
            if issues:
                status['status']='INVALID_SOURCE';coverage.append(status);continue
            rows,folds=evaluate(priors,games,args.home_points,args.min_train_dates,args.min_prior_games)
            if not rows or not folds:
                status['status']='INSUFFICIENT_HISTORY';coverage.append(status);continue
            status['evaluated_games']=len(rows);status['folds']=len(folds);status['status']='EVALUATED'
            for r in rows:r['season']=season
            for f in folds:f['season']=season
            allrows.extend(rows);allfolds.extend(folds)
            for k in MODELS:season_metrics.append({'season':season,'model':k,**metric(report(rows,k))})
            for p in (gamespath,priorpath):hashes[str(p)]=hashlib.sha256(p.read_bytes()).hexdigest()
        except Exception as e:
            status['status']='ERROR: '+str(e)[:300]
        coverage.append(status)
    save_csv(out/'nba_1_6_coverage.csv',coverage,list(coverage[0]))
    save_csv(out/'nba_1_6_integrity_issues.csv',allissues,['season','csv_row','date','away','home','reason'])
    if allrows:
        save_csv(out/'nba_1_6_games.csv',allrows,list(allrows[0]))
        save_csv(out/'nba_1_6_folds.csv',allfolds,list(allfolds[0]))
        save_csv(out/'nba_1_6_season_metrics.csv',season_metrics,list(season_metrics[0]))
    aggregate={k:metric(report(allrows,k)) for k in MODELS} if allrows else {}
    complete=sum(x['status']=='EVALUATED' for x in coverage)==len(SEASONS)
    summary={'phase':'NBA 1.6 multi-season validation (offline test only)',
             'generated_at':datetime.now(timezone.utc).isoformat(),'status':'FULL_COVERAGE_EXPERIMENTAL' if complete else 'PARTIAL_COVERAGE_DO_NOT_PROMOTE',
             'expected_seasons':SEASONS,'evaluated_seasons':[x['season'] for x in coverage if x['status']=='EVALUATED'],
             'source_coverage':coverage,'aggregate':aggregate,'file_sha256':hashes,
             'method':'Per-season chronological walk forward; each game only uses earlier dates from same season and the preceding-season fixed prior. Models remain fixed; no holdout optimization or website writes.',
             'limitations':['Per-season priors must be independently produced, and definitions consistent across seasons.',
                           'No market closing spreads, ROI or calibration probabilities tested.',
                           'Only dataset-level consistency checks, not external schedule verification.',
                           'Do not treat current-season-only output as historical validation.']}
    (out/'nba_1_6_report.json').write_text(json.dumps(summary,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({'status':summary['status'],'evaluated_seasons':summary['evaluated_seasons'],'total_games':len(allrows),'coverage':[(c['season'],c['status']) for c in coverage]},indent=2))
    if args.strict_coverage and not complete:raise SystemExit(2)
    if not allrows:raise SystemExit(2)
if __name__=='__main__':main()
