import json
import numpy as np
from src.config.database import Base,engine,SessionLocal,ensure_schema
from src.models import User,GerminationStudy,SeedLot,LotCount,BayesRun,SamplingNote
from src.utils.security import hash_password
from src.services.study_service import create_snapshot
from src.services.analysis_service import analyze_germination
from src.services.bootstrap_cache import DatabaseFitCache

def seed():
    Base.metadata.create_all(engine);ensure_schema(engine)
    with SessionLocal() as db:
        for role,name in [('admin','陆岑 · 种质负责人'),('analyst','许禾 · 批次分析员'),('viewer','温桥 · 抽样阅读者')]:
            email=role+'@duocen.demo'
            if not db.query(User).filter_by(email=email).first():db.add(User(email=email,name=name,role=role,password_hash=hash_password('Duocen2026!')))
        studies=[]
        for si,(code,name) in enumerate([('DC-01','异质批次 · 萌发观察'),('DC-02','同观测率 · 上界诊断'),('DC-03','全零计数 · 拒绝边界')]):
            study=db.query(GerminationStudy).filter_by(code=code).first()
            if not study:study=GerminationStudy(code=code,name=name,description='28个独立合成批次，试种数20–180；DC-01潜在批率Beta(8,4)后Binomial抽样；DC-02是全部70/100的同观测率人工边界夹具；DC-03全部0。条件EB不作为种子合格或发货保证。',source='duocen-germination-lots-202611.csv');db.add(study);db.flush()
            rng=np.random.default_rng(320+si);ns=rng.integers(20,181,size=28);ps=rng.beta(8,4,size=28);ks=rng.binomial(ns,ps)
            if si==1:ns[:]=100;ks[:]=70
            if si==2:ks[:]=0
            for i,(n,k) in enumerate(zip(ns,ks)):
                lc=f'{code}-L{i+1:02}';lot=db.query(SeedLot).filter_by(study_id=study.id,code=lc).first()
                if not lot:lot=SeedLot(study_id=study.id,code=lc,name=f'独立种子批次 {i+1:02}',origin='铎岑种质试验室 / 合成批次');db.add(lot);db.flush()
                if not db.query(LotCount).filter_by(lot_id=lot.id).first():db.add(LotCount(lot_id=lot.id,n=int(n),k=int(k),note='合成汇总；n为试种粒数，k为萌发粒数，未萌发n−k；每批一条计数。'))
            db.flush();studies.append(study)
        if not db.query(BayesRun).filter_by(name='异质批次 · 条件EB基线').first():
            study=studies[0];snapshot=create_snapshot(db,study);result=analyze_germination(snapshot,.7,50);run=BayesRun(name='异质批次 · 条件EB基线',study_id=study.id,threshold=.7,future_n=50,input_snapshot=snapshot,status='completed',result=result,owner='许禾 · 批次分析员');db.add(run);db.flush();db.add(SamplingNote(run_id=run.id,title='首批独立补种研究说明',interpretation='后验与同批未来计数条件于点估αβ，不含超参数估计误差；贴边必须标弱识别。',proposal='保留每批n/k口径，下一轮同批独立试种，不把阈值概率当成合格或自动发货指令。',status='approved',owner='许禾 · 批次分析员',reviewer='陆岑 · 种质负责人',review_note='批准研究归档；结果只供独立补种研究，条件EB不作种子合格保证。'))
        if not db.query(BayesRun).filter_by(name='异质批次 · 重抽样混合基线').first():
            study=studies[0];snapshot=create_snapshot(db,study);cache=DatabaseFitCache(db)
            result=analyze_germination(snapshot,.7,50,bootstrap_seed=20261113,bootstrap_reps=200,cache=cache)
            run=BayesRun(name='异质批次 · 重抽样混合基线',study_id=study.id,threshold=.7,future_n=50,bootstrap_seed=20261113,bootstrap_reps=200,input_snapshot=snapshot,status='completed',result=result,owner='许禾 · 批次分析员');db.add(run);db.flush()
            db.add(SamplingNote(run_id=run.id,title='批次重抽样混合说明',interpretation='混合区间与阈值概率是成功重拟合批次的等权混合，含先验重估波动；失败样本单独记录，不冒充完整结果。',proposal='固定种子20261113、200次有放回重抽样；比较条件结果与混合结果后再决定独立补种。',status='approved',owner='许禾 · 批次分析员',reviewer='陆岑 · 种质负责人',review_note='批准研究归档；混合结果不是频率覆盖保证，也不作自动发货依据。'))
        db.commit();print(json.dumps({m.__tablename__:db.query(m).count() for m in [User,GerminationStudy,SeedLot,LotCount,BayesRun,SamplingNote]},ensure_ascii=False))
if __name__=='__main__':seed()
