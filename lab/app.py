import asyncio
import logging
import os
import time
from contextlib import asynccontextmanager
from decimal import Decimal as D
from pathlib import Path
from fastapi import FastAPI, Request, Depends, Header, Query
from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException
from fastapi.responses import JSONResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from . import dto
from .store import Store, uid, now, encode
from .dto import Operation, Recipe, Note, Prediction, Submission, INSTRUMENT, PublicOperation, PublicSample, Observation, EventPage, ErrorResponse
from .service import LabError, fail, get, public_sample, public_operation, accept, accept_submission, replay, remember, event, finish, abort, check_inputs

logger=logging.getLogger('lab')
STATIC=Path(__file__).parent/'static'

def create_app(database=None,delay=None):
    store=Store(database or os.getenv('LAB_DB','laboratory.db'))
    processing_delay=float(os.getenv('LAB_DELAY','0.35')) if delay is None else delay

    async def worker():
        while True:
            pending=[]
            for rid in store.ids():
                with store.transaction(run_id=rid) as run:
                    for op in run['operations'].values():
                        if op['status'] in ('accepted','running'):
                            op['status']='running'; pending.append((rid,op['id']))
            for rid,oid in pending:
                await asyncio.sleep(processing_delay)
                try:
                    with store.transaction(run_id=rid) as run:
                        op=run['operations'][oid]
                        if op['status'] in ('accepted','running'): finish(run,op)
                except Exception:
                    logger.exception('Private worker failure')
                    with store.transaction(run_id=rid) as run:
                        op=run['operations'][oid]
                        if op['status'] in ('accepted','running'): abort(run,op)
            await asyncio.sleep(.05)

    @asynccontextmanager
    async def lifespan(app):
        task=asyncio.create_task(worker())
        yield
        task.cancel()
        try: await task
        except asyncio.CancelledError: pass

    app=FastAPI(title='Research Lab — Public API',version='0.1.0',lifespan=lifespan,responses={status:{'model':ErrorResponse} for status in (401,403,404,409,413,422,429,500)},docs_url='/api/docs',openapi_url='/api/openapi.json')
    app.state.store=store

    @app.middleware('http')
    async def payload_limit(request,call_next):
        if request.method=='POST':
            body=await request.body()
            if len(body)>65536:
                return JSONResponse(status_code=413,content={'error':{'code':'payload_too_large','message':'Максимальный запрос: 64 KiB','retryable':False},'request_id':uid()})
        return await call_next(request)

    @app.exception_handler(LabError)
    async def error(request,e):
        return JSONResponse(status_code=e.status,content={'error':{'code':e.code,'message':e.message,'retryable':e.retryable},'request_id':uid()})

    @app.exception_handler(HTTPException)
    async def http_error(request,e):
        return JSONResponse(status_code=e.status_code,content={'error':{'code':'not_found' if e.status_code==404 else 'http_error','message':'Ресурс не найден' if e.status_code==404 else 'Запрос отклонён','retryable':False},'request_id':uid()})

    @app.exception_handler(RequestValidationError)
    async def invalid(request,e):
        return JSONResponse(status_code=422,content={'error':{'code':'invalid_parameters','message':'Проверьте поля, типы и диапазоны параметров','retryable':False},'request_id':uid()})

    @app.exception_handler(Exception)
    async def unexpected(request,e):
        logger.exception('Private request failure')
        return JSONResponse(status_code=500,content={'error':{'code':'internal_error','message':'Ошибка сервиса','retryable':True},'request_id':uid()})

    def participant(authorization: str | None=Header(default=None)):
        if not authorization or not authorization.startswith('Bearer '): fail('unauthorized','Нужен токен участника',401)
        token=authorization[7:]
        try:
            with store.transaction(token=token): pass
        except PermissionError: fail('unauthorized','Токен не найден',401)
        return token

    @app.get('/',include_in_schema=False)
    def index(): return FileResponse(STATIC/'index.html')

    app.mount('/static',StaticFiles(directory=STATIC),name='static')

    @app.get('/health',include_in_schema=False)
    def health(): return {'status':'ok'}

    @app.get('/api/v1/session',response_model=dto.Session)
    def session(token=Depends(participant)):
        with store.transaction(token=token) as r:
            return {'run_id':r['id'],'actor':{'label':r['label']},'available_actions':list(Operation.model_fields['kind'].annotation.__args__), 'budget':{'remaining_credits':r['credits'],'reserved_credits':r['reserved'],'available_credits':str(D(r['credits'])-D(r['reserved'])),'research_spent':r['research_spent'],'verification_spent':r['verification_spent'],'llm_cost':'tracked_by_harness'},'orders':list(r['orders'].values()),'last_event_sequence':r['sequence']}

    @app.get('/api/v1/instruments',response_model=dto.Items[dto.PublicInstrument])
    def instruments(token=Depends(participant)):
        passports=[]
        limits={'power':{'min':1,'max':10,'unit':'level'},'duration':{'min':1,'max':120,'unit':'tick'},'target_temperature_c':{'min':'20','max':'200','unit':'°C'},'mass_g':{'min':'0.1','max':'100','precision':'0.001','unit':'g'},'fraction':{'min':'0','max':'1','exclusive':True},'minimum_output_mass_g':'0.001'}
        costs={'obtain':'1','reference':'2','split':'1','heat':'2 + power × duration / 60','cool':'1','separate':'3','mix':'2','measure_mass':'1','measure_temperature':'1','measure_response':'1'}
        with store.transaction(token=token) as r:
            for kind in Operation.model_fields['kind'].annotation.__args__:
                device=INSTRUMENT.get(kind)
                busy=device and any(o['instrument_id']==device and o['status'] in ('accepted','running') for o in r['operations'].values())
                passports.append({'id':device,'type':kind,'status':'busy' if busy else 'available','cost_credits':costs[kind],'limits':limits,'action_schema':Operation.model_json_schema()})
        return {'items':passports}

    @app.get('/api/v1/batches',response_model=dto.Items[dto.PublicBatch])
    def batches(token=Depends(participant)):
        with store.transaction(token=token) as r: return {'items':[{k:v for k,v in b.items() if k!='fractions'} for b in r['batches'].values()]}

    @app.get('/api/v1/standards',response_model=dto.Items[dto.PublicStandard])
    def standards(token=Depends(participant)):
        with store.transaction(token=token) as r: return {'items':list(r['standards'].values())}

    @app.get('/api/v1/events',response_model=EventPage)
    def events(after:int=Query(0,ge=0),limit:int=Query(100,ge=1,le=200),token=Depends(participant)):
        with store.transaction(token=token) as r:
            candidates=[e for e in r['events'] if e['sequence']>after]; items=candidates[:limit]
            return {'items':items,'next_after':items[-1]['sequence'] if items else after,'has_more':len(candidates)>limit}

    @app.get('/api/v1/samples',response_model=dto.Page[dto.PublicSample])
    @app.get('/api/v1/observations',response_model=dto.Page[dto.Observation])
    @app.get('/api/v1/notes',response_model=dto.Page[dto.SavedNote])
    @app.get('/api/v1/predictions',response_model=dto.Page[dto.SavedPrediction])
    @app.get('/api/v1/orders',response_model=dto.Page[dto.PublicOrder])
    @app.get('/api/v1/recipes',response_model=dto.Page[dto.SavedRecipe])
    @app.get('/api/v1/submissions',response_model=dto.Page[dto.PublicSubmission])
    def listing(request:Request,cursor:str|None=None,limit:int=Query(100,ge=1,le=200),sample_id:str|None=None,status:str|None=None,batch_id:str|None=None,token=Depends(participant)):
        collection=request.url.path.rsplit('/',1)[-1]
        with store.transaction(token=token) as r:
            values=sorted(r[collection].values(),key=lambda v:(v.get('created_at',v.get('recorded_at','')),v['id']))
            if status: values=[v for v in values if v.get('status')==status]
            if batch_id: values=[v for v in values if v.get('batch_id')==batch_id]
            if sample_id: values=[v for v in values if v.get('sample_id')==sample_id or sample_id in v.get('sample_ids',[])]
            start=0
            if cursor:
                positions=[i for i,v in enumerate(values) if v['id']==cursor]
                if not positions: fail('invalid_parameters','Неизвестный cursor',422)
                start=positions[0]+1
            page=values[start:start+limit]
            if collection=='samples': page=[public_sample(v) for v in page]
            return {'items':page,'next_cursor':page[-1]['id'] if page and start+limit<len(values) else None,'has_more':start+limit<len(values)}

    @app.get('/api/v1/samples/{id}',response_model=PublicSample)
    @app.get('/api/v1/operations/{id}',response_model=PublicOperation)
    @app.get('/api/v1/orders/{id}',response_model=dto.PublicOrder)
    @app.get('/api/v1/recipes/{id}',response_model=dto.SavedRecipe)
    @app.get('/api/v1/submissions/{id}',response_model=dto.PublicSubmission)
    def detail(id:str,request:Request,token=Depends(participant)):
        collection=request.url.path.split('/')[-2]
        with store.transaction(token=token) as r:
            value=get(r,collection,id)
            return public_sample(value) if collection=='samples' else public_operation(value) if collection=='operations' else value

    def post_resource(token,key,path,body,create):
        with store.transaction(token=token) as r:
            previous,signature=replay(r,key,path,body)
            if previous is not None: return previous
            timestamp=time.time()
            recent=[t for t in r.get('write_timestamps',[]) if t>timestamp-60]
            if len(recent)>=120: fail('rate_limit','Не более 120 новых команд в минуту',429,True)
            response=create(r,body)
            r['write_timestamps']=[*recent,timestamp]
            return remember(r,key,signature,response)

    @app.post('/api/v1/operations',status_code=202,response_model=PublicOperation)
    def operation(body:Operation,token=Depends(participant),key:str|None=Header(None,alias='Idempotency-Key')):
        return post_resource(token,key,'operations',body.model_dump(),accept)

    @app.post('/api/v1/submissions',status_code=202,response_model=dto.PublicSubmission)
    def submission(body:Submission,token=Depends(participant),key:str|None=Header(None,alias='Idempotency-Key')):
        return post_resource(token,key,'submissions',body.model_dump(),accept_submission)

    def save(collection):
        def create(r,body):
            if collection=='notes':
                for id in body['sample_ids']: get(r,'samples',id)
                for id in body['observation_ids']: get(r,'observations',id)
                if body.get('order_id'): get(r,'orders',body['order_id'])
                if body.get('supersedes'): get(r,'notes',body['supersedes'])
            if collection=='recipes':
                for id in body['evidence_ids']:
                    if id not in r['observations'] and id not in r['operations']: fail('not_found','Свидетельство не найдено',404)
            if collection=='predictions':
                planned=body['planned_operation']; check_inputs(r,planned)
            record={'id':uid(),**body,'created_at':now()}
            r[collection][record['id']]=record; event(r,collection+'.created',payload={'id':record['id']})
            return record
        return create

    @app.post('/api/v1/notes',status_code=201,response_model=dto.SavedNote)
    def note(body:Note,token=Depends(participant),key:str|None=Header(None,alias='Idempotency-Key')):
        return post_resource(token,key,'notes',body.model_dump(),save('notes'))

    @app.post('/api/v1/recipes',status_code=201,response_model=dto.SavedRecipe)
    def recipe(body:Recipe,token=Depends(participant),key:str|None=Header(None,alias='Idempotency-Key')):
        return post_resource(token,key,'recipes',body.model_dump(),save('recipes'))

    @app.post('/api/v1/predictions',status_code=201,response_model=dto.SavedPrediction)
    def prediction(body:Prediction,token=Depends(participant),key:str|None=Header(None,alias='Idempotency-Key')):
        return post_resource(token,key,'predictions',body.model_dump(),save('predictions'))

    return app

app=create_app()
