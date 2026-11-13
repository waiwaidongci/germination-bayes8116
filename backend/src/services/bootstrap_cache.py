"""Persistent cache for batch-bootstrap refits.

A cache entry is valid only when its version, batch data hash, seed and reps all
match.  Updating any n/k changes the data hash, so the entry is ignored and the
fit is recomputed instead of silently reusing stale shapes.
"""
from ..models import BootstrapFit
from ..utils.serialize import record
from .bootstrap_algorithm import BOOTSTRAP_VERSION


class DatabaseFitCache:
    def __init__(self,db):
        self.db=db
        self.hits=0
        self.misses=0

    def _valid(self,row,data_hash,seed,reps):
        return bool(row and row.data_hash==data_hash and row.seed==seed and row.reps==reps)

    def get(self,key,data_hash=None,seed=None,reps=None):
        row=self.db.query(BootstrapFit).filter_by(cache_key=key).first()
        if not self._valid(row,data_hash,seed,reps):
            # Missing, stale or mismatched: never use it for this fit.
            self.misses+=1
            return None
        self.hits+=1
        return {'version':BOOTSTRAP_VERSION,'data_hash':row.data_hash,'seed':row.seed,'requested_reps':row.reps,'status':row.status,'replicate_fits':row.fits}

    def set(self,key,payload):
        row=self.db.query(BootstrapFit).filter_by(cache_key=key).first()
        data_hash=payload['data_hash'];seed=int(payload['seed']);reps=int(payload['requested_reps']);fits=payload['replicate_fits']
        status='complete' if all(x.get('status')=='success' for x in fits) else ('partial' if any(x.get('status')=='success' for x in fits) else 'failed')
        if row is None:row=BootstrapFit(cache_key=key,data_hash=data_hash,seed=seed,reps=reps,status=status,fits=fits);self.db.add(row)
        else:row.data_hash=data_hash;row.seed=seed;row.reps=reps;row.status=status;row.fits=fits
        self.db.flush()
        return payload

    def describe(self,key=None):
        query=self.db.query(BootstrapFit).order_by(BootstrapFit.id.desc())
        if key is not None:query=query.filter_by(cache_key=key)
        return [record(row) for row in query.all()]
