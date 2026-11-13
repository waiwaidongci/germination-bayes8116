from typing import Literal
from fastapi import APIRouter,Depends
from fastapi.responses import Response
from sqlalchemy.orm import Session
from ..config.database import get_db
from ..controllers.dependencies import current_user,roles
from ..constants.roles import Role
from ..schemas.run import RunInput,ScenarioInput
from ..services.run_service import list_runs,get_run,create_run,resample_run,run_scenario,export_run
from ..services.bootstrap_cache import DatabaseFitCache
from ..utils.serialize import record
router=APIRouter(prefix='/runs',tags=['runs'])
@router.get('')
def index(db:Session=Depends(get_db),user=Depends(current_user)):return list_runs(db)
@router.get('/{rid}')
def show(rid:int,db:Session=Depends(get_db),user=Depends(current_user)):return record(get_run(db,rid))
@router.post('',status_code=202)
def create(data:RunInput,db:Session=Depends(get_db),user=Depends(roles(Role.ADMIN,Role.ANALYST))):return create_run(db,data,user)
@router.post('/{rid}/resample',status_code=202)
def resample(rid:int,data:RunInput,db:Session=Depends(get_db),user=Depends(roles(Role.ADMIN,Role.ANALYST))):return resample_run(db,rid,data,user)
@router.post('/{rid}/scenario')
def scenario(rid:int,data:ScenarioInput,db:Session=Depends(get_db),user=Depends(roles(Role.ADMIN,Role.ANALYST))):return run_scenario(db,rid,data)
@router.get('/{rid}/bootstrap-cache')
def bootstrap_cache(rid:int,db:Session=Depends(get_db),user=Depends(current_user)):
    row=get_run(db,rid);b=(row.result or {}).get('bootstrap') or {}
    entries=DatabaseFitCache(db).describe(b.get('cache_key')) if b.get('cache_key') else []
    return {'run_id':rid,'cache_key':b.get('cache_key'),'entries':entries}
@router.get('/{rid}/export')
def export(rid:int,kind:Literal['summary','predictive','bootstrap']='summary',db:Session=Depends(get_db),user=Depends(current_user)):return Response(content='﻿'+export_run(db,rid,kind),media_type='text/csv',headers={'Content-Disposition':f'attachment; filename="germination-{rid}-{kind}.csv"'})
