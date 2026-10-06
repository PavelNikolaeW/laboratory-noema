from decimal import Decimal as D
from . import engine
from .dto import INSTRUMENT
from .store import uid, now, encode

class LabError(Exception):
    def __init__(self,code,message,status=409,retryable=False):
        self.code,self.message,self.status,self.retryable=code,message,status,retryable

def fail(code,message,status=409,retryable=False): raise LabError(code,message,status,retryable)

def get(run,collection,id):
    value=run[collection].get(id)
    if value is None: fail('not_found','Ресурс не найден',404)
    return value

def event(run,kind,operation_id=None,payload=None):
    run['sequence']+=1
    run['events'].append({'event_id':uid(),'sequence':run['sequence'],'kind':kind,'operation_id':operation_id,'recorded_at':now(),'payload':payload or {}})

def public_sample(s):
    return {k:s[k] for k in ('id','revision','status','batch_id','provenance','history','observation_ids','created_at')}

def public_operation(op):
    return {k:op[k] for k in ('id','status','kind','instrument_id','inputs','parameters','prediction_id','result','error','created_at','completed_at')}

def replay(run,key,path,body):
    if not key or len(key)>200: fail('invalid_parameters','Нужен Idempotency-Key (до 200 символов)',422)
    signature=encode({'path':path,'body':body})
    previous=run['idempotency'].get(key)
    if previous:
        if previous['signature']!=signature: fail('idempotency_conflict','Ключ уже использован с другой командой')
        return previous['response'],signature
    return None,signature

def remember(run,key,signature,response):
    run['idempotency'][key]={'signature':signature,'response':response}
    return response

def check_inputs(run,body):
    states=[]
    for ref in body['inputs']:
        s=get(run,'samples',ref['sample_id'])
        if s['status']=='consumed': fail('sample_consumed','Порция уже израсходована')
        if s['status']=='busy': fail('sample_busy','Порция занята',retryable=True)
        if s['revision']!=ref['revision']: fail('stale_revision','Версия порции изменилась')
        states.append(s['state'])
    device=body.get('instrument_id')
    if device and any(o['status'] in ('accepted','running') and o['instrument_id']==device for o in run['operations'].values()): fail('instrument_busy','Прибор занят',retryable=True)
    return states

def reserve(run,amount,category):
    if D(run['credits'])-D(run['reserved'])<amount: fail('budget_exhausted','Недостаточно credits',429)
    run['reserved']=str(D(run['reserved'])+amount)

def accept(run,body):
    kind,p=body['kind'],body['parameters']
    states=check_inputs(run,body)
    price=engine.cost(kind,p)
    if kind=='obtain':
        batch=get(run,'batches',p['batch_id'])
        if D(batch['remaining_mass_g'])<D(p['mass_g']): fail('insufficient_material','Недостаточно материала')
    if kind=='reference' and get(run,'standards',p['standard_id'])['remaining_count']<1: fail('insufficient_material','Стандарт закончился')
    if kind in ('split','separate'):
        try: engine.transition(kind,states,p,run['config'])
        except ValueError: fail('invalid_parameters','Выход меньше минимального размера',422)
    if body.get('prediction_id'):
        prediction=get(run,'predictions',body['prediction_id'])
        planned=dict(body,prediction_id=None)
        if prediction.get('operation_id') or encode(prediction['planned_operation'])!=encode(planned): fail('invalid_prediction','Предсказание не соответствует новой операции')
    reserve(run,price,'research')
    op={'id':uid(),'status':'accepted',**body,'result':None,'error':None,'created_at':now(),'completed_at':None,'config':dict(run['config']),'price':str(price),'category':'research','states':states}
    run['operations'][op['id']]=op
    for ref in body['inputs']: run['samples'][ref['sample_id']]['status']='busy'
    if kind=='obtain': batch['remaining_mass_g']=str(D(batch['remaining_mass_g'])-D(p['mass_g']))
    if kind=='reference': run['standards'][p['standard_id']]['remaining_count']-=1
    if body.get('prediction_id'): prediction['operation_id']=op['id']
    event(run,'operation.accepted',op['id'])
    return public_operation(op)

def sample(run,state,batch_id,provenance,opid):
    s={'id':uid(),'created_at':now(),'revision':1,'status':'available','batch_id':batch_id,'provenance':provenance,'history':[opid],'observation_ids':[],'state':state}
    run['samples'][s['id']]=s
    return s

def observation(run,s,quantity,instrument,config):
    obs={'id':uid(),'sample_id':s['id'],'sample_revision':s['revision'],'quantity':quantity,'value':engine.fmt(engine.measure(s['state'],quantity,config)),'unit':{'mass':'g','temperature':'°C','response':'response_unit'}[quantity],'instrument_id':instrument,'recorded_at':now()}
    run['observations'][obs['id']]=obs
    s['observation_ids'].append(obs['id'])
    return obs

def accept_submission(run,body):
    from .research import active_orders
    order=get(run,'orders',body['order_id'])
    if body['order_id'] not in {order['id'] for order in active_orders(run)}:
        fail('order_inactive','Заявка больше не активна')
    recipe=get(run,'recipes',body['recipe_id'])
    batch=get(run,'batches',order['batch_id']); amount=D(order['input_mass_g'])*order['validation_repeats']
    if D(batch['verification_reserve_g'])<amount: fail('insufficient_material','Резерв проверки исчерпан')
    price=D(order['validation_cost']); reserve(run,price,'verification')
    batch['verification_reserve_g']=str(D(batch['verification_reserve_g'])-amount)
    sid,oid=uid(),uid()
    submission={'id':sid,'submission_id':sid,'operation_id':oid,**body,'status':'accepted','result':None,'created_at':now()}
    run['submissions'][sid]=submission
    op={'id':oid,'status':'accepted','kind':'validation','instrument_id':None,'inputs':[],'parameters':body,'prediction_id':None,'result':None,'error':None,'created_at':now(),'completed_at':None,'config':dict(run['config']),'price':str(price),'category':'verification','submission_id':sid,'recipe':recipe,'order':dict(order)}
    run['operations'][oid]=op; event(run,'operation.accepted',oid)
    return submission

def finish(run,op):
    kind,p,config=op['kind'],op['parameters'],op['config']; outputs=[]; observations=[]
    sources=[run['samples'][r['sample_id']] for r in op['inputs']]
    if kind=='validation':
        order,recipe=op['order'],op['recipe']; batch=run['batches'][order['batch_id']]; results=[]
        for repeat in range(order['validation_repeats']):
            state=engine.fresh(batch['fractions'],order['input_mass_g']); price=D(0)
            for step in recipe['steps']:
                states=engine.transition(step['kind'],[state],step['parameters'],config)
                state=states[1 if step.get('keep_output')=='remainder' else 0]
                price+=engine.cost(step['kind'],step['parameters'])
            state=engine.transition('cool',[state],{'target_temperature_c':'20'},config)[0]
            s=sample(run,state,order['batch_id'],{'kind':'verification','nominal_mass_g':order['input_mass_g'],'parents':[]},op['id'])
            obs=observation(run,s,'response','analyzer-1',config); observations.append(obs)
            measured=engine.measure(state,'response',config); y=engine.mass(state)/D(order['input_mass_g'])
            reasons=[]
            if measured<D(order['response_min']): reasons.append('response_below_minimum')
            if y<D(order['yield_min']): reasons.append('yield_below_minimum')
            if price>D(order['max_recipe_credits']): reasons.append('recipe_cost_exceeded')
            results.append({'repeat':repeat+1,'measured_response':engine.fmt(measured),'yield':engine.fmt(y),'recipe_credits':str(price),'reasons':reasons,'observation_ids':[obs['id']]})
            s['status']='consumed'; outputs.append(public_sample(s))
        result={'pass':all(not r['reasons'] for r in results),'repeats':results,'observations':observations}
        submission=run['submissions'][op['submission_id']]; submission.update(status='succeeded',result=result)
        if result['pass']: run['orders'][order['id']]['status']='completed'
    elif kind=='obtain':
        s=sample(run,engine.fresh(run['batches'][p['batch_id']]['fractions'],p['mass_g']),p['batch_id'],{'kind':'batch','nominal_mass_g':p['mass_g'],'parents':[]},op['id']); outputs.append(public_sample(s))
    elif kind=='reference':
        s=sample(run,engine.fresh(['.8','0','.2','0'],'10'),None,{'kind':'reference','standard_id':p['standard_id'],'nominal_mass_g':'10','parents':[]},op['id']); outputs.append(public_sample(s))
    elif kind.startswith('measure_'):
        observations.append(observation(run,sources[0],kind[8:],op['instrument_id'],config))
    else:
        states=engine.transition(kind,op['states'],p,config)
        if kind in ('heat','cool'):
            s=sources[0]; s['state']=states[0]; s['revision']+=1; s['history'].append(op['id']); outputs.append(public_sample(s))
        else:
            batch_ids={s['batch_id'] for s in sources}; bid=next(iter(batch_ids)) if len(batch_ids)==1 else None
            for index,state in enumerate(states):
                s=sample(run,state,bid,{'kind':kind,'parents':[r['sample_id'] for r in op['inputs']],'output':('selected' if index==0 else 'remainder') if kind=='separate' else str(index+1)},op['id']); outputs.append(public_sample(s))
            for s in sources: s['status']='consumed'; s['revision']+=1; s['history'].append(op['id'])
    for s in sources:
        if s['status']=='busy': s['status']='available'
    for out in outputs:
        if out['status']=='busy': out['status']='available'
    op.update(status='succeeded',completed_at=now(),result={'samples':outputs,'observations':observations} if kind!='validation' else result)
    run['reserved']=str(D(run['reserved'])-D(op['price'])); run['credits']=str(D(run['credits'])-D(op['price']))
    field='verification_spent' if op['category']=='verification' else 'research_spent'
    run[field]=str(D(run[field])+D(op['price']))
    event(run,'operation.succeeded',op['id'],{'result_ref':'/api/v1/operations/'+op['id']})

def abort(run,op):
    for ref in op['inputs']:
        s=run['samples'][ref['sample_id']]
        if s['status']=='busy': s['status']='available'
    p=op['parameters']
    if op['kind']=='obtain':
        b=run['batches'][p['batch_id']]; b['remaining_mass_g']=str(D(b['remaining_mass_g'])+D(p['mass_g']))
    if op['kind']=='reference': run['standards'][p['standard_id']]['remaining_count']+=1
    if op['kind']=='validation':
        order=op['order']; b=run['batches'][order['batch_id']]; b['verification_reserve_g']=str(D(b['verification_reserve_g'])+D(order['input_mass_g'])*order['validation_repeats'])
        run['submissions'][op['submission_id']]['status']='failed'
    run['reserved']=str(D(run['reserved'])-D(op['price']))
    op.update(status='failed',completed_at=now(),error={'code':'processing_failed','message':'Операция не выполнена; резерв возвращён','retryable':False})
    event(run,'operation.failed',op['id'])
