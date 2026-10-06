"""Experimenter-only pilot provisioning and exact quiescent world branches."""
import copy
import hashlib
import json
import os
from decimal import Decimal as D, InvalidOperation
from pathlib import Path

from . import engine
from .store import encode, uid, now, token_hash
from .service import event


def safe_boundary(run):
    if (D(run['reserved']) != 0 or any(o['status'] in ('accepted', 'running') for o in run['operations'].values())
            or any(s['status'] == 'busy' for s in run['samples'].values())):
        raise ValueError('Wait for a safe operation boundary')


def active_orders(run):
    pilot = run.get('research')
    if not pilot:
        return list(run['orders'].values())
    ids = pilot['phases'][pilot['active_phase']]['order_ids']
    return [run['orders'][oid] for oid in ids]


def witness(batch, order, config):
    # Closed feasibility check only; never publish this recipe to participants.
    for power in range(1, 11):
        for duration in range(1, 121):
            heat = {'kind': 'heat', 'parameters': {'power': power, 'duration': duration}}
            for clean in (False, True):
                steps = [heat]
                if clean:
                    steps.append({'kind': 'separate', 'parameters': {'mode': 'selective'}, 'keep_output': 'selected'})
                steps.append({'kind': 'cool', 'parameters': {'target_temperature_c': '20'}})
                state = engine.fresh(batch['fractions'], order['input_mass_g'])
                price = D(0)
                for step in steps:
                    state = engine.transition(step['kind'], [state], step['parameters'], config)[0]
                    price += engine.cost(step['kind'], step['parameters'])
                if (engine.measure(state, 'response', config) >= D(order['response_min'])
                        and engine.mass(state) / D(order['input_mass_g']) >= D(order['yield_min'])
                        and price <= D(order['max_recipe_credits'])):
                    return steps
    raise ValueError('Order is unreachable in the configured recipe budget')


def install_pilot(run):
    safe_boundary(run)
    if 'research' in run:
        return run['research']
    if any(run[name] for name in ('operations', 'samples', 'observations', 'notes', 'predictions', 'recipes', 'submissions')):
        raise ValueError('Pilot provisioning requires a clean run')
    mass = D(next(iter(run['orders'].values()))['input_mass_g'])
    phases = {}
    for phase, multiplier, profiles in (
        ('P1', D(1), [(['.6', '0', '.35', '.05'], False), (['.6', '0', '.2', '.2'], True)]),
        ('P2', D(2), [(['.65', '0', '.31', '.04'], False), (['.58', '0', '.22', '.2'], True)]),
        ('P3', D('1.5'), [(['.65', '0', '.31', '.04'], False)]),
    ):
        batches, orders = {}, {}
        for index, (fractions, clean) in enumerate(profiles, 1):
            bid, oid = uid(), uid()
            batch = {'id': bid, 'label': f'Партия {len(phases)*2+index}', 'remaining_mass_g': '200',
                     'verification_reserve_g': '200', 'min_mass_g': '0.1', 'max_mass_g': '100', 'fractions': fractions}
            order = {'id': oid, 'brief': 'Подберите очистку и подтвердите рецепт на свежих порциях.' if clean else 'Подберите обработку покрытия и подтвердите рецепт на свежих порциях.',
                     'batch_id': bid, 'input_mass_g': str(mass*multiplier), 'response_min': '90' if clean else '70',
                     'yield_min': '0.60' if clean else '0.80', 'max_recipe_credits': '15',
                     'validation_repeats': 2, 'validation_cost': '10', 'status': 'open'}
            batches[bid], orders[oid] = batch, order
        witnesses = {}
        for oid, order in orders.items():
            baseline = witness(batches[order['batch_id']], order, run['config'])
            changed = dict(run['config'], heater_gain=str(D(run['config']['heater_gain'])*D('.5')))
            impaired = witness(batches[order['batch_id']], order, changed)
            witnesses[oid] = {'baseline': baseline, 'half_gain': impaired}
        phases[phase] = {'credits': '100', 'batches': batches, 'orders': orders,
                         'order_ids': list(orders), 'private_feasibility_witnesses': witnesses}
    # Replace only the unused development assignment. All future tasks remain private.
    run['orders'], run['batches'] = {}, {}
    run['research'] = {'version': 'pilot-0.1', 'active_phase': None, 'phases': phases, 'history': []}
    advance_phase(run, 'P1')
    return run['research']


def advance_phase(run, phase):
    safe_boundary(run)
    pilot = run.get('research')
    if not pilot or phase not in pilot['phases']:
        raise ValueError('Install the pilot plan before advancing phases')
    current = pilot['active_phase']
    if current == phase:
        return pilot['phases'][phase]
    order = [None, 'P1', 'P2', 'P3']
    if order.index(phase) != order.index(current) + 1:
        raise ValueError('Phases must advance once, in order')
    if current:
        old = pilot['phases'][current]
        old['closed_at'] = now()
        old['end_research_spent'], old['end_verification_spent'] = run['research_spent'], run['verification_spent']
        old['unspent_credits'] = run['credits']
        old['end_material'] = {bid: {key: run['batches'][bid][key] for key in ('remaining_mass_g','verification_reserve_g')} for bid in old['batches']}
        for batch in run['batches'].values():
            batch['remaining_mass_g'], batch['verification_reserve_g'] = '0', '0'
    plan = pilot['phases'][phase]
    run['batches'].update(copy.deepcopy(plan['batches']))
    run['orders'].update(copy.deepcopy(plan['orders']))
    run['credits'] = plan['credits']
    plan['started_at'] = now()
    plan['start_research_spent'], plan['start_verification_spent'] = run['research_spent'], run['verification_spent']
    pilot['active_phase'] = phase
    pilot['history'].append({'phase': phase, 'recorded_at': now()})
    run['private_events'].append({'kind': 'phase_started', 'phase': phase, 'recorded_at': now()})
    event(run, 'orders.updated', payload={'order_ids': plan['order_ids']})
    return plan


def snapshot(store, run_id, output):
    with store.transaction(run_id=run_id) as run:
        safe_boundary(run)
        # Local human-control retry credentials are administration, not world state.
        if run.get('local_attempts'):
            raise ValueError('Snapshot a research run without local human retry credentials')
        frozen = copy.deepcopy(run)
    digest = hashlib.sha256(encode(frozen).encode()).hexdigest()
    envelope = {'snapshot_version': 1, 'state_sha256': digest, 'state': frozen}
    descriptor = os.open(output, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    with os.fdopen(descriptor, 'w') as file:
        file.write(encode(envelope)+'\n')
    return digest


def fork_snapshot(store, path, multiplier='.5'):
    if Path(path).stat().st_size > 16*1024*1024:
        raise ValueError('Snapshot exceeds 16 MiB')
    envelope = json.loads(Path(path).read_text())
    state = envelope['state']
    digest = hashlib.sha256(encode(state).encode()).hexdigest()
    if envelope['snapshot_version'] != 1 or digest != envelope['state_sha256']:
        raise ValueError('Snapshot checksum/version mismatch')
    safe_boundary(state)
    current_engine = hashlib.sha256(Path(engine.__file__).read_bytes()).hexdigest()
    if state['protocol']['engine_sha256'] != current_engine:
        raise ValueError('Engine differs from the frozen snapshot')
    try:
        factor = D(multiplier)
    except InvalidOperation:
        raise ValueError('Invalid gain multiplier') from None
    if not factor.is_finite() or not D('.01') <= factor <= D(1):
        raise ValueError('Invalid gain multiplier')
    gain = D(state['config']['heater_gain'])*factor
    if not D('.01') <= gain <= D(10):
        raise ValueError('Invalid gain multiplier')
    if state['mode'] != 'undisclosed' or state.get('local_attempts'):
        raise ValueError('Forks require an undisclosed research run')
    if state.get('research', {}).get('active_phase') != 'P2':
        raise ValueError('Fork at the post-P2 boundary before provisioning P3')
    signature = hashlib.sha256((digest+':'+str(factor.normalize())).encode()).hexdigest()
    with store.engine.connect() as connection:
        connection.exec_driver_sql('BEGIN IMMEDIATE')
        connection.exec_driver_sql('CREATE TABLE IF NOT EXISTS research_forks (signature TEXT PRIMARY KEY, credentials TEXT NOT NULL)')
        existing = connection.exec_driver_sql('SELECT credentials FROM research_forks WHERE signature=?', (signature,)).first()
        if existing:
            connection.rollback()
            return json.loads(existing[0])
        children = {}
        for branch in ('U', 'D'):
            child = copy.deepcopy(state)
            rid, token = uid(), uid()+uid()
            # Labels are equal and neutral; branch assignments stay private.
            child.update(id=rid, pair_id=digest)
            child['research']['origin'] = {'snapshot_sha256': digest, 'parent_run_id': state['id'], 'branch': branch}
            if branch == 'D':
                child['config']['heater_gain'] = str(gain)
            child['private_events'].append({'kind': 'snapshot_fork', 'branch': branch, 'snapshot_sha256': digest, 'recorded_at': now()})
            connection.exec_driver_sql('INSERT INTO runs VALUES (?,?,?)', (rid, token_hash(token), encode(child)))
            children[branch] = {'run_id': rid, 'token': token}
        result = {'snapshot_sha256': digest, 'runs': children}
        connection.exec_driver_sql('INSERT INTO research_forks VALUES (?,?)', (signature, encode(result)))
        connection.commit()
        return result
