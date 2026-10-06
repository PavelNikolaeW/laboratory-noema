import copy
import json
from decimal import Decimal as D

import pytest
from fastapi.testclient import TestClient

from lab.app import create_app
from lab.store import Store, uid
from lab.service import accept, finish
from lab.research import install_pilot, advance_phase, snapshot, fork_snapshot, active_orders
from lab.metrics import summarize
from test_lab import body, post, wait


@pytest.mark.parametrize('scenario', ['S1', 'S2', 'S3'])
def test_pilot_phases_are_private_reachable_and_budgeted(tmp_path, scenario):
    app = create_app(str(tmp_path/'lab.db'), delay=0)
    store = app.state.store
    auth = store.create(scenario=scenario)
    with store.transaction(run_id=auth['run_id']) as run:
        plan = install_pilot(run)
        initial = copy.deepcopy(plan)
        assert install_pilot(run) == initial
        with pytest.raises(ValueError): advance_phase(run, 'P3')
    with TestClient(app) as client:
        client.headers['Authorization'] = 'Bearer '+auth['token']
        orders = client.get('/api/v1/orders').json()['items']
        assert len(orders) == 2
        visible = json.dumps(client.get('/api/v1/session').json())+json.dumps(client.get('/api/v1/batches').json())
        assert 'fractions' not in visible and 'witness' not in visible and 'heater_gain' not in visible
        assert not any(oid in visible for oid in initial['phases']['P2']['order_ids'])
        for order in orders:
            witness = initial['phases']['P1']['private_feasibility_witnesses'][order['id']]['baseline']
            recipe = post(client, 'recipes', {'name': 'closed validator fixture', 'steps': witness}).json()
            key = uid()
            request = {'order_id': order['id'], 'recipe_id': recipe['id']}
            submission = post(client, 'submissions', request, key).json()
            assert wait(client, submission['operation_id'])['result']['pass']
            assert post(client, 'submissions', request, key).json()['id'] == submission['id']
        with store.transaction(run_id=auth['run_id']) as run:
            assert D(run['verification_spent']) == 20
            old_phase = summarize(run)['phases'][0]
            assert old_phase['orders_passed'] == 2 and old_phase['lab_credits'] == '20' and old_phase['material_g'] == str(4*D(orders[0]['input_mass_g']))
            advance_phase(run, 'P2')
            frozen = copy.deepcopy(run)
            advance_phase(run, 'P2')
            assert run == frozen  # resume cannot refill credits or stock
            assert D(run['verification_spent']) == 20 and run['credits'] == '100'
        transferred = client.get('/api/v1/orders').json()['items']
        assert len(transferred) == 2
        assert not {o['batch_id'] for o in orders} & {o['batch_id'] for o in transferred}
        assert {D(o['input_mass_g']) for o in transferred} == {2*D(orders[0]['input_mass_g'])}
        denied = post(client, 'submissions', request)
        assert denied.status_code == 409 and denied.json()['error']['code'] == 'order_inactive'
        for endpoint in ('pilot', 'phase', 'snapshot', 'fork'):
            assert client.post('/api/v1/'+endpoint).status_code == 404
        with store.transaction(run_id=auth['run_id']) as run:
            run['credits'] = '0'
        blocked = post(client, 'operations', body('obtain', parameters={'batch_id': transferred[0]['batch_id'], 'mass_g': '1'}))
        assert blocked.status_code == 429


def test_snapshot_exact_independent_branches_and_restart(tmp_path):
    database = str(tmp_path/'lab.db')
    store = Store(database)
    auth = store.create()
    with store.transaction(run_id=auth['run_id']) as run:
        install_pilot(run)
        bid = active_orders(run)[0]['batch_id']
        op = accept(run, body('obtain', parameters={'batch_id': bid, 'mass_g': '10'}))
    path = tmp_path/'snapshot.json'
    with pytest.raises(ValueError): snapshot(store, auth['run_id'], str(path))
    assert not path.exists()
    with store.transaction(run_id=auth['run_id']) as run:
        finish(run, run['operations'][op['id']])
        advance_phase(run, 'P2')
        run['notes']['note-fixture'] = {'id': 'note-fixture', 'text': 'knowledge', 'created_at': 'fixture'}
        parent = copy.deepcopy(run)
    digest = snapshot(store, auth['run_id'], str(path))
    assert path.stat().st_mode & 0o777 == 0o600
    with pytest.raises(FileExistsError): snapshot(store, auth['run_id'], str(path))
    for invalid in ('NaN', 'Infinity', 'abc', '0', '2'):
        with pytest.raises(ValueError): fork_snapshot(store, path, invalid)
    forked = fork_snapshot(store, path)
    assert fork_snapshot(Store(database), path, '0.50') == forked
    assert len(store.ids()) == 3
    assert (tmp_path/'lab.db').stat().st_mode & 0o777 == 0o600
    assert len({c['token'] for c in forked['runs'].values()}) == 2
    children = {}
    for branch, credentials in forked['runs'].items():
        with store.transaction(token=credentials['token']) as run:
            children[branch] = copy.deepcopy(run)
            assert run['research']['origin']['snapshot_sha256'] == digest
            for field in ('samples', 'operations', 'events', 'notes', 'observations', 'recipes', 'predictions', 'submissions', 'idempotency', 'sequence', 'credits', 'batches', 'orders'):
                assert run[field] == parent[field]
            advance_phase(run, 'P3')
            plan = run['research']['phases']['P3']
            for order in active_orders(run):
                steps = plan['private_feasibility_witnesses'][order['id']]['baseline' if branch == 'U' else 'half_gain']
                rid = uid()
                run['recipes'][rid] = {'id': rid, 'name': 'private validator', 'steps': steps}
                from lab.service import accept_submission
                submission = accept_submission(run, {'recipe_id': rid, 'order_id': order['id']})
                finish(run, run['operations'][submission['operation_id']])
                assert run['submissions'][submission['id']]['result']['pass']
    assert children['U']['config'] == parent['config']
    assert D(children['D']['config']['heater_gain']) == D(parent['config']['heater_gain'])*D('.5')
    assert children['U']['label'] == children['D']['label']
    with store.transaction(run_id=auth['run_id']) as run: assert run == parent
    with store.transaction(token=forked['runs']['U']['token']) as run:
        run['notes']['only-U'] = {'text': 'no sharing'}
    with store.transaction(token=forked['runs']['D']['token']) as run: assert 'only-U' not in run['notes']
    corrupted = json.loads(path.read_text()); corrupted['state']['credits'] = '999'
    bad = tmp_path/'bad.json'; bad.write_text(json.dumps(corrupted))
    with pytest.raises(ValueError): fork_snapshot(store, bad)
    assert len(store.ids()) == 3

    # Identical future request keys are independent in the two participant runs.
    operation_ids = []
    app = create_app(database, delay=0)
    with TestClient(app) as client:
        for branch, credentials in forked['runs'].items():
            client.headers['Authorization'] = 'Bearer '+credentials['token']
            visible = client.get('/api/v1/session').json()
            assert 'origin' not in json.dumps(visible) and 'heater_gain' not in json.dumps(visible)
            bid = visible['orders'][0]['batch_id']
            request = body('obtain', parameters={'batch_id': bid, 'mass_g': '1'})
            accepted = post(client, 'operations', request, 'same-future-key')
            assert accepted.status_code == 202
            oid = accepted.json()['id']; operation_ids.append(oid)
            assert wait(client, oid)['status'] == 'succeeded'
            assert post(client, 'operations', request, 'same-future-key').json()['id'] == oid
        assert len(set(operation_ids)) == 2
