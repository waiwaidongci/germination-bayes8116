import json,os,time,traceback
from pathlib import Path
from .config.database import Base,engine,SessionLocal,ensure_schema
from .models import BayesRun
from .constants.status import RunStatus
from .services.run_service import queue
from .services.analysis_service import analyze_germination
from .services.bootstrap_cache import DatabaseFitCache
from .services.audit_service import log
ARTIFACTS=Path(os.environ.get("ARTIFACTS_DIR","/artifacts"))
Base.metadata.create_all(engine);ensure_schema(engine);ARTIFACTS.mkdir(parents=True,exist_ok=True)
while True:
    (ARTIFACTS/"heartbeat").write_text(str(time.time()))
    job=queue.blpop("germination:jobs",timeout=2)
    if not job:continue
    with SessionLocal() as db:
        run=db.get(BayesRun,int(job[1]))
        if not run or run.status!=RunStatus.QUEUED:continue
        run.status=RunStatus.RUNNING;db.commit()
        try:
            cache=DatabaseFitCache(db)
            run.result=analyze_germination(run.input_snapshot,run.threshold,run.future_n,run.bootstrap_seed,run.bootstrap_reps,cache=cache)
            run.status=RunStatus.COMPLETED
            (ARTIFACTS/f"run-{run.id}.json").write_text(json.dumps(run.result,ensure_ascii=False));log(db,"worker","bayes.completed",run.id)
        except Exception as e:
            db.rollback();run=db.get(BayesRun,int(job[1]));run.status=RunStatus.FAILED;run.error=str(e);traceback.print_exc()
        db.commit()
