#!/usr/bin/env python3
"""NBA 1.5D: offline forward-only model comparison. No website/data writes.
Uses the already installed 1.5C integrity checker and 1.4/1.5A functions.
New iterative variant is an experimental regularized opponent adjustment, not a validated production model.
"""
import argparse,csv,json,sys,statistics
from collections import defaultdict
from datetime import datetime,timezone
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parent))
from nba_1_4_backtest import load_priors,predict,report
from nba_1_5c_integrity_rolling import examine
from nba_1_5a_adjustment_audit import variants

MODELS=('baseline','form_shrink10','full_shrink10','conservative_form_shrink20','iterative_sos_shrink20')

def iterative(priors,history,shrink=20,iterations=6,damping=.65):
    """Fit offense/defense residuals vs opponent strength via ridge-like fixed-point updates.
    Positive defensive rating means worse defense, matching existing model.
    Each rating update uses completed past games only. Pace shrinks toward prior.
    """
    samples=defaultdict(list)
    for g in history:
        a,h=g['away'],g['home']
        ao=100*g['as']/g['ap']; ho=100*g['hs']/g['hp']
        samples[a].append((h,ao,ho,g['ap']))
        samples[h].append((a,ho,ao,g['hp']))
    off={k:0. for k in priors}; deff={k:0. for k in priors}
    # Model measured offensive efficiency = priorOff(team)+priorDef(opp)-115 + teamOffResidual + oppDefResidual
    for _ in range(iterations):
        n_off=off.copy();n_def=deff.copy()
        for team,obs in samples.items():
            n=len(obs); po=priors[team]
            raw_o=sum(own_eff-(po['off']+priors[opp]['def']-115)-deff[opp] for opp,own_eff,opp_eff,poss in obs)/(n+shrink)
            raw_d=sum(opp_eff-(priors[opp]['off']+po['def']-115)-off[opp] for opp,own_eff,opp_eff,poss in obs)/(n+shrink)
            n_off[team]=(1-damping)*off[team]+damping*raw_o
            n_def[team]=(1-damping)*deff[team]+damping*raw_d
        off,deff=n_off,n_def
    result={k:dict(p) for k,p in priors.items()}
    for k,obs in samples.items():
        n=len(obs);w=n/(n+shrink)
        result[k]['off']+=off[k]
        result[k]['def']+=deff[k]
        result[k]['pace']+=w*(sum(x[3] for x in obs)/n-priors[k]['pace'])
    return result

def write_csv(path,rows,headers):
    with path.open('w',encoding='utf-8',newline='') as f:
        w=csv.DictWriter(f,fieldnames=headers,extrasaction='ignore');w.writeheader();w.writerows(rows)

def evaluate(prior,games,hca,min_train_dates,min_prior_games):
    dates=sorted(set(g['date'] for g in games))
    if len(dates)<=min_train_dates:raise ValueError('Too few distinct dates for evaluation')
    history=[];rows=[];folds=[]
    for ix,day in enumerate(dates):
        daygames=[g for g in games if g['date']==day]
        counts=defaultdict(int)
        for g in history:
            counts[g['away']]+=1;counts[g['home']]+=1
        if ix>=min_train_dates:
            v10=variants(prior,history,10)
            v20=variants(prior,history,20)
            models={'baseline':v10['baseline'],'form_shrink10':v10['form'],
                    'full_shrink10':v10['full'],'conservative_form_shrink20':v20['form'],
                    'iterative_sos_shrink20':iterative(prior,history,20)}
            dayrows=[]
            for g in daygames:
                a,h=g['away'],g['home']
                if min(counts[a],counts[h])<min_prior_games:continue
                row={'date':day.isoformat(),'away':a,'home':h,'away_prior_games':counts[a],
                    'home_prior_games':counts[h],'actual_margin':g['hs']-g['as'],'actual_total':g['hs']+g['as']}
                for k in MODELS:
                    m,t=predict(models[k],a,h,hca)
                    row[k+'_margin']=round(m,6);row[k+'_total']=round(t,6)
                dayrows.append(row)
            if dayrows:
                fold={'fold':len(folds)+1,'date':day.isoformat(),'games':len(dayrows)}
                for k in MODELS:
                    r=report(dayrows,k)
                    fold[k+'_spread_mae']=r['margin_mae']
                    fold[k+'_winner_accuracy']=r['winner_accuracy']
                    fold[k+'_total_mae']=r['total_mae']
                folds.append(fold)
                for r in dayrows:r['fold']=fold['fold']
                rows.extend(dayrows)
        history.extend(daygames)
    return rows,folds

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--games',default='data/nba_games_2026_27.csv')
    p.add_argument('--priors',default='data/nba_2025_26_team_ratings_baseline.csv')
    p.add_argument('--out',default='backtests/nba_1_5d')
    p.add_argument('--min-train-dates',type=int,default=4)
    p.add_argument('--min-prior-games',type=int,default=0)
    p.add_argument('--home-points',type=float,default=2.)
    p.add_argument('--holdout-folds',type=int,default=3)
    args=p.parse_args()
    if args.min_train_dates<1 or args.min_prior_games<0 or args.holdout_folds<1:p.error('Invalid date or prior-game settings')
    out=Path(args.out);out.mkdir(parents=True,exist_ok=True)
    priors=load_priors(args.priors)
    games,issues,nsource,_=examine(args.games,priors)
    write_csv(out/'nba_1_5d_integrity_issues.csv',issues,['csv_row','date','away','home','reason'])
    if issues:
        print('STOP: '+str(len(issues))+' integrity issue(s). See nba_1_5d_integrity_issues.csv. No backtest run.')
        raise SystemExit(2)
    rows,folds=evaluate(priors,games,args.home_points,args.min_train_dates,args.min_prior_games)
    if len(folds)<=args.holdout_folds:raise SystemExit('Not enough forward folds for requested holdout')
    ntrain=len(folds)-args.holdout_folds
    train=[r for r in rows if r['fold']<=ntrain]
    hold=[r for r in rows if r['fold']>ntrain]
    all_metrics={k:report(rows,k) for k in MODELS}
    train_metrics={k:report(train,k) for k in MODELS}
    hold_metrics={k:report(hold,k) for k in MODELS}
    # Select only on earlier folds; never promote based on later fold performance.
    pick=min(MODELS,key=lambda k:(train_metrics[k]['margin_mae'], -train_metrics[k]['winner_accuracy'],k))
    summary={'phase':'NBA 1.5D offline conservative-form / iterative-opponent comparison',
       'generated_at':datetime.now(timezone.utc).isoformat(),'status':'EXPERIMENTAL_DO_NOT_DEPLOY',
       'source_games':nsource,'integrity_issues':len(issues),'evaluated_games':len(rows),
       'evaluation_folds':len(folds),'train_folds':ntrain,'holdout_folds':args.holdout_folds,
       'train_games':len(train),'holdout_games':len(hold),
       'model_definitions':{'baseline':'Prior 2025-26 ratings only',
          'form_shrink10':'1.5C current form with n/(n+10) weighting',
          'full_shrink10':'1.5C one-pass form + opponent prior correction',
          'conservative_form_shrink20':'Current form with n/(n+20) weighting',
          'iterative_sos_shrink20':'Experimental iterative ridge-like opponent residual correction with n+20 shrinkage and six damped iterations'},
       'training_selected_by_spread_mae':pick,'training':train_metrics,'later_holdout':hold_metrics,'all_folds':all_metrics,
       'method':'Strict previous-date game history, fixed candidate models. Holdout is last N dates, never used to choose model.',
       'warnings':['Only a handful of dates in one early season; errors are noisy and models not proven better.',
          'Historical prior may contain source or definition uncertainty and source has not been externally independently audited.',
          'Training folds reused for a small fixed model selection; holdout remains one small period.',
          'No betting odds/ROI examined; no positive expected value inferred.',
          'Iterative opponent model is an exploratory approximation, not a production-calibrated SOS rating.']}
    write_csv(out/'nba_1_5d_games.csv',rows,list(rows[0]))
    write_csv(out/'nba_1_5d_folds.csv',folds,list(folds[0]))
    (out/'nba_1_5d_report.json').write_text(json.dumps(summary,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({'status':summary['status'],'games':len(rows),'folds':len(folds),'training_selection':pick,'holdout':hold_metrics},indent=2))
    print('Reports:',out)
if __name__=='__main__':main()
