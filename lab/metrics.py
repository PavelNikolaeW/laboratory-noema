"""Metrics derived exclusively from the public trace."""
from decimal import Decimal as D

def summarize(run):
    groups={}
    for prediction in run['predictions'].values():
        op=run['operations'].get(prediction.get('operation_id'))
        if not op or op['status']!='succeeded': continue
        for obs in op['result']['observations']:
            if obs['quantity']!=prediction['quantity']: continue
            key=(obs['quantity'],obs['unit'])
            groups.setdefault(key,[]).append(abs(D(obs['value'])-D(prediction['expected_value'])))
    completed=[s for s in run['submissions'].values() if s['status']=='succeeded']
    return {'run_id':run['id'],'credits':{'research':run['research_spent'],'verification':run['verification_spent'],'reserved':run['reserved'],'remaining':run['credits'],'llm':'tracked_by_harness'},'predictions':{'total':len(run['predictions']),'scored':sum(map(len,groups.values())),'by_quantity':[{'quantity':q,'unit':u,'count':len(errors),'mae':str(sum(errors,D(0))/len(errors))} for (q,u),errors in sorted(groups.items())]},'validation':{'completed':len(completed),'passed':sum(s['result']['pass'] for s in completed),'production_runs':[{'submission_id':s['id'],'repeats':s['result']['repeats']} for s in completed]}}
