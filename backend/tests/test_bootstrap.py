"""Tests for batch bootstrap refits, mixture quantiles and cache invalidation.

Run from backend/: PYTHONPATH=. python -m pytest tests -q
"""
import math
from types import SimpleNamespace

import numpy as np
import pytest
from scipy.stats import beta as beta_distribution

from src.services.bootstrap_algorithm import (BOOTSTRAP_VERSION, MemoryFitCache,
    bootstrap_cache_key, bootstrap_data_hash, fit_bootstrap, mixture_quantile,
    ordered_batches, plan_from_bootstrap, summarize_bootstrap, validate_bootstrap_options)
from src.services.analysis_service import analyze_germination
from src.services.bootstrap_cache import DatabaseFitCache
from src.models import BootstrapFit


def fixture_batches(seed=320,size=12,shape=(8,4)):
    rng=np.random.default_rng(seed);ns=rng.integers(20,181,size=size);ps=rng.beta(shape[0],shape[1],size=size);ks=rng.binomial(ns,ps)
    lots=[{'id':i+1,'code':'L%02d'%(i+1),'study_id':1,'name':'批%d'%(i+1),'origin':''} for i in range(size)]
    counts=[{'lot_id':i+1,'n':int(ns[i]),'k':int(ks[i]),'note':''} for i in range(size)]
    return lots,counts


class StubCache(MemoryFitCache):
    """Memory cache that speaks the same get/set signature as the DB cache."""
    def get(self,key,data_hash=None,seed=None,reps=None):
        item=self.items.get(key)
        if not item:
            self.misses+=1
            return None
        if data_hash is not None and (item['data_hash']!=data_hash or int(item['seed'])!=int(seed) or int(item['requested_reps'])!=int(reps)):
            self.misses+=1
            return None
        self.hits+=1
        return item


def tiny_plan(lots,counts,seed=7,reps=4,successes=None,existing=None):
    """Fast plan for quantile tests: use known shapes instead of real refits."""
    ordered=ordered_batches(lots,counts);data_hash=bootstrap_data_hash(ordered)
    fits=[]
    for i in range(reps):
        if existing and i in existing:fits.append({'replicate':i,'status':'failed','reason':'全部0或全部1无法稳定估计先验，拒绝拟合','sum_n':0,'sum_k':0,'unique_lots':0})
        else:
            alpha,beta=successes[i]
            fits.append({'replicate':i,'status':'success','alpha':alpha,'beta':beta,'loglik':-1.})
    return {'version':BOOTSTRAP_VERSION,'data_hash':data_hash,'fit_hash':bootstrap_cache_key(data_hash,seed,reps),'cache_key':bootstrap_cache_key(data_hash,seed,reps),'seed':seed,'requested_reps':reps,'successful_reps':len([f for f in fits if f['status']=='success']),'failed_reps':len([f for f in fits if f['status']=='failed']),'status':'partial' if any(f['status']=='failed' for f in fits) else 'complete','weight':'equal_1_over_successful_reps','replicate_fits':fits,'failure_summary':[],'refit_reused':False,'reuse_source':'computed'}


def test_same_seed_same_order_and_reordered_batches_reproduce():
    lots,counts=fixture_batches(size=10)
    first=fit_bootstrap(lots,counts,12345,30)
    rng=np.random.default_rng(9);order=rng.permutation(len(lots))
    shuffled_lots=[lots[i] for i in order];by_id={c['lot_id']:c for c in counts};shuffled_counts=[by_id[l['id']] for l in shuffled_lots]
    second=fit_bootstrap(shuffled_lots,shuffled_counts,12345,30)
    assert first['replicate_fits']==second['replicate_fits']
    assert first['fit_hash']==second['fit_hash']==second['cache_key']
    assert first['status']==second['status'],"same seed must reproduce failures too"


def test_order_mismatched_counts_rejected():
    lots,_=fixture_batches(size=8);wrong=[{'lot_id':i,'n':30,'k':10} for i in range(1,8)]
    with pytest.raises(ValueError, match="一一对应"):
        ordered_batches(lots,wrong)
    duplicated=fixture_batches(size=8)[1]+[{'lot_id':1,'n':30,'k':10}]
    with pytest.raises(ValueError, match="只允许一条计数"):
        ordered_batches(lots,duplicated)


def test_seed_and_reps_validation():
    for seed,reps in [(True,20),(-1,20),(2**32,20),(1,19),(1,1001),(1,True),(1.5,20),(None,20)]:
        with pytest.raises(ValueError):
            validate_bootstrap_options(seed,reps)
    assert validate_bootstrap_options(2**32-1,20)==(2**32-1,20)


def test_partial_failures_are_kept_and_not_used_as_success():
    lots,counts=fixture_batches(size=12)
    plan=tiny_plan(lots,counts,seed=7,reps=6,successes={i:(10.+i,5.) for i in range(6)},existing={2,4})
    summary=summarize_bootstrap(plan,lots,counts,.7,20)
    assert summary['status']=='partial'
    assert summary['successful_reps']==4 and summary['failed_reps']==2
    assert len(summary['failures'])==2 and summary['failures'][0]['replicate']==2
    assert summary['failures'][0]['reason'].startswith('全部0')
    assert '不冒充完整次数结果' in summary['notes']['partial']
    assert not summary['complete']
    # Only the successful shapes define the mixture.
    for row in summary['lots']:
        assert 0<row['mixture_prob_gt_threshold']<1
        assert row['prediction']['future_n']==20


def test_all_failures_keep_conditional_but_no_mixture():
    lots,counts=fixture_batches(size=8);snapshot={'study':{'code':'S','name':'S'},
        'lots':lots,'counts':counts}
    result=analyze_germination(snapshot,.7,50,bootstrap_seed=3,bootstrap_reps=20)
    plan=tiny_plan(lots,counts,seed=3,reps=20,successes={},existing=set(range(20)))
    summary=summarize_bootstrap(plan,lots,counts,.7,50)
    assert summary['status']=='failed' and summary['successful_reps']==0
    assert summary['lots']==[] and '全部重拟合样本失败' in summary['notes']['failure']
    assert result['lots'] and result['fit']['alpha']>0,"original conditional results must stay"


def test_real_degenerate_resamples_are_recorded_not_mixed():
    """All-zero resamples really occur: each one must fail with its reason."""
    lots,counts=fixture_batches(size=8)
    for count in counts[:7]:count['k']=0
    counts[7]['k']=max(3,counts[7]['k'])
    plan=fit_bootstrap(lots,counts,2026,40)
    failed=[x for x in plan['replicate_fits'] if x['status']=='failed']
    assert failed,"a resample drawing only zero-k batches must be rejected"
    for row in failed:
        assert row['reason']=='全部0或全部1无法稳定估计先验，拒绝拟合'
        assert 0<=row['sum_k']<=row['sum_n'] and row['unique_lots']>=1
    successful=[x for x in plan['replicate_fits'] if x['status']=='success']
    assert successful,"the k>0 batch must keep some resamples fittable"
    summary=summarize_bootstrap(plan,lots,counts,.7,20)
    assert summary['status']=='partial' and summary['successful_reps']==len(successful)
    assert summary['failed_reps']==len(failed)
    assert len(summary['failure_summary'])==1


def test_real_all_one_resamples_fail_too():
    lots,counts=fixture_batches(size=8)
    for count in counts[:7]:count['k']=count['n']
    counts[7]['k']=max(0,counts[7]['n']-2)
    plan=fit_bootstrap(lots,counts,11,40)
    failed=[x for x in plan['replicate_fits'] if x['status']=='failed']
    assert failed and all(x['sum_k']==x['sum_n'] for x in failed)


def test_single_component_mixture_equals_conditional_result():
    """With every refit returning the same shapes, the mixture must equal the conditional EB."""
    from src.services.analysis_service import conditional_result
    from src.services.posterior_algorithm import posterior_prediction
    lots,counts=fixture_batches(size=8)
    snapshot={'study':{'code':'S','name':'S'},'lots':lots,'counts':counts}
    base=conditional_result(snapshot,.7,20)
    fit=base['fit']
    plan=tiny_plan(lots,counts,seed=5,reps=4,successes={i:(fit['alpha'],fit['beta']) for i in range(4)})
    summary=summarize_bootstrap(plan,lots,counts,.7,20)
    assert summary['status']=='complete'
    for row,conditional in zip(summary['lots'],base['lots']):
        direct=posterior_prediction(fit['alpha'],fit['beta'],row['count']['n'],row['count']['k'],.7,20)
        assert row['mixture_mean']==pytest.approx(direct['mean'],abs=1e-12)
        assert row['mixture_lower']==pytest.approx(direct['lower'],abs=1e-9)
        assert row['mixture_upper']==pytest.approx(direct['upper'],abs=1e-9)
        assert row['mixture_prob_gt_threshold']==pytest.approx(direct['prob_gt_threshold'],abs=1e-12)
        assert row['prediction']['mean']==pytest.approx(direct['prediction']['mean'],abs=1e-9)
        assert row['prediction']['variance']==pytest.approx(direct['prediction']['variance'],abs=1e-9)


def test_conditional_rows_unchanged_when_bootstrap_added():
    lots,counts=fixture_batches(size=10)
    snapshot={'study':{'code':'S','name':'S'},'lots':lots,'counts':counts}
    plain=analyze_germination(snapshot,.7,20)
    with_boot=analyze_germination(snapshot,.7,20,bootstrap_seed=31,bootstrap_reps=20)
    assert with_boot['bootstrap'] is not None and plain['bootstrap'] is None
    assert with_boot['fit']==plain['fit']
    assert with_boot['lots']==plain['lots']
    assert with_boot['fit_hash']==plain['fit_hash']
    # The mixture is a different estimator, not a relabelled conditional interval.
    item=with_boot['bootstrap']['lots'][0]
    assert item['mixture_mean']!=plain['lots'][0]['mean'] or item['mixture_lower']!=plain['lots'][0]['lower']


def test_mixture_quantile_matches_bisection_and_single_component():
    a=np.array([3.,8.]);b=np.array([2.,4.]);weights=np.array([.3,.7])
    def cdf(x):return float(np.sum(weights*beta_distribution.cdf(x,a,b)))
    q=mixture_quantile(a,b,.5,weights)
    assert abs(cdf(q)-.5)<1e-10
    assert 0<q<1
    # Degenerate mixture = one Beta: compare against SciPy ppf.
    single=mixture_quantile(np.array([7.]),np.array([9.]),.95,None)
    assert abs(single-beta_distribution.ppf(.95,7,9))<1e-9
    assert mixture_quantile(a,b,0,weights)==0 and mixture_quantile(a,b,1,weights)==1
    with pytest.raises(ValueError):mixture_quantile(a,b,.5,np.array([.5,.6]))
    with pytest.raises(ValueError):mixture_quantile(np.array([-1.]),np.array([2.]),.5)


def test_mixture_interval_and_tail_are_consistent():
    lots,counts=fixture_batches(size=10)
    plan=tiny_plan(lots,counts,seed=11,reps=5,successes={i:(6.+i*2,3.+i) for i in range(5)})
    summary=summarize_bootstrap(plan,lots,counts,.75,20)
    for row in summary['lots']:
        grid=np.array([c['p'] for c in row['mixture_cdf']]);values=np.array([c['cdf'] for c in row['mixture_cdf']])
        assert np.all(np.diff(values)>=-1e-12),"mixture CDF must be monotone"
        assert values[0]<=.025+1e-9 and values[-1]>=.975-1e-9
        assert row['mixture_lower']<row['mixture_upper']
        # Mixture quantiles must agree with the stored mixture CDF within one grid cell.
        below=grid[values<=.025]
        assert not len(below) or row['mixture_lower']>=below.max()-1e-9
        above=grid[values>=.975]
        assert not len(above) or row['mixture_upper']<=above.min()+1e-9
        tail_expected=float(np.sum(np.full(5,1/5)*beta_distribution.sf(.75,np.array([6.+i*2 for i in range(5)])+row['count']['k'],np.array([3.+i for i in range(5)])+row['count']['n']-row['count']['k'])))
        assert row['mixture_prob_gt_threshold']==pytest.approx(tail_expected)
        assert abs(sum(p['probability'] for p in row['prediction']['pmf'])-1)<1e-9
        assert row['prediction']['normalization_error']<=1e-7


def test_cache_reuses_only_exact_data_seed_and_reps(tmp_path):
    lots,counts=fixture_batches(size=10)
    cache=StubCache()
    first=fit_bootstrap(lots,counts,99,20,cache=cache)
    assert first['reuse_source']=='computed' and cache.hits==0 and cache.misses==1
    again=fit_bootstrap(lots,counts,99,20,cache=cache)
    assert again['reuse_source']=='cache' and again['refit_reused'] and again['replicate_fits']==first['replicate_fits']
    assert cache.hits==1
    # New seed or reps must not reuse.
    other_seed=fit_bootstrap(lots,counts,100,20,cache=cache)
    assert other_seed['reuse_source']=='computed' and other_seed['seed']==100
    other_reps=fit_bootstrap(lots,counts,99,21,cache=cache)
    assert other_reps['reuse_source']=='computed' and other_reps['requested_reps']==21
    # Source data update invalidates the cached entry and forces recomputation.
    changed=[dict(c) for c in counts];changed[0]['k']=max(0,changed[0]['k']-1)
    changed_plan=fit_bootstrap(lots,changed,99,20,cache=cache)
    assert changed_plan['reuse_source']=='computed'
    assert changed_plan['data_hash']!=first['data_hash'] and changed_plan['fit_hash']!=first['fit_hash']


def test_scenario_change_reuses_fits_and_new_scenario_recomputes(tmp_path):
    lots,counts=fixture_batches(size=10)
    plan=fit_bootstrap(lots,counts,42,20,cache=StubCache())
    base=summarize_bootstrap(plan,lots,counts,.7,20)
    reused=plan_from_bootstrap(dict(base))
    assert reused['refit_reused'] and reused['reuse_source']=='source_run'
    new=summarize_bootstrap(reused,lots,counts,.85,60,scenario_reused=True)
    assert new['fit_hash']==base['fit_hash'] and new['data_hash']==base['data_hash']
    assert new['refit_reused'] and new['scenario_reused']
    assert new['threshold']==.85 and new['future_n']==60
    assert new['lots'][0]['mixture_prob_gt_threshold']<base['lots'][0]['mixture_prob_gt_threshold']
    assert new['lots'][0]['prediction']['future_n']==60
    # Stale data + old plan must be rejected instead of relabelled.
    changed=[dict(c) for c in counts];changed[0]['n']+=1
    with pytest.raises(ValueError, match="缓存须失效"):
        summarize_bootstrap(plan,lots,changed,.7,20)


def test_database_cache_rejects_stale_row(db_session=None):
    engine=__import__('sqlalchemy').create_engine('sqlite://')
    from sqlalchemy.orm import sessionmaker
    Base=__import__('src.config.database',fromlist=['Base']).Base
    Base.metadata.create_all(engine);Session=sessionmaker(bind=engine,expire_on_commit=False)
    with Session() as db:
        cache=DatabaseFitCache(db)
        key='k1';data='h1'
        assert cache.get(key,data,5,20) is None and cache.misses==1
        cache.set(key,{'data_hash':data,'seed':5,'requested_reps':20,'replicate_fits':[{'replicate':0,'status':'success','alpha':2.,'beta':1.,'loglik':0.}]})
        db.commit()
        assert cache.get(key,data,5,20) is not None and cache.hits==1
        assert cache.get(key,'h2',5,20) is None,"updated source data must invalidate"
        assert cache.get(key,data,6,20) is None
