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
    result = {'run_id':run['id'],'credits':{'research':run['research_spent'],'verification':run['verification_spent'],'reserved':run['reserved'],'remaining':run['credits'],'llm':'tracked_by_harness'},'predictions':{'total':len(run['predictions']),'scored':sum(map(len,groups.values())),'by_quantity':[{'quantity':q,'unit':u,'count':len(errors),'mae':str(sum(errors,D(0))/len(errors))} for (q,u),errors in sorted(groups.items())]},'validation':{'completed':len(completed),'passed':sum(s['result']['pass'] for s in completed),'production_runs':[{'submission_id':s['id'],'repeats':s['result']['repeats']} for s in completed]}}

    if 'research' in run:
        phases = []
        for name, plan in run['research']['phases'].items():
            if 'started_at' not in plan:
                continue
            research = D(plan.get('end_research_spent', run['research_spent'])) - D(plan['start_research_spent'])
            verification = D(plan.get('end_verification_spent', run['verification_spent'])) - D(plan['start_verification_spent'])
            remaining = plan.get('end_material', run['batches'])
            material = sum((sum((D(batch[key])-D(remaining[bid][key]) for key in ('remaining_mass_g','verification_reserve_g')), D(0)) for bid,batch in plan['batches'].items()), D(0))
            phases.append({'phase': name, 'orders_assigned': len(plan['order_ids']),
                           'orders_passed': sum(run['orders'][oid]['status']=='completed' for oid in plan['order_ids']),
                           'lab_credits': str(research+verification), 'validation_credits': str(verification),
                           'material_g': str(material), 'remaining_credits': plan.get('unspent_credits',run['credits'])})
        result['phases'] = phases
        result['snapshot_origin'] = run['research'].get('origin')
    return result
