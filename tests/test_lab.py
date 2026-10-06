import json
import time
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal as D
import pytest
from fastapi.testclient import TestClient
from lab.app import create_app
from lab.store import uid
from lab import engine as e
from lab.dto import INSTRUMENT
from lab.service import accept, finish

@pytest.fixture
def world(tmp_path):
    app=create_app(str(tmp_path/'lab.db'),delay=.02)
    credentials=app.state.store.create()
    with TestClient(app) as c:
        c.headers['Authorization']='Bearer '+credentials['token']
        yield c,app,credentials

def post(c,path,body,key=None):
    return c.post('/api/v1/'+path,json=body,headers={'Idempotency-Key':key or uid()})

def body(kind,inputs=None,parameters=None):
    return {'kind':kind,'instrument_id':INSTRUMENT.get(kind),'inputs':inputs or [],'parameters':parameters or {},'prediction_id':None}

def wait(c,id):
    deadline=time.monotonic()+4
    while time.monotonic()<deadline:
        op=c.get('/api/v1/operations/'+id).json()
        if op['status'] in ('succeeded','failed'): return op
        time.sleep(.02)
    pytest.fail('Worker timeout')

def execute(c,kind,s=None,p=None):
    refs=[] if s is None else [{'sample_id':s['id'],'revision':s['revision']}]
    response=post(c,'operations',body(kind,refs,p))
    assert response.status_code==202,response.text
    op=wait(c,response.json()['id'])
    assert op['status']=='succeeded',op
    return op

def obtain(c,mass='10'):
    b=c.get('/api/v1/batches').json()['items'][0]
    return execute(c,'obtain',p={'batch_id':b['id'],'mass_g':mass})['result']['samples'][0]

HEAT={'kind':'heat','parameters':{'power':5,'duration':12}}
COOL={'kind':'cool','parameters':{'target_temperature_c':'20'}}

def submit(c,steps):
    r=post(c,'recipes',{'name':'Procedure','steps':steps}).json()
    order=c.get('/api/v1/orders').json()['items'][0]
    response=post(c,'submissions',{'order_id':order['id'],'recipe_id':r['id']})
    assert response.status_code==202,response.text
    wait(c,response.json()['operation_id'])
    return c.get('/api/v1/submissions/'+response.json()['id']).json()

# L01-L03: conservation, history, mixed activation, hot mixture threshold.
def test_engine_invariants():
    s=e.fresh(['.6','0','.35','.05'],'10')
    for kind,params in [('split',{'fraction':'.333333333333333333'}),('separate',{'mode':'selective'})]:
        parts=e.transition(kind,[s],params,e.DEFAULT_CONFIG)
        assert abs(sum(map(e.mass,parts))-e.mass(s))<D('1e-12')
        assert all(D(p[k])>=0 for p in parts for k in e.COMPONENTS)
        mixed=e.transition('mix',parts,{},e.DEFAULT_CONFIG)[0]
        assert abs(e.mass(mixed)-e.mass(s))<D('1e-12')
    hot=e.transition('heat',[s],{'power':5,'duration':12},e.DEFAULT_CONFIG)[0]
    cold=e.transition('cool',[hot],{'target_temperature_c':'20'},e.DEFAULT_CONFIG)[0]
    assert e.mass(hot)==e.mass(s) and cold['a1']==hot['a1'] and cold['a0']=='0'
    mixed=e.transition('mix',[cold,s],{},e.DEFAULT_CONFIG)[0]
    assert D(mixed['a0'])==6 and D(mixed['a1'])==6
    very_hot=e.transition('heat',[s],{'power':10,'duration':120},e.DEFAULT_CONFIG)[0]
    mixed=e.transition('mix',[very_hot,s],{},e.DEFAULT_CONFIG)[0]
    assert mixed['a0']=='0'

# L04, L05, L20-L23.
@pytest.mark.parametrize('scenario,steps,expected',[('S1',[HEAT,COOL],True),('S2',[HEAT,COOL,{'kind':'separate','parameters':{'mode':'selective'},'keep_output':'selected'}],True),('S3',[HEAT,COOL],False),('S3',[{'kind':'heat','parameters':{'power':10,'duration':12}},COOL],True)])
def test_fixtures(tmp_path,scenario,steps,expected):
    app=create_app(str(tmp_path/'lab.db'),delay=0)
    auth=app.state.store.create(scenario=scenario)
    with TestClient(app) as c:
        c.headers['Authorization']='Bearer '+auth['token']
        result=submit(c,steps)['result']
        assert result['pass'] is expected
        assert len(result['repeats'])==2
        if scenario=='S2': assert result['repeats'][0]['yield']=='0.660000' and result['repeats'][0]['measured_response']=='100.000000'
        if scenario=='S1': assert result['repeats'][0]['measured_response']=='73.000000' and result['repeats'][0]['recipe_credits']=='4'

def test_gain_change(world):
    c,app,auth=world
    assert submit(c,[HEAT,COOL])['result']['pass']
    with app.state.store.transaction(run_id=auth['run_id']) as r: r['config']['heater_gain']='.5'
    assert not submit(c,[HEAT,COOL])['result']['pass']
    assert submit(c,[{'kind':'heat','parameters':{'power':10,'duration':12}},COOL])['result']['pass']
    budget=c.get('/api/v1/session').json()['budget']
    assert budget['research_spent']=='0' and budget['verification_spent']=='30'

# L06-L09, L26: stale measurements and replay.
def test_observations_and_idempotency(world):
    c,app,auth=world;s=obtain(c)
    first=execute(c,'measure_response',s)['result']['observations'][0]
    second=execute(c,'measure_response',s)['result']['observations'][0]
    assert first['value']==second['value']=='55.000000'
    assert c.get('/api/v1/samples/'+s['id']).json()['revision']==1
    request=body('heat',[{'sample_id':s['id'],'revision':1}],{'power':5,'duration':12});key=uid()
    accepted=post(c,'operations',request,key);wait(c,accepted.json()['id'])
    repeated=post(c,'operations',request,key)
    assert repeated.json()==accepted.json()
    assert post(c,'operations',dict(request,parameters={'power':4,'duration':12}),key).status_code==409
    assert post(c,'notes',{'text':'x'},key).status_code==409
    observations=c.get('/api/v1/observations').json()['items']
    assert observations[0]==first and observations[0]['sample_revision']==1
    snapshot=c.get('/api/v1/samples/'+s['id']).json()
    assert snapshot['revision']==2 and 'temperature' not in snapshot and 'state' not in snapshot
    for _ in range(10): assert c.get('/api/v1/samples/'+s['id']).json()==snapshot
    assert post(c,'operations',request).json()['error']['code']=='stale_revision'

# L10-L12.
def test_busy_consumed_and_budget(world):
    c,app,auth=world;s=obtain(c)
    app_delay_request=body('heat',[{'sample_id':s['id'],'revision':1}],{'power':1,'duration':1})
    with ThreadPoolExecutor(2) as pool:
        responses=list(pool.map(lambda _:post(c,'operations',app_delay_request),range(2)))
    assert sorted(r.status_code for r in responses)==[202,409]
    wait(c,next(r.json()['id'] for r in responses if r.status_code==202))
    s=c.get('/api/v1/samples/'+s['id']).json()
    execute(c,'split',s,{'fraction':'.5'})
    assert post(c,'operations',body('measure_mass',[{'sample_id':s['id'],'revision':s['revision']}])).status_code==409
    assert post(c,'operations',body('mix',[{'sample_id':s['id'],'revision':1}]*2)).status_code==422
    b=c.get('/api/v1/batches').json()['items'][0]
    with app.state.store.transaction(run_id=auth['run_id']) as r: r['credits']='0'
    before=c.get('/api/v1/batches').json()
    assert post(c,'operations',body('obtain',parameters={'batch_id':b['id'],'mass_g':'10'})).status_code==429
    assert c.get('/api/v1/batches').json()==before
    with app.state.store.transaction(run_id=auth['run_id']) as r: r['credits']='100';r['batches'][b['id']]['remaining_mass_g']='1'
    assert post(c,'operations',body('obtain',parameters={'batch_id':b['id'],'mass_g':'10'})).json()['error']['code']=='insufficient_material'

# L13-L15: restart between acceptance/calculation/commit, stable event pages.
def test_restart(tmp_path):
    db=str(tmp_path/'lab.db');app=create_app(db,delay=0);auth=app.state.store.create()
    with app.state.store.transaction(run_id=auth['run_id']) as r:
        bid=next(iter(r['batches']));op=accept(r,body('obtain',parameters={'batch_id':bid,'mass_g':'10'}))
    # Calculation is rolled back as if the worker died before effect commit.
    with pytest.raises(RuntimeError):
        with app.state.store.transaction(run_id=auth['run_id']) as r:
            r['operations'][op['id']]['status']='running';finish(r,r['operations'][op['id']]);raise RuntimeError('crash')
    restarted=create_app(db,delay=0)
    with TestClient(restarted) as c:
        c.headers['Authorization']='Bearer '+auth['token']
        completed=wait(c,op['id']);assert completed['status']=='succeeded'
        assert len(c.get('/api/v1/samples').json()['items'])==1
        assert c.get('/api/v1/batches').json()['items'][0]['remaining_mass_g']=='190'
        pages=c.get('/api/v1/events?limit=1').json();assert pages==c.get('/api/v1/events?limit=1').json()
        next_page=c.get('/api/v1/events?after='+str(pages['next_after'])).json();assert next_page['items'][0]['sequence']>pages['next_after']
    restarted=create_app(db,delay=0)
    with TestClient(restarted) as c:
        c.headers['Authorization']='Bearer '+auth['token']
        assert c.get('/api/v1/operations/'+op['id']).json()==completed
        assert c.get('/api/v1/session').json()['budget']['research_spent']=='1'

# L16-L19, L21, L27-L28.
def test_boundary_prediction_and_validation(world):
    c,app,auth=world;s=obtain(c)
    planned=body('measure_response',[{'sample_id':s['id'],'revision':1}]);p=post(c,'predictions',{'quantity':'response','expected_value':'55','planned_operation':planned})
    assert p.status_code==201,p.text
    request=dict(planned,prediction_id=p.json()['id']);result=post(c,'operations',request);wait(c,result.json()['id'])
    assert post(c,'operations',request).status_code==409
    # New prediction can target a new repeated measurement, never mutate an accepted operation.
    assert post(c,'predictions',{'quantity':'response','expected_value':'55','planned_operation':planned}).status_code==201
    with app.state.store.transaction(run_id=auth['run_id']) as r:
        from lab.metrics import summarize
        metrics=summarize(r)
        assert metrics['predictions']['scored']==1 and D(metrics['predictions']['by_quantity'][0]['mae'])==0
    for value in ('NaN','Infinity','-1','1000','0.1001'):
        b=c.get('/api/v1/batches').json()['items'][0]
        assert post(c,'operations',body('obtain',parameters={'batch_id':b['id'],'mass_g':value})).status_code==422
    assert post(c,'notes',{'text':'x','secret':1}).status_code==422
    assert post(c,'recipes',{'name':'x','steps':[{'kind':'mix','parameters':{}}]}).status_code==422
    assert post(c,'submissions',{'order_id':next(iter(app.state.store.ids())),'recipe_id':s['id']}).status_code==404
    other=app.state.store.create()
    with app.state.store.transaction(token=other['token']) as r: foreign=next(iter(r['orders']))
    assert c.get('/api/v1/orders/'+foreign).status_code==404
    assert c.post('/api/v1/admin/reset').status_code==404
    with TestClient(app) as anon: assert anon.get('/api/v1/session').status_code==401
    exposed=json.dumps([c.get('/api/openapi.json').json(),c.get('/api/v1/session').json(),c.get('/api/v1/instruments').json(),c.get('/api/v1/batches').json(),c.get('/api/v1/samples').json(),c.get('/api/v1/operations/'+result.json()['id']).json()])
    for secret in ('heater_gain','activation_threshold','analyzer_gain','fractions','a0','a1','config','states'):
        assert '"'+secret+'"' not in exposed
    assert post(c,'notes',{'text':'hypothesis'},key=None).status_code==201
    assert c.post('/api/v1/notes',json={'text':'x'}).status_code==422

# L22 and reference lineage, submission snapshot.
def test_snapshot_and_reference(world):
    c,app,auth=world
    standard=c.get('/api/v1/standards').json()['items'][0]
    s=execute(c,'reference',p={'standard_id':standard['id']})['result']['samples'][0]
    parts=execute(c,'split',s,{'fraction':'.5'})['result']['samples']
    assert parts[0]['provenance']['parents']==[s['id']]
    recipe=post(c,'recipes',{'name':'x','steps':[HEAT,COOL]}).json();order=c.get('/api/v1/orders').json()['items'][0]
    response=post(c,'submissions',{'order_id':order['id'],'recipe_id':recipe['id']}).json()
    with app.state.store.transaction(run_id=auth['run_id']) as r: r['config']['heater_gain']='.5'
    wait(c,response['operation_id'])
    assert c.get('/api/v1/submissions/'+response['id']).json()['result']['pass']

# Actual subprocess crash; accepted reservation is recovered on process restart.
def test_process_kill_and_sdk(tmp_path):
    import os
    import socket
    import subprocess
    import sys
    import asyncio
    import httpx
    from sdk.client import LabClient
    from lab.store import Store
    db=str(tmp_path/'crash.db');auth=Store(db).create()
    with socket.socket() as sock:
        sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
    url=f'http://127.0.0.1:{port}'
    env={**os.environ,'LAB_DB':db,'LAB_DELAY':'10'}
    def start():
        process=subprocess.Popen([sys.executable,'-m','uvicorn','lab.app:app','--port',str(port)],env=env,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        deadline=time.monotonic()+8
        while time.monotonic()<deadline:
            try:
                if httpx.get(url+'/health').status_code==200: return process
            except httpx.TransportError: pass
            time.sleep(.05)
        process.kill();process.wait();pytest.fail('Server startup timed out')
    process=start()
    headers={'Authorization':'Bearer '+auth['token'],'Idempotency-Key':uid()}
    with httpx.Client(base_url=url,headers=headers) as client:
        batch=client.get('/api/v1/batches').json()['items'][0]
        request=body('obtain',parameters={'batch_id':batch['id'],'mass_g':'10'})
        accepted=client.post('/api/v1/operations',json=request).json()
    process.kill();process.wait()
    env['LAB_DELAY']='0'
    process=start()
    try:
        async def resume():
            async with LabClient(url,auth['token']) as lab:
                result=await lab.wait(accepted['id'])
                assert result['status']=='succeeded'
                replayed=await lab.operate(request,headers['Idempotency-Key'])
                assert replayed==accepted
                samples=await lab.get('samples');assert len(samples['items'])==1
                assert (await lab.get('session'))['budget']['research_spent']=='1'
        asyncio.run(resume())
    finally:
        process.terminate();process.wait(timeout=5)


def test_failure_refund_and_pagination(world,monkeypatch):
    c,app,auth=world
    with app.state.store.transaction(run_id=auth['run_id']) as r:
        bid=next(iter(r['batches']))
        op=accept(r,body('obtain',parameters={'batch_id':bid,'mass_g':'10'}))
        from lab.service import abort
        abort(r,r['operations'][op['id']])
    assert c.get('/api/v1/session').json()['budget']['reserved_credits']=='0'
    assert c.get('/api/v1/batches').json()['items'][0]['remaining_mass_g']=='200'
    for text in ('first','second','third'): assert post(c,'notes',{'text':text}).status_code==201
    first=c.get('/api/v1/notes?limit=1').json();second=c.get('/api/v1/notes?limit=1&cursor='+first['next_cursor']).json()
    assert first['items'][0]['text']=='first' and second['items'][0]['text']=='second'
    assert first==c.get('/api/v1/notes?limit=1').json()
    assert c.get('/api/v1/notes?limit=201').status_code==422
    assert c.post('/api/v1/notes',content='x'*70000,headers={'Content-Type':'application/json','Idempotency-Key':uid()}).status_code==413


def test_admin_boundary_exports_metrics(world,tmp_path):
    import subprocess
    import sys
    from lab.metrics import summarize
    c,app,auth=world
    database=app.state.store.engine.url.database
    command=[sys.executable,'-m','lab.admin','--db',database]
    with app.state.store.transaction(run_id=auth['run_id']) as r:
        bid=next(iter(r['batches']));op=accept(r,body('obtain',parameters={'batch_id':bid,'mass_g':'10'}))
    wait(c,op['id'])
    fresh_app=create_app(str(tmp_path/'boundary.db'))
    second=fresh_app.state.store.create(mode='notified')
    with fresh_app.state.store.transaction(run_id=second['run_id']) as r:
        bid=next(iter(r['batches']));accepted=accept(r,body('obtain',parameters={'batch_id':bid,'mass_g':'10'}))
    other=[sys.executable,'-m','lab.admin','--db',str(tmp_path/'boundary.db')]
    result=subprocess.run([*other,'gain',second['run_id'],'.5'],capture_output=True,text=True)
    assert result.returncode==2 and 'safe operation boundary' in result.stderr
    with fresh_app.state.store.transaction(run_id=second['run_id']) as r: finish(r,r['operations'][accepted['id']])
    assert subprocess.run([*other,'gain',second['run_id'],'.5'],capture_output=True).returncode==0
    output=tmp_path/'public.json'
    assert subprocess.run([*other,'export',second['run_id'],'--output',str(output)],capture_output=True).returncode==0
    trace=json.loads(output.read_text())
    assert 'config' not in trace and any(e['kind']=='maintenance' for e in trace['events'])
    assert '.5' not in json.dumps(trace['events'])
    with app.state.store.transaction(run_id=auth['run_id']) as r:
        assert summarize(r)['credits']['research']=='1'
    metrics=subprocess.run([*command,'metrics',auth['run_id']],capture_output=True,text=True)
    assert metrics.returncode==0 and json.loads(metrics.stdout)['run_id']==auth['run_id']


def test_local_new_attempt_preserves_old_and_retries(world):
    from lab.local_control import create_control, ORIGIN
    c,app,auth=world
    sample=obtain(c)
    post(c,'notes',{'text':'keep my experiment'})
    previous=c.get('/api/v1/session').json()
    with TestClient(create_control(app.state.store.engine.url.database,secret='local-human-test-key')) as control:
        headers={'Origin':ORIGIN,'Authorization':'Bearer '+auth['token'],'Idempotency-Key':'new-attempt'}
        assert control.get('/capabilities').json()=={'new_attempt':False}
        assert control.post('/attempts',headers=headers).status_code==403
        control.cookies.set('lab-local-control','local-human-test-key')
        assert control.get('/capabilities').json()=={'new_attempt':True}
        assert control.post('/attempts',headers={k:v for k,v in headers.items() if k!='Origin'}).status_code==403
        assert control.post('/attempts',headers={**headers,'Origin':'https://evil.example'}).status_code==403
        assert control.post('/attempts',headers={**headers,'Authorization':'Bearer invalid'}).status_code==401
        assert control.post('/attempts',headers=headers,json={'scenario':'S2'}).status_code==422
        created=control.post('/attempts',headers=headers)
        assert created.status_code==201
        credentials=created.json()
        assert control.post('/attempts',headers=headers).json()==credentials
        assert credentials['run_id']!=auth['run_id']
    # Public listener never offers this capability, even with valid participant credentials.
    assert c.post('/api/v1/attempts').status_code==404
    assert c.post('/attempts').status_code==404
    assert c.get('/api/v1/session').json()==previous
    assert c.get('/api/v1/notes').json()['items'][0]['text']=='keep my experiment'
    assert c.get('/api/v1/samples/'+sample['id']).status_code==200
    c.headers['Authorization']='Bearer '+credentials['token']
    fresh=c.get('/api/v1/session').json()
    assert fresh['budget']['remaining_credits']=='100'
    assert fresh['orders'][0]['input_mass_g']==previous['orders'][0]['input_mass_g']
    assert fresh['orders'][0]['status']=='open'
    assert c.get('/api/v1/samples').json()['items']==[]
    assert c.get('/api/v1/notes').json()['items']==[]
    assert c.get('/api/v1/samples/'+sample['id']).status_code==404
