from pydantic import BaseModel,Field,ConfigDict,model_validator
from ..services.bootstrap_algorithm import MIN_REPS,MAX_REPS,MAX_SEED
class RunInput(BaseModel):
    model_config=ConfigDict(extra="forbid")
    name:str=Field(min_length=2,max_length=160)
    study_id:int=Field(gt=0,strict=True)
    threshold:float=Field(default=.7,gt=0,lt=1,allow_inf_nan=False,strict=True)
    future_n:int=Field(default=50,ge=1,le=200,strict=True)
    bootstrap_seed:int|None=Field(default=None,ge=0,le=MAX_SEED,strict=True)
    bootstrap_reps:int|None=Field(default=None,ge=MIN_REPS,le=MAX_REPS,strict=True)
    @model_validator(mode='after')
    def bootstrap_pair(self):
        if (self.bootstrap_seed is None)!=(self.bootstrap_reps is None):raise ValueError("重抽样种子与次数须同时提供或同时省略")
        return self
class ScenarioInput(BaseModel):
    model_config=ConfigDict(extra="forbid")
    threshold:float=Field(default=.7,gt=0,lt=1,allow_inf_nan=False,strict=True)
    future_n:int=Field(default=50,ge=1,le=200,strict=True)
