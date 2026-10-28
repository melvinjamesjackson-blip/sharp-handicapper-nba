#!/usr/bin/env python3
"""NBA 1.4 leakage-free backtest; matches NBA 1.3.1 scoring and rating equations.
Requires the pre-existing automated output data/nba_games_2026_27.csv.
No network requests, no data writes to the live website.
"""
import argparse,csv,json,math,pathlib,statistics
from collections import defaultdict
from datetime import date,datetime,timezone

REQUIRED=('date','away','home','away_score','home_score','away_possessions','home_possessions')
ALIASES={'GS':'GSW','NY':'NYK','NO':'NOP','SA':'SAS','PHO':'PHX','UTAH':'UTA','WSH':'WAS'}
def abbr(s):
    s=s.strip().upper()
    return ALIASES.get(s,s)
def load_priors(path):
    with open(path,encoding='utf-8-sig',newline='') as f: rows=list(csv.DictReader(f))
    p={}
    for r in rows:
        k=abbr(r['team']);p[k]={'off':float(r['off_rtg']),'def':float(r['def_rtg']),'pace':float(r['pace'])}
    if len(p)!=30: raise ValueError('Expected 30 baseline teams; got '+str(len(p)))
    return p

def load_games(path,prior):
    with open(path,encoding='utf-8-sig',newline='') as f:
        reader=csv.DictReader(f)
        if not reader.fieldnames or any(c not in reader.fieldnames for c in REQUIRED):
            raise ValueError('Game CSV missing columns; expected: '+','.join(REQUIRED))
        games=[];seen=set()
        for i,r in enumerate(reader,2):
            try:
                d=date.fromisoformat(r['date']);a=abbr(r['away']);h=abbr(r['home']);
                ap=float(r['away_possessions']);hp=float(r['home_possessions']);a_score=float(r['away_score']);h_score=float(r['home_score'])
                assert a in prior and h in prior and a!=h
                assert 65<=ap<=130 and 65<=hp<=130 and 40<=a_score<=200 and 40<=h_score<=200
                key=(d,a,h)
                assert key not in seen
                seen.add(key)
                games.append({'date':d,'away':a,'home':h,'ap':ap,'hp':hp,'as':a_score,'hs':h_score})
            except Exception as e: raise ValueError(f'Bad or duplicate game at CSV row {i}: {r}') from e
    return sorted(games,key=lambda g:(g['date'],g['away'],g['home']))

def ratings(prior,previous,shrink=10):
    """Same per-game residuals + n/(n+10) shrinkage as NBA 1.3.1."""
    summary=defaultdict(lambda:{'n':0,'o':0.,'d':0.,'p':0.})
    for g in previous:
        a,h=g['away'],g['home'];pa,ph=prior[a],prior[h]
        ra=g['as']*100/g['ap']-(pa['off']+ph['def']-115)
        rh=g['hs']*100/g['hp']-(ph['off']+pa['def']-115)
        for k,o,d,p in ((a,ra,rh,g['ap']),(h,rh,ra,g['hp'])):
            s=summary[k];s['n']+=1;s['o']+=o;s['d']+=d;s['p']+=p
    r={k:dict(v) for k,v in prior.items()}
    for k,s in summary.items():
        n=s['n'];w=n/(n+shrink);base=prior[k]
        r[k]={'off':base['off']+w*s['o']/n,
              'def':base['def']+w*s['d']/n,
              'pace':base['pace']+w*(s['p']/n-base['pace'])}
    return r

def predict(r,a,h,hca):
    away,home=r[a],r[h]
    pace=(away['pace']+home['pace'])/2
    away_score=pace*(away['off']+home['def']-115)/100-hca/2
    home_score=pace*(home['off']+away['def']-115)/100+hca/2
    return home_score-away_score,home_score+away_score

def report(rows,key):
    if not rows:return {'games':0,'margin_mae':None,'total_mae':None,'winner_accuracy':None,'margin_bias':None,'total_bias':None}
    er=[r[f'{key}_margin']-r['actual_margin'] for r in rows]
    et=[r[f'{key}_total']-r['actual_total'] for r in rows]
    winners=[r for r in rows if r['actual_margin']!=0 and r[f'{key}_margin']!=0]
    return {'games':len(rows),'margin_mae':round(statistics.mean(abs(x) for x in er),3),
            'total_mae':round(statistics.mean(abs(x) for x in et),3),
            'winner_accuracy':round(sum((r[f'{key}_margin']>0)==(r['actual_margin']>0) for r in winners)/len(winners),4) if winners else None,
            'margin_bias':round(statistics.mean(er),3),'total_bias':round(statistics.mean(et),3)}

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--games',default='data/nba_games_2026_27.csv');parser.add_argument('--priors',default='data/nba_2025_26_team_ratings_baseline.csv');parser.add_argument('--out',default='backtests/nba_1_4');parser.add_argument('--home-points',type=float,default=2.);parser.add_argument('--shrink',type=float,default=10.);parser.add_argument('--min-prior-games',type=int,default=0)
    args=parser.parse_args()
    if args.shrink<0 or args.min_prior_games<0: parser.error('shrink and min-prior-games must be nonnegative')
    p=load_priors(args.priors);g=load_games(args.games,p)
    if not g:raise SystemExit('No game rows; no report created')
    result=[];eligible_prior=[]
    dates=sorted(set(x['date'] for x in g))
    for d in dates:
        on_day=[x for x in g if x['date']==d]
        updated=ratings(p,eligible_prior,args.shrink)
        counts=defaultdict(int)
        for old in eligible_prior:
            counts[old['away']]+=1;counts[old['home']]+=1
        for x in on_day:
            if min(counts[x['away']],counts[x['home']])<args.min_prior_games:continue
            bmargin,btotal=predict(p,x['away'],x['home'],args.home_points)
            amargin,atotal=predict(updated,x['away'],x['home'],args.home_points)
            result.append({'date':d.isoformat(),'away':x['away'],'home':x['home'],'away_prior_games':counts[x['away']],'home_prior_games':counts[x['home']],
                           'actual_margin':round(x['hs']-x['as'],4),'actual_total':round(x['hs']+x['as'],4),
                           'baseline_margin':round(bmargin,5),'baseline_total':round(btotal,5),
                           'adjusted_margin':round(amargin,5),'adjusted_total':round(atotal,5)})
        eligible_prior.extend(on_day) # update AFTER all same-date predictions
    output=pathlib.Path(args.out);output.mkdir(parents=True,exist_ok=True)
    comparison={'baseline':report(result,'baseline'),'adjusted':report(result,'adjusted')}
    summary={'version':'NBA 1.4','generated_at':datetime.now(timezone.utc).isoformat(),'evaluation_method':'walk-forward; all same-day games excluded before rating update','data_cutoff':'game date strictly before prediction date','total_source_games':len(g),'tested_games':len(result),'date_range':[dates[0].isoformat(),dates[-1].isoformat()], 'home_court_points':args.home_points,'shrink_denominator':args.shrink,'min_prior_games':args.min_prior_games,'results':comparison,'notes':['Historical 2025-26 prior is fixed and not trained on evaluation games.','No closing market lines provided; ATS ROI and betting edge cannot be assessed.','Short early-season history may produce highly unstable accuracy.','Opponent-adjusted variant is one-pass opponent-prior residual adjustment, not an iterative or calibrated solution.']}
    with open(output/'nba_1_4_backtest_rows.csv','w',newline='',encoding='utf-8') as f:
        if result:
            w=csv.DictWriter(f,fieldnames=list(result[0]));w.writeheader();w.writerows(result)
        else:f.write('date,away,home\n')
    with open(output/'nba_1_4_report.json','w',encoding='utf-8') as f:json.dump(summary,f,indent=2)
    print(json.dumps(summary,indent=2))
    print('Saved',output)
if __name__=='__main__':main()
