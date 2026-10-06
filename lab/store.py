import hashlib
import json
import os
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from sqlalchemy import create_engine
from .engine import DEFAULT_CONFIG

def uid(): return uuid.uuid4().hex

def now(): return datetime.now(timezone.utc).isoformat()

def token_hash(token): return hashlib.sha256(token.encode()).hexdigest()

def encode(value): return json.dumps(value, sort_keys=True, separators=(',',':'), ensure_ascii=False, allow_nan=False)

class Store:
    def __init__(self,path):
        self.engine = create_engine('sqlite:///'+path,connect_args={'timeout':30})
        with self.engine.connect() as c:
            c.exec_driver_sql('PRAGMA journal_mode=WAL')
            c.exec_driver_sql('CREATE TABLE IF NOT EXISTS runs (id TEXT PRIMARY KEY, token TEXT UNIQUE NOT NULL, data TEXT NOT NULL)')
            c.exec_driver_sql('PRAGMA user_version=1')
            c.commit()

    @contextmanager
    def transaction(self, token=None, run_id=None):
        # ponytail: one SQLite writer; use normalized PostgreSQL tables when concurrent run throughput requires it.
        with self.engine.connect() as c:
            c.exec_driver_sql('BEGIN IMMEDIATE')
            row = c.exec_driver_sql('SELECT id,data FROM runs WHERE '+('token=?' if token else 'id=?'),(token_hash(token) if token else run_id,)).first()
            if not row:
                c.rollback()
                raise PermissionError('Unknown participant')
            run = json.loads(row[1])
            try:
                yield run
                c.exec_driver_sql('UPDATE runs SET data=? WHERE id=?',(encode(run),row[0]))
                c.commit()
            except BaseException:
                c.rollback()
                raise

    def ids(self):
        with self.engine.connect() as c: return [r[0] for r in c.exec_driver_sql('SELECT id FROM runs')]

    def create(self,label='Участник',scenario='S1', mode='undisclosed', pair_id=None):
        token, rid = uid()+uid(), uid()
        batch, standard, order = uid(),uid(),uid()
        fractions = ['.6','0','.2','.2'] if scenario=='S2' else ['.6','0','.35','.05']
        run = {'id':rid,'label':label,'scenario':scenario,'mode':mode,'pair_id':pair_id,'created_at':now(),'config':dict(DEFAULT_CONFIG),'protocol':{'version':'0.1.0','initial_config':dict(DEFAULT_CONFIG),'engine_sha256':hashlib.sha256(__import__('pathlib').Path(__file__).with_name('engine.py').read_bytes()).hexdigest()},'private_events':[], 'credits':'100','reserved':'0','research_spent':'0','verification_spent':'0','sequence':0,'idempotency':{},'operations':{},'samples':{},'observations':{},'notes':{},'predictions':{},'recipes':{},'submissions':{},'events':[],
            'batches':{batch:{'id':batch,'label':'Партия покрытия','remaining_mass_g':'200','verification_reserve_g':'200','min_mass_g':'0.1','max_mass_g':'100','fractions':fractions}},
            'standards':{standard:{'id':standard,'label':'R-80','remaining_count':10,'certificate':{'mass_g':'10','response':'80','temperature_c':'20'}}},
            'orders':{order:{'id':order,'brief':'Подберите обработку покрытия и подтвердите рецепт на свежих порциях.','batch_id':batch,'input_mass_g':'20' if scenario=='S3' else '10','response_min':'90' if scenario=='S2' else '70','yield_min':'0.60' if scenario=='S2' else '0.80','max_recipe_credits':'15','validation_repeats':2,'validation_cost':'10','status':'open'}}}
        with self.engine.begin() as c: c.exec_driver_sql('INSERT INTO runs VALUES (?,?,?)',(rid,token_hash(token),encode(run)))
        return {'run_id':rid,'token':token}
