#!/usr/bin/env python3
"""NBA 1.5C offline integrity check and forward-only rolling evaluation.
Does not overwrite live data or adjust website ratings. Python standard library only.
"""
import argparse,csv,json,sys,math
from collections import defaultdict
from datetime import date,datetime,timezone
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parent))
from nba_1_4_backtest import load_priors,abbr,predict,report,REQUIRED
from nba_1_5a_adjustment_audit import variants

MODELS=('baseline','form','opponent','full')

def write_csv(path,rows,headers):
    with path.open('w',newline='',encoding='utf-8') as f:
        w=csv.DictWriter(f,fieldnames=headers,extrasaction='ignore');w.writeheader();w.writerows(rows)

def examine(path,priors):
    with open(path,encoding='utf-8-sig',newline='') as f:
        rdr=csv.DictReader(f)
        if not rdr.fieldnames or any(c not in rdr.fieldnames for c in REQUIRED):
            raise ValueError('Missing columns: '+', '.join(REQUIRED))
        rows=list(rdr)
    parsed={};bad=[];gamekeys=defaultdict(list);dayteams=defaultdict(list)
    for rownum,r in enumerate(rows,2):
        try:
            d=date.fromisoformat(r['date']);a=abbr(r['away']);h=abbr(r['home'])
            ap=float(r['away_possessions']);hp=float(r['home_possessions'])
            away=float(r['away_score']);home=float(r['home_score'])
            if a not in priors or h not in priors or a==h:raise ValueError('invalid team pairing')
            if not all(math.isfinite(v) for v in (ap,hp,away,home)):raise ValueError('nonfinite statistics')
            if not (65<=ap<=130 and 65<=hp<=130 and 40<=away<=200 and 40<=home<=200):raise ValueError('implausible score or possessions')
            g={'date':d,'away':a,'home':h,'ap':ap,'hp':hp,'as':away,'hs':home}
            parsed[rownum]=g
            gamekeys[(d,a,h)].append(rownum)
            dayteams[(d,a)].append(rownum);dayteams[(d,h)].append(rownum)
        except Exception as exc:
            bad.append({'csv_row':rownum,'date':r.get('date',''),'away':r.get('away',''),'home':r.get('home',''),'reason':'invalid_row: '+str(exc)})
    problems=defaultdict(set)
    for key,lines in gamekeys.items():
        if len(lines)>1:
            for line in lines: problems[line].add('duplicate_exact_game')
    for key,lines in dayteams.items():
        if len(lines)>1:
            for line in lines:problems[line].add('team_multiple_games_same_date: '+key[1])
    for line,reasons in sorted(problems.items()):
        g=parsed[line]
        bad.append({'csv_row':line,'date':g['date'].isoformat(),'away':g['away'],'home':g['home'],'reason':'; '.join(sorted(reasons))})
    flagged=set(problems)
    games=sorted((g for line,g in parsed.items() if line not in flagged),key=lambda g:(g['date'],g['away'],g['home']))
    return games,sorted(bad,key=lambda r:(r['csv_row'],r['reason'])),len(rows),sorted(flagged)

def evaluate(prior,games,hca,shrink,min_prior):
    history=[];pred=[]
    for day in sorted({g['date'] for g in games}):
        ratings=variants(prior,history,shrink)
        counts=defaultdict(int)
        for g in history:counts[g['away']]+=1;counts[g['home']]+=1
        for g in (x for x in games if x['date']==day):
            a,h=g['away'],g['home']
            if min(counts[a],counts[h])<min_prior:continue
            row={'date':day.isoformat(),'away':a,'home':h,'actual_margin':g['hs']-g['as'], 'actual_total':g['hs']+g['as'], 'away_prior_games':counts[a],'home_prior_games':counts[h]}
            for model in MODELS:
                m,t=predict(ratings[model],a,h,hca)
                row[model+'_margin']=round(m,6);row[model+'_total']=round(t,6)
            pred.append(row)
        history.extend(x for x in games if x['date']==day)
    return pred

def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--games',default='data/nba_games_2026_27.csv')
    ap.add_argument('--priors',default='data/nba_2025_26_team_ratings_baseline.csv')
    ap.add_argument('--out',default='backtests/nba_1_5c')
    ap.add_argument('--quarantine',action='store_true',help='Explicitly exclude ALL conflicting rows and analyze only clean data; report remains flagged')
    ap.add_argument('--min-train-dates',type=int,default=4)
    ap.add_argument('--min-prior-games',type=int,default=0)
    ap.add_argument('--home-points',type=float,default=2.)
    ap.add_argument('--shrink',type=float,default=10.)
    args=ap.parse_args()
    if args.min_train_dates<1 or args.min_prior_games<0 or args.shrink<=0:ap.error('invalid parameter')
    out=Path(args.out);out.mkdir(parents=True,exist_ok=True)
    prior=load_priors(args.priors)
    games,issues,source_count,conflicts=examine(args.games,prior)
    write_csv(out/'nba_1_5c_integrity_issues.csv',issues,['csv_row','date','away','home','reason'])
    integrity={'source_rows':source_count,'issues':len(issues),'rows_with_team_date_conflict':len(conflicts),'clean_rows':len(games),'policy':'STRICT unless --quarantine explicitly used', 'quarantine_requested':args.quarantine,'external_schedule_verified':False}
    (out/'nba_1_5c_integrity.json').write_text(json.dumps(integrity,indent=2)+'\n')
    if issues and not args.quarantine:
        print('DATA INTEGRITY FAILURE: '+str(len(issues))+' issues. See '+str(out/'nba_1_5c_integrity_issues.csv'))
        print('No calibration results produced. Fix source data or rerun with --quarantine for diagnostic-only results.')
        raise SystemExit(2)
    if not games:raise SystemExit('No clean games remain')
    predictions=evaluate(prior,games,args.home_points,args.shrink,args.min_prior_games)
    dates=sorted({r['date'] for r in predictions})
    if len(dates)<=args.min_train_dates:raise SystemExit('Not enough dates for forward validation')
    folds=[];detail=[]
    # Each future date is an evaluation fold. Earlier days are available to train team ratings;
    # no future dates enter the rating state. Parameters are fixed before evaluation.
    for i in range(args.min_train_dates,len(dates)):
        test_date=dates[i]
        test=[r for r in predictions if r['date']==test_date]
        if not test:continue
        f={'fold':len(folds)+1,'evaluated_date':test_date,'prior_evaluation_dates':i,'games':len(test)}
        for model in MODELS:
            stats=report(test,model)
            for metric in ('margin_mae','total_mae','winner_accuracy','margin_bias','total_bias'):
                f[model+'_'+metric]=stats[metric]
        folds.append(f)
        for r in test:
            row=dict(r);row['fold']=f['fold'];detail.append(row)
    if not folds:raise SystemExit('No evaluation folds')
    agg={m:report(detail,m) for m in MODELS}
    comparison={'status':'DIAGNOSTIC_ONLY' if issues else 'CLEAN_SOURCE_UNVERIFIED_EXTERNALLY',
        'phase':'NBA 1.5C data-integrity and expanding-window walk-forward',
        'generated_at':datetime.now(timezone.utc).isoformat(),
        'integrity':integrity,'first_evaluation_date':folds[0]['evaluated_date'],
        'last_evaluation_date':folds[-1]['evaluated_date'],
        'evaluation_folds':len(folds),'evaluated_games':len(detail),
        'parameters':{'shrink':args.shrink,'hca':args.home_points,'min_train_dates':args.min_train_dates,'min_prior_games':args.min_prior_games},
        'results':agg,'method':'Fixed model variants, each evaluated on a later date using only earlier-day games. No hyperparameter selection performed.',
        'warnings':['Exclusion of flagged rows under --quarantine can bias results; source records must be independently reconciled.',
          'Team/date duplicates cannot establish which game is correct. No external schedule verification performed.',
          'Current early-season sample is small. These are not independent market-odds backtests.',
          'Only prior-season fixed ratings are used as baseline; SOS correction is one-pass opponent prior, not iterative SOS.']}
    write_csv(out/'nba_1_5c_rolling_folds.csv',folds,list(folds[0]))
    write_csv(out/'nba_1_5c_rolling_games.csv',detail,list(detail[0]))
    (out/'nba_1_5c_report.json').write_text(json.dumps(comparison,indent=2)+'\n')
    print(json.dumps(comparison,indent=2))
    print('Reports:',out)
if __name__=='__main__':main()
