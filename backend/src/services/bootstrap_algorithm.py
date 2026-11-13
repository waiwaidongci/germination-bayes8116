"""Batch-level bootstrap: resample lots with replacement, refit the prior each time.

The plan is deterministic for (sorted batch data, seed, reps).  Fitting results are
kept separate from scenario summaries so a new threshold / future_n can reuse them.
"""
import numpy as np
from collections import Counter
from scipy.special import betaln,gammaln,logsumexp
from scipy.stats import beta as beta_distribution

from .eb_algorithm import fit_hyperparameters
from ..utils.fingerprint import fingerprint

BOOTSTRAP_VERSION='batch-resample-refit-mixture-v1'
MIN_REPS=20
MAX_REPS=1000
MAX_SEED=2**32-1
WEIGHT_NOTE='等权：每个成功重拟合样本权重 1/S；失败样本不参与混合。'
MIXTURE_NOTE='区间与阈值概率是成功重拟合批次的等权混合，包含重抽样下先验重估的波动；它不是频率覆盖保证、不是种子合格保证，也不是自动发货依据。'


class MemoryFitCache:
    """Small deterministic stand-in for the DB-backed fit cache (also used in tests)."""
    def __init__(self,maxsize=16):
        self.maxsize=maxsize
        self.items={}
        self.hits=0
        self.misses=0
    def get(self,key):
        if key in self.items:
            self.hits+=1
            return self.items[key]
        self.misses+=1
        return None
    def set(self,key,value):
        self.items[key]=value
        while len(self.items)>self.maxsize:
            self.items.pop(next(iter(self.items)))
        return value


def validate_bootstrap_options(seed,reps):
    if isinstance(seed,bool) or not isinstance(seed,int) or not 0<=seed<=MAX_SEED:
        raise ValueError("重抽样种子须为0–%d的整数"%MAX_SEED)
    if isinstance(reps,bool) or not isinstance(reps,int) or not MIN_REPS<=reps<=MAX_REPS:
        raise ValueError("重抽样次数须为%d–%d的整数"%(MIN_REPS,MAX_REPS))
    return seed,reps


def ordered_batches(lots,counts):
    by_lot={}
    for count in counts:
        lot_id=count.get('lot_id')
        if lot_id in by_lot:raise ValueError("每批只允许一条计数")
        by_lot[lot_id]=count
    lot_ids=[lot.get('id') for lot in lots]
    if len(set(lot_ids))!=len(lots):raise ValueError("批次身份重复")
    if set(by_lot)!=set(lot_ids):raise ValueError("批次与计数必须一一对应，不能缺失或未知")
    pairs=sorted(((lot,by_lot[lot['id']]) for lot in lots),key=lambda x:x[0]['code'])
    if len({lot['code'] for lot,_ in pairs})!=len(pairs):raise ValueError("批次编号重复")
    return pairs


def bootstrap_data_hash(pairs):
    return fingerprint({'method':BOOTSTRAP_VERSION,'lots':[{'code':lot['code'],'n':count['n'],'k':count['k']} for lot,count in pairs]})


def bootstrap_cache_key(data_hash,seed,reps):
    return fingerprint({'method':BOOTSTRAP_VERSION,'data_hash':data_hash,'seed':int(seed),'reps':int(reps)})


def _normalise_fits(fits,reps):
    if not isinstance(fits,list) or len(fits)!=reps:
        raise ValueError("缓存的重抽样拟合记录与次数不一致")
    normalised=[]
    for index,item in enumerate(fits):
        if not isinstance(item,dict) or int(item.get('replicate',-1))!=index:raise ValueError("重抽样拟合记录序号不连续")
        if item.get('status')=='success':
            alpha=float(item['alpha']);beta=float(item['beta'])
            if not np.isfinite([alpha,beta]).all() or min(alpha,beta)<=0:raise ValueError("缓存的重抽样形状参数无效")
            loglik=item.get('loglik')
            loglik=float(loglik) if isinstance(loglik,(int,float)) and np.isfinite(loglik) else None
            normalised.append({'replicate':index,'status':'success','alpha':alpha,'beta':beta,'loglik':loglik})
        elif item.get('status')=='failed':
            normalised.append({'replicate':index,'status':'failed','reason':str(item.get('reason') or '原因未记录'),'sum_n':int(item.get('sum_n',0)),'sum_k':int(item.get('sum_k',0)),'unique_lots':int(item.get('unique_lots',0))})
        else:raise ValueError("缓存的重抽样拟合记录状态无效")
    return normalised


def _plan_from_fits(fits,data_hash,seed,reps,reused,source):
    successes=[x for x in fits if x['status']=='success']
    failures=[x for x in fits if x['status']=='failed']
    status='complete' if not failures else ('partial' if successes else 'failed')
    reasons=Counter(x['reason'] for x in failures)
    return {'version':BOOTSTRAP_VERSION,'data_hash':data_hash,'fit_hash':bootstrap_cache_key(data_hash,seed,reps),'cache_key':bootstrap_cache_key(data_hash,seed,reps),'seed':int(seed),'requested_reps':int(reps),'successful_reps':len(successes),'failed_reps':len(failures),'status':status,'weight':'equal_1_over_successful_reps','replicate_fits':fits,'failure_summary':[{'reason':reason,'count':count} for reason,count in sorted(reasons.items())],'refit_reused':bool(reused),'reuse_source':source,'fit_note':WEIGHT_NOTE+' 重拟合沿用有界多起点边际MLE，不声称全局最优；失败样本保留原因，不被当作成功样本。'}


def _plan_matches(plan,data_hash,seed,reps):
    try:
        return bool(isinstance(plan,dict) and plan.get('version')==BOOTSTRAP_VERSION and plan.get('data_hash')==data_hash and int(plan.get('seed'))==int(seed) and int(plan.get('requested_reps'))==int(reps) and isinstance(plan.get('replicate_fits'),list))
    except (TypeError,ValueError,AttributeError):return False


def fit_bootstrap(lots,counts,seed,reps,cache=None,existing_plan=None,progress=None):
    seed,reps=validate_bootstrap_options(seed,reps)
    pairs=ordered_batches(lots,counts);data_hash=bootstrap_data_hash(pairs);key=bootstrap_cache_key(data_hash,seed,reps)
    if existing_plan is not None and _plan_matches(existing_plan,data_hash,seed,reps):
        fits=_normalise_fits(existing_plan['replicate_fits'],reps)
        return _plan_from_fits(fits,data_hash,seed,reps,True,'source_run')
    if cache is not None:
        cached=cache.get(key,data_hash,seed,reps)
        if cached is not None and _plan_matches(cached,data_hash,seed,reps):
            fits=_normalise_fits(cached['replicate_fits'],reps)
            return _plan_from_fits(fits,data_hash,seed,reps,True,'cache')
    n=np.asarray([count['n'] for _,count in pairs],dtype=np.int64)
    k=np.asarray([count['k'] for _,count in pairs],dtype=np.int64)
    codes=[lot['code'] for lot,_ in pairs]
    rng=np.random.default_rng(seed);fits=[]
    for replicate in range(reps):
        # Resampling operates on code-sorted batches, so changing the incoming
        # batch order cannot change the same-seed resamples or their fits.
        index=rng.integers(0,len(n),size=len(n))
        sample_n=n[index];sample_k=k[index]
        sample_codes=[codes[i] for i in index]
        try:
            fit=fit_hyperparameters(sample_n,sample_k)
            fits.append({'replicate':replicate,'status':'success','alpha':float(fit['alpha']),'beta':float(fit['beta']),'loglik':float(fit['loglik'])})
        except Exception as error:
            fits.append({'replicate':replicate,'status':'failed','reason':str(error) or type(error).__name__,'sum_n':int(sample_n.sum()),'sum_k':int(sample_k.sum()),'unique_lots':len(set(sample_codes))})
        if progress is not None:progress(replicate+1,reps)
    plan=_plan_from_fits(fits,data_hash,seed,reps,False,'computed')
    if cache is not None:cache.set(key,{'version':plan['version'],'data_hash':data_hash,'seed':seed,'requested_reps':reps,'replicate_fits':plan['replicate_fits']})
    return plan


def mixture_quantile(a,b,probability,weights=None,tolerance=1e-12,max_iterations=200):
    """Quantile of sum_i w_i BetaCDF(x; a_i,b_i) by bisection (no density assumptions)."""
    a=np.asarray(a,dtype=float);b=np.asarray(b,dtype=float)
    if a.ndim!=1 or a.shape!=b.shape or not len(a):raise ValueError("混合分位数需要非空同长形状向量")
    if not np.isfinite(a).all() or not np.isfinite(b).all() or min(a.min(),b.min())<=0:raise ValueError("混合分位数形状须为有限正数")
    if not 0<=probability<=1:raise ValueError("分位概率须在[0,1]")
    if weights is None:weights=np.full(len(a),1/len(a))
    weights=np.asarray(weights,dtype=float)
    if weights.shape!=a.shape or not np.isfinite(weights).all() or weights.min()<0 or abs(weights.sum()-1)>1e-9:raise ValueError("混合权重须非负且和为1")
    if probability<=0:return 0.
    if probability>=1:return 1.
    def cdf(value):return float(np.sum(weights*beta_distribution.cdf(value,a,b)))
    low,high=0.,1.
    for _ in range(max_iterations):
        middle=(low+high)/2
        if cdf(middle)<probability:low=middle
        else:high=middle
        if high-low<=tolerance:break
    return float((low+high)/2)


def _mixture_future(a,b,weights,future_n):
    j=np.arange(future_n+1)
    log_choose=gammaln(future_n+1)-gammaln(j+1)-gammaln(future_n-j+1)
    log_rows=log_choose[None,:]+betaln(a[:,None]+j[None,:],b[:,None]+future_n-j[None,:])-betaln(a,b)[:,None]
    row_normalizer=logsumexp(log_rows,axis=1)
    row_mass=np.exp(row_normalizer)
    if not np.isfinite(row_mass).all() or np.max(np.abs(row_mass-1))>1e-7:raise ValueError("重抽样单次预测PMF归一偏差过大")
    log_weights=np.log(weights)
    log_mixture=logsumexp(log_rows-row_normalizer[:,None]+log_weights[:,None],axis=0)
    pmf=np.exp(log_mixture);mass=float(pmf.sum())
    if not np.isfinite(mass) or abs(mass-1)>1e-7:raise ValueError("重抽样混合预测PMF归一偏差过大")
    means=future_n*a/(a+b)
    variances=future_n*(a/(a+b))*((b)/(a+b))*((a+b+future_n)/(a+b+1))
    mean=float(np.sum(weights*means));second=float(np.sum(weights*(variances+means*means)));variance=max(0.,second-mean*mean)
    cumulative=np.cumsum(pmf)
    lower_k=int(np.searchsorted(cumulative,.025,side='left'));upper_k=int(np.searchsorted(cumulative,.975,side='left'))
    pmf=pmf/mass
    return {'future_n':future_n,'pmf':[{'k':int(v),'probability':float(p)} for v,p in zip(j,pmf)],'mean':mean,'variance':variance,'lower_k':min(lower_k,future_n),'upper_k':min(upper_k,future_n),'mass_before_normalization':mass,'normalization_error':abs(mass-1)}


def plan_from_bootstrap(bootstrap):
    """Rebuild the refit plan from a stored bootstrap summary (for scenario reuse)."""
    if not isinstance(bootstrap,dict) or not isinstance(bootstrap.get('replicate_fits'),list):
        raise ValueError("缺少可复用的重抽样拟合记录")
    return {'version':bootstrap.get('version'),'data_hash':bootstrap.get('data_hash'),'fit_hash':bootstrap.get('fit_hash'),'cache_key':bootstrap.get('cache_key'),'seed':bootstrap.get('seed'),'requested_reps':bootstrap.get('requested_reps'),'successful_reps':bootstrap.get('successful_reps'),'failed_reps':bootstrap.get('failed_reps'),'status':bootstrap.get('status'),'weight':bootstrap.get('weight'),'replicate_fits':bootstrap['replicate_fits'],'failure_summary':bootstrap.get('failure_summary',[]),'refit_reused':True,'reuse_source':'source_run'}


def summarize_bootstrap(plan,lots,counts,threshold,future_n,scenario_reused=False):
    if type(threshold) not in (int,float) or isinstance(threshold,bool) or not np.isfinite(threshold) or not 0<threshold<1:raise ValueError("研究阈值须为(0,1)有限比例")
    if type(future_n) is not int or isinstance(future_n,bool) or not 1<=future_n<=200:raise ValueError("未来试种数须为1–200整数")
    pairs=ordered_batches(lots,counts);data_hash=bootstrap_data_hash(pairs)
    if plan.get('data_hash')!=data_hash:raise ValueError("重抽样拟合与当前批次数据不一致，缓存须失效重算")
    successful=[x for x in plan['replicate_fits'] if x['status']=='success']
    summary={'version':BOOTSTRAP_VERSION,'status':plan['status'],'seed':plan['seed'],'requested_reps':plan['requested_reps'],'successful_reps':plan['successful_reps'],'failed_reps':plan['failed_reps'],'weight':plan['weight'],'data_hash':data_hash,'fit_hash':plan['fit_hash'],'cache_key':plan['cache_key'],'refit_reused':bool(plan['refit_reused']),'reuse_source':plan['reuse_source'],'scenario_reused':bool(scenario_reused),'failure_summary':plan['failure_summary'],'failures':[x for x in plan['replicate_fits'] if x['status']=='failed'],'replicate_fits':plan['replicate_fits'],'threshold':float(threshold),'future_n':future_n,'lots':[],'complete':plan['status']=='complete','notes':{'weight':WEIGHT_NOTE,'interpretation':MIXTURE_NOTE,'partial':'有失败重拟合样本：混合只使用成功样本的等权分布，不冒充完整次数结果。' if plan['status']!='complete' else '全部重拟合样本成功；仍只是模型内混合，不保证频率覆盖。'}}
    if not successful:
        summary['status']='failed';summary['notes']['failure']='全部重拟合样本失败，未生成混合区间；原条件后验结果仍保留但不得冒充重抽样结果。'
        return summary
    fits_a=np.asarray([x['alpha'] for x in successful],dtype=float)
    fits_b=np.asarray([x['beta'] for x in successful],dtype=float)
    weights=np.full(len(successful),1/len(successful))
    posterior_means=fits_a/(fits_a+fits_b);threshold_probabilities=beta_distribution.sf(threshold,fits_a,fits_b)
    future_means=future_n*posterior_means
    grid=np.linspace(0,1,201)
    for lot,count in pairs:
        a=fits_a+count['k'];b=fits_b+count['n']-count['k']
        cdf=beta_distribution.cdf(grid[:,None],a[None,:],b[None,:]).mean(axis=1)
        lower=mixture_quantile(a,b,.025,weights);upper=mixture_quantile(a,b,.975,weights)
        tail=float(np.sum(weights*beta_distribution.sf(threshold,a,b)))
        prediction=_mixture_future(a,b,weights,future_n)
        if not np.isfinite([lower,upper,tail]).all():raise ValueError("重抽样混合后验计算未能给出有限概率")
        spread={'mean_q025':float(np.quantile(posterior_means,.025)),'mean_q975':float(np.quantile(posterior_means,.975)),'prob_gt_threshold_q025':float(np.quantile(threshold_probabilities,.025)),'prob_gt_threshold_q975':float(np.quantile(threshold_probabilities,.975)),'future_mean_q025':float(np.quantile(future_means,.025)),'future_mean_q975':float(np.quantile(future_means,.975))}
        summary['lots'].append({'lot':lot,'count':count,'mixture_mean':float(np.sum(weights*a/(a+b))),'mixture_lower':float(lower),'mixture_upper':float(upper),'mixture_prob_gt_threshold':tail,'threshold':float(threshold),'mixture_cdf':[{'p':float(v),'cdf':float(q)} for v,q in zip(grid,cdf)],'prediction':prediction,'replicate_spread':spread,'weights':'equal_1_over_successful_reps','interpretation':MIXTURE_NOTE})
    return summary
