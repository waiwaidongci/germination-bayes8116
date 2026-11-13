"""Service-level tests on SQLite: resample/scenario reuse, cache and export."""
import csv,io
import pytest
from sqlalchemy import create_engine,text
from sqlalchemy.orm import sessionmaker
from fastapi import HTTPException

from src.config.database import Base,ensure_schema
from src.models import User,GerminationStudy,SeedLot,LotCount,BayesRun,SamplingNote,BootstrapFit
from src.schemas.run import RunInput,ScenarioInput
from src.services import run_service
from src.services.analysis_service import analyze_germination
from src.services.bootstrap_cache import DatabaseFitCache
import numpy as np


class Queue:
    def __init__(self):self.jobs=[]
    def rpush(self,name,value):self.jobs.append((name,value))


@pytest.fixture()
def db(monkeypatch,tmp_path):
    engine=create_engine('sqlite:///'+str(tmp_path/'test.db'))
    Base.metadata.create_all(engine)
    Session=sessionmaker(bind=engine,expire_on_commit=False)
    session=Session()
    user=User(name='许禾',email='analyst@duocen.demo',role='analyst',password_hash='x')
    study=GerminationStudy(code='DC-01',name='异质批次',description='',source='synthetic')
    session.add_all([user,study]);session.flush()
    rng=np.random.default_rng(320);ns=rng.integers(20,181,size=10);ps=rng.beta(8,4,size=10);ks=rng.binomial(ns,ps)
    for i in range(10):
        lot=SeedLot(study_id=study.id,code='L%02d'%(i+1),name='批',origin='')
        session.add(lot);session.flush();session.add(LotCount(lot_id=lot.id,n=int(ns[i]),k=int(ks[i]),note=''))
    session.commit()
    monkeypatch.setattr(run_service,'queue',Queue())
    yield session,user,study
    session.close()


def make_completed(db,user,study,seed=777,reps=20,threshold=.7,future=20):
    payload=RunInput(name='重抽样研究',study_id=study.id,threshold=threshold,future_n=future,bootstrap_seed=seed,bootstrap_reps=reps)
    row=run_service.create_run(db,payload,user)
    stored=run_service.get_run(db,row['id'])
    result=analyze_germination(stored.input_snapshot,stored.threshold,stored.future_n,stored.bootstrap_seed,stored.bootstrap_reps,cache=DatabaseFitCache(db))
    stored.result=result;stored.status='completed';db.commit()
    return stored


def test_create_and_resample_keep_seed_and_reps(db):
    session,user,study=db
    run=make_completed(session,user,study)
    row=run_service.get_run(session,run.id)
    assert row.bootstrap_seed==777 and row.bootstrap_reps==20
    assert row.result['bootstrap']['seed']==777
    assert row.result['bootstrap']['requested_reps']==20
    assert row.result['bootstrap']['successful_reps']==20
    assert len(row.result['bootstrap']['replicate_fits'])==20
    # Resample builds on the frozen snapshot, so a later source update cannot alter it.
    session.add(LotCount(lot_id=1,n=5,k=5,note='') if False else BayesRun(name='x',study_id=study.id,threshold=.7,future_n=20,input_snapshot={},owner='x'))
    session.query(LotCount).filter_by(lot_id=1).first().n=999
    session.commit()
    child=run_service.resample_run(db=session,rid=run.id,data=RunInput(name='再抽样',study_id=study.id,threshold=.7,future_n=30,bootstrap_seed=888,bootstrap_reps=20),user=user)
    child_row=run_service.get_run(session,child['id'])
    assert child_row.bootstrap_seed==888 and child_row.bootstrap_reps==20
    frozen={c['lot_id']:c for c in child_row.input_snapshot['counts']}
    assert frozen[1]['n']!=999,"frozen snapshot must not follow source edits"
    assert child_row.status=='queued'


def test_resample_requires_completed_source(db):
    session,user,study=db
    payload=RunInput(name='未完成',study_id=study.id,threshold=.7,future_n=20,bootstrap_seed=1,bootstrap_reps=20)
    row=run_service.create_run(session,payload,user)
    with pytest.raises(HTTPException) as e:run_service.resample_run(session,row['id'],payload,user)
    assert e.value.status_code==409


def test_scenario_reuses_plan_without_refit(db):
    session,user,study=db
    run=make_completed(session,user,study)
    original=run.result['bootstrap']
    updated=run_service.run_scenario(session,run.id,ScenarioInput(threshold=.85,future_n=50))
    b=updated['bootstrap']
    assert b['scenario_reused'] and b['refit_reused'] and b['reuse_source']=='source_run'
    assert b['seed']==original['seed'] and b['requested_reps']==original['requested_reps']
    assert b['fit_hash']==original['fit_hash'] and b['data_hash']==original['data_hash']
    assert b['threshold']==.85 and b['future_n']==50
    assert b['lots'][0]['prediction']['future_n']==50
    assert updated['scenario_reused']['source_run']==run.id
    # Conditional rows are also recomputed for the new scenario, but fit is untouched.
    assert updated['threshold']==.85 and updated['future_n']==50
    assert updated['fit']==original_conditional(run.result)['fit']
    # No new cache row and no new run were created.
    assert session.query(BayesRun).count()==1
    assert session.query(BootstrapFit).count()>=1


def original_conditional(result):
    return {'fit':result['fit']}


def test_scenario_rejects_stale_snapshot(db):
    session,user,study=db
    run=make_completed(session,user,study)
    row=run_service.get_run(session,run.id)
    snapshot=dict(row.input_snapshot)
    counts=[dict(c) for c in snapshot['counts']];counts[0]['n']+=1
    row.input_snapshot={**snapshot,'counts':counts};session.commit()
    with pytest.raises(HTTPException) as e:run_service.run_scenario(session,run.id,ScenarioInput(threshold=.8,future_n=20))
    assert e.value.status_code==422 and '缓存须失效' in e.value.detail


def test_cache_entry_reused_across_runs_and_invalidated_when_data_changes(db):
    session,user,study=db
    first=make_completed(session,user,study,seed=555,reps=20)
    assert first.result['bootstrap']['reuse_source']=='computed'
    second=make_completed(session,user,study,seed=555,reps=20)
    assert second.result['bootstrap']['reuse_source']=='cache'
    assert second.result['bootstrap']['replicate_fits']==first.result['bootstrap']['replicate_fits']
    cache=DatabaseFitCache(session)
    assert cache.describe(first.result['bootstrap']['cache_key'])
    # Changing source n/k changes the data hash, so a new fit is required.
    third_snapshot=dict(first.input_snapshot)
    counts=[dict(c) for c in third_snapshot['counts']];counts[0]['k']=max(0,counts[0]['k']-2)
    changed=analyze_germination({**third_snapshot,'counts':counts},.7,20,bootstrap_seed=555,bootstrap_reps=20,cache=DatabaseFitCache(session))
    assert changed['bootstrap']['reuse_source']=='computed'
    assert changed['bootstrap']['data_hash']!=first.result['bootstrap']['data_hash']
    assert len(session.query(BootstrapFit).all())==2


def test_bootstrap_export_lists_failures_and_rejects_missing(db):
    session,user,study=db
    run=make_completed(session,user,study)
    session.add(SamplingNote(run_id=run.id,title='t',interpretation='i',proposal='p',status='approved',owner='u'))
    session.commit()
    content=run_service.export_run(session,run.id,'bootstrap')
    rows=list(csv.reader(io.StringIO(content.lstrip('﻿'))))
    assert rows[0][:4]==['lot','n','k','seed']
    assert len(rows)==1+len(run.result['bootstrap']['lots'])+len(run.result['bootstrap']['failures'])
    failed=[r for r in rows[1:] if r[7]=='failed']
    assert len(failed)==run.result['bootstrap']['failed_reps']
    conditional=run_service.export_run(session,run.id,'summary')
    assert 'conditional_mean' in conditional.splitlines()[0]
    # Export without approval is forbidden.
    session.query(SamplingNote).delete();session.commit()
    with pytest.raises(HTTPException) as e:run_service.export_run(session,run.id,'bootstrap')
    assert e.value.status_code==403


def test_ensure_schema_upgrades_legacy_bayes_runs(tmp_path):
    engine=create_engine('sqlite:///'+str(tmp_path/'legacy.db'))
    with engine.begin() as connection:
        connection.execute(text('CREATE TABLE bayes_runs (id INTEGER PRIMARY KEY, name TEXT, study_id INTEGER, threshold FLOAT, future_n INTEGER, input_snapshot JSON, status TEXT, result JSON, error TEXT, owner TEXT, created_at TEXT)'))
    ensure_schema(engine)
    from sqlalchemy import inspect
    columns={c['name'] for c in inspect(engine).get_columns('bayes_runs')}
    assert {'bootstrap_seed','bootstrap_reps'}<=columns
    ensure_schema(engine)
