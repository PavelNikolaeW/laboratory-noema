"""Local experimenter CLI. No admin routes or participant-accessible config."""
import argparse
import hashlib
import json
from decimal import Decimal
from pathlib import Path
from .store import Store, now, uid
from .service import event


def main():
    p=argparse.ArgumentParser(); p.add_argument('--db',default='laboratory.db')
    commands=p.add_subparsers(dest='command',required=True)
    c=commands.add_parser('create'); c.add_argument('--scenario',choices=['S1','S2','S3','S4'],default='S1'); c.add_argument('--label',default='Павел'); c.add_argument('--mode',choices=['notified','undisclosed'],default='undisclosed'); c.add_argument('--paired',action='store_true')
    c=commands.add_parser('gain'); c.add_argument('run_id'); c.add_argument('value')
    c=commands.add_parser('export'); c.add_argument('run_id'); c.add_argument('--private',action='store_true'); c.add_argument('--output',required=True)
    c=commands.add_parser('metrics'); c.add_argument('run_id')
    args=p.parse_args(); store=Store(args.db)
    if args.command=='create':
        pair=uid() if args.paired else None
        result=store.create(args.label,args.scenario,args.mode,pair)
        if args.paired: result={'pair_id':pair,'runs':[result,store.create(args.label+' — контроль',args.scenario,args.mode,pair)]}
        print(json.dumps(result,ensure_ascii=False,indent=2)); return
    with store.transaction(run_id=args.run_id) as run:
        if args.command=='metrics':
            from .metrics import summarize
            print(json.dumps(summarize(run),ensure_ascii=False,indent=2)); return
        if args.command=='gain':
            gain=Decimal(args.value)
            if not gain.is_finite() or not Decimal('0.01')<=gain<=Decimal(10): p.error('gain must be finite and between .01 and 10')
            if any(o['status'] in ('accepted','running') for o in run['operations'].values()): p.error('Wait for a safe operation boundary')
            run['private_events'].append({'kind':'heater_gain_changed','before':run['config']['heater_gain'],'after':str(gain),'recorded_at':now()})
            run['config']['heater_gain']=str(gain)
            if run['mode']=='notified': event(run,'maintenance',payload={'message':'Оборудование прошло обслуживание; проверьте применимость процедур.'})
            print('Intervention saved'); return
        if args.private:
            result=run
        else:
            from .service import public_sample, public_operation
            result={k:run[k] for k in ('id','label','created_at','credits','reserved','research_spent','verification_spent','orders','notes','predictions','recipes','submissions','observations','events')}
            result['samples']={k:public_sample(v) for k,v in run['samples'].items()}
            result['operations']={k:public_operation(v) for k,v in run['operations'].items()}
        result=dict(result,provenance_hash=hashlib.sha256(json.dumps({'config':run['config'],'scenario':run['scenario']},sort_keys=True).encode()).hexdigest()) if args.private else result
        Path(args.output).write_text(json.dumps(result,ensure_ascii=False,indent=2))
        print('Export saved')

if __name__=='__main__': main()
