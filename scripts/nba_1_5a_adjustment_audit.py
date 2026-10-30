#!/usr/bin/env python3
"""NBA 1.5A: leakage-free, four-way rating adjustment audit.
Read only existing CSVs; writes reports under backtests/nba_1_5a.
"""
import argparse,csv,json,sys,statistics
from collections import defaultdict
from datetime import datetime,timezone
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parent))
from nba_1_4_backtest import load_priors,load_games,predict,report

def variants(priors,history,shrink):
    sums=defaultdict(lambda:{'n':0,'rawO':0.,'rawD':0.,'sosO':0.,'sosD':0.,'pace':0.})
    for g in history:
        a,h=g['away'],g['home'];pa,ph=priors[a],priors[h]
        ao=g['as']*100/g['ap'];ho=g['hs']*100/g['hp']
        for team,off,def_allowed,poss,opp in ((a,ao,ho,g['ap'],ph),(h,ho,ao,g['hp'],pa)):
            t=sums[team];p=priors[team]
            t['n']+=1;t['rawO']+=off-p['off'];t['rawD']+=def_allowed-p['def']
            t['sosO']+=115-opp['def'];t['sosD']+=115-opp['off'];t['pace']+=poss
    out={k:{team:dict(p) for team,p in priors.items()} for k in ('baseline','form','opponent','full')}
    for team,s in sums.items():
        n=s['n'];w=n/(n+shrink);p=priors[team]
        for kind in ('form','opponent','full'):
            raw=kind in ('form','full');opp=kind in ('opponent','full')
            out[kind][team]['off']=p['off']+w*((s['rawO'] if raw else 0)+(s['sosO'] if opp else 0))/n
            out[kind][team]['def']=p['def']+w*((s['rawD'] if raw else 0)+(s['sosD'] if opp else 0))/n
            # Pace belongs to current-form update, not SOS-only model.
            if raw: out[kind][team]['pace']=p['pace']+w*(s['pace']/n-p['pace'])
    return out

def write_csv(path,rows,columns):
    with path.open('w',newline='',encoding='utf-8') as f:
        w=csv.DictWriter(f,fieldnames=columns);w.writeheader();w.writerows(rows)

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument('--games',default='data/nba_games_2026_27.csv')
    ap.add_argument('--priors',default='data/nba_2025_26_team_ratings_baseline.csv')
    ap.add_argument('--out',default='backtests/nba_1_5a')
    ap.add_argument('--shrink',type=float,default=10)
    ap.add_argument('--home-points',type=float,default=2)
    ap.add_argument('--min-prior-games',type=int,default=0)
    args=ap.parse_args()
    if args.shrink<0 or args.min_prior_games<0:ap.error('negative value not allowed')
    prior=load_priors(args.priors);games=load_games(args.games,prior)
    if not games:raise SystemExit('No games; no audit created')
    history=[];rows=[]
    for day in sorted({g['date'] for g in games}):
        day_games=[g for g in games if g['date']==day]
        rs=variants(prior,history,args.shrink)
        count=defaultdict(int)
        for g in history:count[g['away']]+=1;count[g['home']]+=1
        for g in day_games:
            a,h=g['away'],g['home']
            if min(count[a],count[h])<args.min_prior_games:continue
            r={'date':day.isoformat(),'away':a,'home':h,'away_prior_games':count[a],'home_prior_games':count[h],
               'actual_margin':g['hs']-g['as'],'actual_total':g['hs']+g['as']}
            for k in rs:
                m,t=predict(rs[k],a,h,args.home_points)
                r[k+'_margin']=round(m,5);r[k+'_total']=round(t,5)
            r['raw_form_margin_impact']=round(r['form_margin']-r['baseline_margin'],5)
            r['sos_only_margin_impact']=round(r['opponent_margin']-r['baseline_margin'],5)
            r['full_minus_form_margin_impact']=round(r['full_margin']-r['form_margin'],5)
            r['full_margin_shift']=round(r['full_margin']-r['baseline_margin'],5)
            r['baseline_correct']=int((r['baseline_margin']>0)==(r['actual_margin']>0))
            r['full_correct']=int((r['full_margin']>0)==(r['actual_margin']>0))
            r['winner_flip']=int((r['baseline_margin']>0)!=(r['full_margin']>0))
            rows.append(r)
        history.extend(day_games)
    if not rows:raise SystemExit('No eligible test games; no audit created')
    comparison={k:report(rows,k) for k in ('baseline','form','opponent','full')}
    flips=[r for r in rows if r['winner_flip']]
    summary={'version':'NBA 1.5A adjustment audit','generated_at':datetime.now(timezone.utc).isoformat(),
       'method':'walk-forward: strict earlier-date history; identical scores and HCA for all variants',
       'source_games':len(games),'tested_games':len(rows),'first_date':rows[0]['date'],'last_date':rows[-1]['date'],
       'shrink_denominator':args.shrink,'home_court_points':args.home_points,
       'variants':{'baseline':'2025-26 priors only','form':'shrunk unadjusted current-form changes + pace',
                   'opponent':'shrunk opponent-prior corrections only (no pace change)',
                   'full':'shrunk current-form + opponent-prior corrections + pace; matches NBA 1.4 adjusted formula'},
       'results':comparison,'winner_flips':len(flips),
       'baseline_correct_full_wrong':sum(r['baseline_correct'] and not r['full_correct'] for r in flips),
       'baseline_wrong_full_correct':sum(r['full_correct'] and not r['baseline_correct'] for r in flips),
       'warnings':['Four-model decomposition is diagnostic and not four independent tuned betting models.',
                   'The SOS-only model removes current form and pace; this is not independently calibrated strength-of-schedule.',
                   '53-game or similarly small samples cannot establish predictive improvement.',
                   '2025-26 priors must predate every evaluation game; market lines and ROI were not tested.']}
    out=Path(args.out);out.mkdir(parents=True,exist_ok=True)
    cols=list(rows[0]);write_csv(out/'nba_1_5a_game_audit.csv',rows,cols)
    write_csv(out/'nba_1_5a_winner_flips.csv',flips,cols)
    (out/'nba_1_5a_report.json').write_text(json.dumps(summary,indent=2)+'\n')
    print(json.dumps(summary,indent=2));print('Reports:',out)
if __name__=='__main__':main()
