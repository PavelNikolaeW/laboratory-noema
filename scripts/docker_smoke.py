"""Disposable container smoke; does not access any existing application volumes."""
import json
import subprocess
import time
import uuid
import httpx

name='noema-smoke-'+uuid.uuid4().hex[:10]
volume=name+'-data'
def docker(*args): return subprocess.check_output(['docker',*args],text=True).strip()
def wait_health(client):
    for _ in range(80):
        try:
            if client.get('/health').status_code==200: return
        except httpx.TransportError: pass
        time.sleep(.1)
    raise RuntimeError('Container health timeout')

docker('volume','create',volume)
try:
    credentials=json.loads(docker('run','--rm','-v',volume+':/data','noema-lab:0.1','python','-m','lab.admin','--db','/data/laboratory.db','create'))
    docker('run','-d','--name',name,'-p','127.0.0.1:8001:8000','-v',volume+':/data','noema-lab:0.1')
    with httpx.Client(base_url='http://127.0.0.1:8001',headers={'Authorization':'Bearer '+credentials['token']}) as client:
        wait_health(client)
        order=client.get('/api/v1/orders').json()['items'][0]
        response=client.post('/api/v1/recipes',headers={'Idempotency-Key':'recipe'},json={'name':'Docker smoke','steps':[{'kind':'heat','parameters':{'power':5,'duration':12}},{'kind':'cool','parameters':{'target_temperature_c':'20'}}]})
        response.raise_for_status();recipe=response.json()
        response=client.post('/api/v1/submissions',headers={'Idempotency-Key':'submission'},json={'order_id':order['id'],'recipe_id':recipe['id']})
        response.raise_for_status();submission=response.json()
        for _ in range(60):
            result=client.get('/api/v1/submissions/'+submission['id']).json()
            if result['status']=='succeeded': break
            time.sleep(.1)
        assert result['result']['pass'],result
        docker('restart',name);wait_health(client)
        assert client.get('/api/v1/submissions/'+submission['id']).json()==result
        assert client.get('/api/v1/session').json()['budget']['verification_spent']=='10'
    print('Docker smoke PASS: create → recipe → validation → restart → persisted result')
finally:
    subprocess.run(['docker','rm','-f',name],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
    subprocess.run(['docker','volume','rm',volume],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
