"""Joint time-block resampling for a disclosed finite strategy family.

Exploratory under stationarity/mixing assumptions, not a DSR/SPA implementation
and not a correction for undisclosed historical researcher choices.
"""
import math
import random
import statistics


def equity_returns(curve, capital):
    if not math.isfinite(capital) or capital<=0: raise ValueError('positive capital required')
    dates=[]; values=[]; previous=capital
    for row in curve:
        day=row['timestamp'][:10]; current=row['equity']
        if dates and day<=dates[-1]: raise ValueError('unique ordered daily curve required')
        if not math.isfinite(current) or current<=0: raise ValueError('positive finite equity required')
        dates.append(day); values.append(current/previous-1); previous=current
    return dates,values


def _quantile(values, p):
    x=(len(values)-1)*p; lo=int(x); hi=min(lo+1,len(values)-1)
    return values[lo]+(values[hi]-values[lo])*(x-lo)


def joint_block_test(series, *, block=20, replications=2000, seed=20260908, alpha=.05):
    if not series or type(block) is not int or block<1 or type(replications) is not int or replications<100:
        raise ValueError('nonempty family, positive blocks, >=100 replications required')
    if not 0<alpha<1: raise ValueError('alpha must be in (0,1)')
    names=sorted(series); n=len(series[names[0]])
    if any(len(series[k])!=n or any(not math.isfinite(v) for v in series[k]) for k in names):
        raise ValueError('finite aligned equal-length return paths required')
    if n<max(3,2*block): return dict(status='unavailable_short_history',candidates={},family_size=len(names))
    means={k:statistics.mean(series[k]) for k in names}
    valid={k:statistics.pvariance(series[k])>1e-30 for k in names}
    prefixes={}
    for k in names:
        p=[0.0]
        for v in list(series[k])*2: p.append(p[-1]+v)
        prefixes[k]=p
    rng=random.Random(seed); samples={k:[] for k in names}; maxima=[]
    for _ in range(replications):
        totals={k:0.0 for k in names}; remaining=n
        while remaining:
            start=rng.randrange(n); length=min(block,remaining); remaining-=length
            for k in names: totals[k]+=prefixes[k][start+length]-prefixes[k][start]
        values={k:totals[k]/n for k in names}
        for k in names: samples[k].append(values[k])
        maxima.append(max([0.0]+[values[k]-means[k] for k in names if valid[k]]))
    result={}; tail=alpha/(2*len(names))
    for k in names:
        ordered=sorted(samples[k])
        result[k]=dict(status='exploratory' if valid[k] else 'unavailable_constant_returns',
            mean_daily_net_return=means[k],active_return_sessions=sum(abs(v)>1e-15 for v in series[k]),
            family_adjusted_p=(1+sum(v>=means[k] for v in maxima))/(replications+1) if valid[k] else None,
            bonferroni_mean_ci=[_quantile(ordered,tail),_quantile(ordered,1-tail)] if valid[k] else None)
    return dict(status='exploratory',method='joint_centered_circular_block_max_mean',
        family_size=len(names),sessions=n,block_sessions=block,replications=replications,seed=seed,alpha=alpha,
        candidates=result,complete_historical_trial_correction=False,
        warning='Same sampled dates across candidates preserve their cross-correlation. Approximate stationarity/mixing assumptions; not a proof, not DSR/SPA, not a complete historical trial correction. Mean CI is daily arithmetic, not CAGR.')


def choose_development(summaries, *, minimum_trades=30, minimum_symbols=5):
    """The input contract contains DEVELOPMENT statistics only; cash is valid."""
    eligible=[]
    for name,s in summaries.items():
        if (s['trades']>=minimum_trades and s['traded_symbols']>=minimum_symbols
                and s['total_return']>0 and s['cost_2x_return']>0): eligible.append(name)
    return sorted(eligible,key=lambda k:(-summaries[k]['total_return'],k))[0] if eligible else 'CASH'


def evidence_gate(metrics, statistic, *, traded_symbols, cost_return, requirements,
                  independent_holdout, pit_universe, actual_theory_path, complete_trial_history=False):
    flags=(independent_holdout,pit_universe,actual_theory_path,complete_trial_history)
    if any(type(v) is not bool for v in flags): raise ValueError('explicit evidence booleans required')
    ci=statistic.get('bonferroni_mean_ci'); p=statistic.get('family_adjusted_p')
    research=dict(trades=metrics['trades']>=requirements['minimum_closed_trades'],
        symbols=traded_symbols>=requirements['minimum_symbols_with_closed_trades'],
        active_sessions=statistic.get('active_return_sessions',0)>=requirements['minimum_active_sessions'],
        positive_net=metrics['total_return']>0,cost_stress=cost_return>0,
        family_adjusted_significance=p is not None and 0<=p<requirements['alpha'],
        positive_confidence_lower_bound=ci is not None and ci[0]>0)
    external=dict(independent_holdout=independent_holdout,point_in_time_universe=pit_universe,
                  actual_theory_path=actual_theory_path,complete_historical_trial_record=complete_trial_history)
    return dict(research_checks=research,external_checks=external,
                research_passed=all(research.values()),validated=all(research.values()) and all(external.values()),
                reason='Conditional evidence for the declared scope only, never a future-profit guarantee; caller must supply verified external evidence, not inferred booleans.')
