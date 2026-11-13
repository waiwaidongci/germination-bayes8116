import csv,io
from redis import Redis
from fastapi import HTTPException
from ..models import BayesRun,SamplingNote
from ..config.settings import REDIS_URL
from ..utils.serialize import record
from .study_service import get_study,create_snapshot
from .analysis_service import prepare_snapshot,scenario_result
from .bootstrap_algorithm import summarize_bootstrap,plan_from_bootstrap
from .audit_service import log
queue=Redis.from_url(REDIS_URL,decode_responses=True)
def list_runs(db):return [record(r) for r in db.query(BayesRun).order_by(BayesRun.id.desc())]
def get_run(db,rid):
    row=db.get(BayesRun,rid)
    if not row:raise HTTPException(404,"批次概率运行不存在")
    return row

def _snapshot_for(row,db):
    snapshot=row.input_snapshot
    try:prepare_snapshot(snapshot)
    except ValueError as e:raise HTTPException(422,str(e))
    return snapshot
def create_run(db,data,user):
    study=get_study(db,data.study_id);snapshot=create_snapshot(db,study)
    try:prepare_snapshot(snapshot)
    except ValueError as e:raise HTTPException(422,str(e))
    payload=data.model_dump()
    row=BayesRun(**payload,input_snapshot=snapshot,owner=user.name);db.add(row);db.flush();log(db,user.email,'bayes.enqueue',row.id);db.commit()
    try:queue.rpush('germination:jobs',row.id)
    except Exception:row.status='failed';row.error='队列不可用，请重提';db.commit();raise HTTPException(503,row.error)
    return record(row)

def resample_run(db,rid,data,user):
    """Queue a new bootstrap run on the frozen snapshot of an existing run."""
    source=get_run(db,rid)
    if source.status!='completed':raise HTTPException(409,"仅可基于已完成研究发起重抽样")
    if data.bootstrap_seed is None or data.bootstrap_reps is None:raise HTTPException(422,"重抽样研究须同时提供种子与次数")
    if data.study_id!=source.study_id:raise HTTPException(422,"重抽样必须沿用源研究的研究档案")
    snapshot=_snapshot_for(source,db)
    row=BayesRun(name=data.name,study_id=source.study_id,threshold=data.threshold,future_n=data.future_n,bootstrap_seed=data.bootstrap_seed,bootstrap_reps=data.bootstrap_reps,input_snapshot=snapshot,owner=user.name)
    db.add(row);db.flush();log(db,user.email,'bayes.resample_enqueue',f"{source.id}->{row.id}");db.commit()
    try:queue.rpush('germination:jobs',row.id)
    except Exception:row.status='failed';row.error='队列不可用，请重提';db.commit();raise HTTPException(503,row.error)
    return record(row)

def run_scenario(db,rid,data):
    """Reuse the frozen refit plan of a completed bootstrap run under a new threshold/future_n."""
    source=get_run(db,rid)
    if source.status!='completed':raise HTTPException(409,"仅已完成研究可切换情景")
    result=source.result or {}
    plan=result.get('bootstrap')
    if not plan or not plan.get('replicate_fits'):raise HTTPException(409,"该研究没有可复用的重抽样拟合记录")
    snapshot=_snapshot_for(source,db)
    try:
        updated=scenario_result(result,snapshot,data.threshold,data.future_n)
        updated['bootstrap']=summarize_bootstrap(plan_from_bootstrap(plan),snapshot['lots'],snapshot['counts'],data.threshold,data.future_n,scenario_reused=True)
    except ValueError as e:raise HTTPException(422,str(e))
    updated['bootstrap']['input_hash']=updated['input_hash']
    updated['scenario_reused']={'source_run':source.id,'threshold':float(data.threshold),'future_n':data.future_n,'previous_threshold':source.threshold,'previous_future_n':source.future_n}
    return updated

def export_run(db,rid,kind='summary'):
    row=get_run(db,rid)
    if row.status!='completed' or not db.query(SamplingNote).filter_by(run_id=rid,status='approved').first():raise HTTPException(403,'仅获批完成研究可导出')
    o=io.StringIO();w=csv.writer(o);r=row.result;fit=r['fit']
    if kind=='predictive':
        w.writerow(['scope','lot','future_n','future_k','probability','fit_alpha','fit_beta','weak_identification','input_hash'])
        for x in r['lots']:
            for p in x['prediction']['pmf']:w.writerow(['conditional',x['lot']['code'],row.future_n,p['k'],p['probability'],fit['alpha'],fit['beta'],fit['weak_identification'],r['input_hash']])
    elif kind=='bootstrap':
        b=r.get('bootstrap')
        if not b:raise HTTPException(409,'该研究未配置批次重抽样')
        w.writerow(['lot','n','k','seed','reps','successful_reps','failed_reps','status','mixture_mean','mixture_q025','mixture_q975','mixture_Pr_p_gt_threshold','threshold','future_n','mix_future_mean','mix_future_variance','mix_future_pmf','fit_hash','data_hash'])
        for x in b['lots']:
            pmf=' '.join(f"{p['k']}:{p['probability']:.10g}" for p in x['prediction']['pmf'])
            w.writerow([x['lot']['code'],x['count']['n'],x['count']['k'],b['seed'],b['requested_reps'],b['successful_reps'],b['failed_reps'],b['status'],x['mixture_mean'],x['mixture_lower'],x['mixture_upper'],x['mixture_prob_gt_threshold'],row.threshold,row.future_n,x['prediction']['mean'],x['prediction']['variance'],pmf,b['fit_hash'],b['data_hash']])
        for f in b.get('failures',[]):w.writerow(['REPLICATE-%d-%s'%(f['replicate'],f['reason']),'','',b['seed'],b['requested_reps'],b['successful_reps'],b['failed_reps'],'failed','','','','','','','','','',b['fit_hash'],b['data_hash']])
    else:
        w.writerow(['lot','n','k','raw_rate','post_alpha','post_beta','conditional_mean','conditional_q025','conditional_q975','Pr_p_gt_threshold','threshold','future_n','future_mean','future_variance','fit_alpha','fit_beta','weak_identification','fit_hash','input_hash'])
        for x in r['lots']:w.writerow([x['lot']['code'],x['count']['n'],x['count']['k'],x['raw_rate'],x['post_alpha'],x['post_beta'],x['mean'],x['lower'],x['upper'],x['prob_gt_threshold'],row.threshold,row.future_n,x['prediction']['mean'],x['prediction']['variance'],fit['alpha'],fit['beta'],fit['weak_identification'],r['fit_hash'],r['input_hash']])
    return o.getvalue()
