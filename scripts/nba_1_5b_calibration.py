#!/usr/bin/env python3
"""NBA 1.5B: chronological, pregame-only calibration audit (no live model changes)."""
import argparse,csv,json,sys,statistics,itertools
from collections import defaultdict
from datetime import datetime,timezone
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parent))
from nba_1_4_backtest import load_priors,load_games,predict,report
from nba_1_5a_adjustment_audit import variants

def calculate(prior,games,shrink,form_weight,sos_weight,hca,min_games):
    rows=[]; history=[]
    for d in sorted({g['date'] for g in games}):
        v=variants(prior,history,shrink)
        counts=defaultdict(int)
        for g in history:counts[g['away']]+=1;counts[g['home']]+=1
        r={team:dict(p) for team,p in prior.items()}
        for team,p in prior.items():
            f=v['form'][team];o=v['opponent'][team]
            for field in ('off','def'):
                r[team][field]=p[field]+form_weight*(f[field]-p[field])+sos_weight*(o[field]-p[field])
            r[team]['pace']=p['pace']+form_weight*(f['pace']-p['pace'])
        for g in (g for g in games if g['date']==d):
            a,h=g['away'],g['home']
            if min(counts[a],counts[h])<min_games:continue
            bm,bt=predict(prior,a,h,hca);m,t=predict(r,a,h,hca)
            rows.append({'date':d.isoformat(),'away':a,'home':h,'away_prior_games':counts[a],
              'home_prior_games':counts[h],'actual_margin':g['hs']-g['as'],
              'actual_total':g['hs']+g['as'],'baseline_margin':round(bm,6),
              'baseline_total':round(bt,6),'candidate_margin':round(m,6),
              'candidate_total':round(t,6),
              'winner_flip':int((bm>0)!=(m>0))})
        history.extend(g for g in games if g['date']==d)
    return rows

def metric(rows,prefix):
    return report(rows,prefix)

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument('--games',default='data/nba_games_2026_27.csv')
    ap.add_argument('--priors',default='data/nba_2025_26_team_ratings_baseline.csv')
    ap.add_argument('--out',default='backtests/nba_1_5b')
    ap.add_argument('--hca',type=float,default=2.0)
    ap.add_argument('--min-prior-games',type=int,default=0)
    args=ap.parse_args()
    prior=load_priors(args.priors);games=load_games(args.games,prior)
    dates=sorted({g['date'] for g in games})
    if len(dates)<4:raise SystemExit('Need at least 4 distinct game dates for chronological split')
    # Entire dates are segregated. The latest ~35% are never used for choosing weights.
    cut=max(2,min(len(dates)-2,int(len(dates)*.65)))
    training_dates={d.isoformat() for d in dates[:cut]}
    holdout_dates={d.isoformat() for d in dates[cut:]}
    configurations=list(itertools.product((10,15,20,30),(0,.25,.5,.75,1),(0,.5,1)))
    scores=[];cache={}
    for shrink,form,sos in configurations:
        key=(shrink,form,sos)
        rows=calculate(prior,games,shrink,form,sos,args.hca,args.min_prior_games)
        tr=[r for r in rows if r['date'] in training_dates]
        if not tr:continue
        rm=metric(tr,'candidate');base=metric(tr,'baseline')
        scores.append({'shrink':shrink,'form_weight':form,'sos_weight':sos,'training_games':len(tr),
          'training_margin_mae':rm['margin_mae'],'training_winner_accuracy':rm['winner_accuracy'],
          'training_total_mae':rm['total_mae'],'training_flips':sum(r['winner_flip'] for r in tr)})
        cache[key]=rows
    if not scores:raise SystemExit('No eligible games on training dates')
    # Optimizes TRAINING margin MAE; never peeks at holdout for candidate selection.
    scores.sort(key=lambda r:(r['training_margin_mae'],-(r['training_winner_accuracy'] or 0),r['form_weight']+r['sos_weight']))
    winner=scores[0];key=(winner['shrink'],winner['form_weight'],winner['sos_weight'])
    selected=cache[key]
    holdout=[r for r in selected if r['date'] in holdout_dates]
    train=[r for r in selected if r['date'] in training_dates]
    if not holdout:raise SystemExit('No eligible holdout games; lower min-prior-games')
    # Compare unchanged historical and ORIGINAL full model on identical holdout games.
    original=calculate(prior,games,10,1,1,args.hca,args.min_prior_games)
    original_hold=[r for r in original if r['date'] in holdout_dates]
    assert len(original_hold)==len(holdout)
    summary={'phase':'NBA 1.5B offline calibration',
       'generated_at':datetime.now(timezone.utc).isoformat(),
       'method':'All predictions use prior-day-only games. Grid search selects by training margin MAE; later dates held out.',
       'source_games':len(games),'source_dates':len(dates),
       'train_date_range':[dates[0].isoformat(),dates[cut-1].isoformat()],
       'holdout_date_range':[dates[cut].isoformat(),dates[-1].isoformat()],
       'train_games':len(train),'holdout_games':len(holdout),'candidates_tested':len(scores),
       'selection_criterion':'min training spread MAE; winner accuracy tie-break; lighter adjustments second tie-break',
       'selected':{k:winner[k] for k in ('shrink','form_weight','sos_weight')},
       'training':{'baseline':metric(train,'baseline'),'selected':metric(train,'candidate')},
       'holdout':{'historical':metric(holdout,'baseline'),
                  'original_full_adjusted_shrink10':metric(original_hold,'candidate'),
                  'selected_calibration':metric(holdout,'candidate')},
       'holdout_winner_flips_vs_baseline':sum(r['winner_flip'] for r in holdout),
       'cautions':['Only a few game dates exist early in 2026-27; estimates are unstable.',
           'Date-based holdout is after training, but teams use all earlier games, as a real pregame forecast would.',
           'One test split alone is insufficient to justify live deployment.',
           'SOS is opponent-prior correction, not iterative schedule-adjusted team strength.',
           'No market spreads, salaries, or ROI tested; no betting edge established.']}
    out=Path(args.out);out.mkdir(parents=True,exist_ok=True)
    (out/'nba_1_5b_report.json').write_text(json.dumps(summary,indent=2)+'\n')
    with (out/'nba_1_5b_grid.csv').open('w',newline='') as f:
        w=csv.DictWriter(f,fieldnames=list(scores[0]));w.writeheader();w.writerows(scores)
    with (out/'nba_1_5b_selected_holdout.csv').open('w',newline='') as f:
        w=csv.DictWriter(f,fieldnames=list(holdout[0]));w.writeheader();w.writerows(holdout)
    print(json.dumps(summary,indent=2))
    print('Files:',out)
if __name__=='__main__':main()
