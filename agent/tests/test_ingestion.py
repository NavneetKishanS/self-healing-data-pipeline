import os
import tempfile
import time
import unittest
from unittest.mock import patch

from agent.server import create_app

ROW = {'order_id': 'o1', 'customer_id': 'c1', 'amount': 25.5, 'created_at': '2026-09-12T00:00:00+00:00'}

class IngestionTests(unittest.TestCase):
    def test_detects_incoming_types_and_persists_exact_samples(self):
        with tempfile.TemporaryDirectory() as directory:
            client = create_app(local_no_auth=True, state_dir=directory).test_client()
            good = client.post('/api/ingestion', json={'batchId': 'good', 'rows': [ROW]})
            self.assertEqual(good.json['flagged_count'], 0)
            bad_row = {**ROW, 'amount': '25.5'}
            bad = client.post('/api/ingestion', json={'batchId': 'bad', 'rows': [ROW, bad_row]})
            self.assertEqual(bad.json['flagged_count'], 1)
            self.assertEqual(bad.json['mismatches'][0]['row'], 2)
            self.assertEqual(bad.json['flagged_samples'][0]['data'], bad_row)
            restarted = create_app(local_no_auth=True, state_dir=directory).test_client()
            self.assertEqual(restarted.get('/api/ingestion').json['total_batches'], 2)

    def test_duplicate_batch_is_not_ingested_twice(self):
        with tempfile.TemporaryDirectory() as directory:
            client = create_app(local_no_auth=True, state_dir=directory).test_client()
            payload = {'batchId': 'same', 'rows': [ROW]}
            client.post('/api/ingestion', json=payload)
            self.assertEqual(client.post('/api/ingestion', json=payload).status_code, 200)
            payload['rows'] = [{**ROW, 'amount': 'bad'}]
            self.assertEqual(client.post('/api/ingestion', json=payload).status_code, 409)
            self.assertEqual(client.get('/api/ingestion').json['total_batches'], 1)
            self.assertEqual(client.post('/api/ingestion/same/investigate').status_code, 400)

    def test_owner_isolation_and_input_bounds(self):
        class Verifier:
            def verify(self, token, permission):
                return token
        with tempfile.TemporaryDirectory() as directory:
            client = create_app(verifier=Verifier(), state_dir=directory).test_client()
            client.post('/api/ingestion', headers={'Authorization': 'one'}, json={'batchId': 'one', 'rows': [ROW]})
            self.assertEqual(client.get('/api/ingestion', headers={'Authorization': 'two'}).json['total_batches'], 0)
            self.assertEqual(client.post('/api/ingestion/one/investigate', headers={'Authorization': 'two'}).status_code, 403)
            self.assertEqual(client.post('/api/ingestion', json={'batchId': 'empty', 'rows': []}).status_code, 400)


BAD_ROW = {**ROW, 'amount': '25.5'}
FIXED = {'outcome': 'fixed', 'terminal_event': 'verified', 'mutation_state': 'applied',
         'application': {'applied': True, 'message': 'patched'}, 'model_requests': [{'status': 'returned'}] * 2}
NO_ANSWER = {'outcome': 'gave_up', 'terminal_event': 'model_error', 'mutation_state': 'not_attempted',
             'application': None, 'model_requests': [{'status': 'error', 'error_type': 'RateLimitError'}]}
REJECTED = {'outcome': 'needs_human', 'terminal_event': 'rejected', 'mutation_state': 'not_attempted',
            'application': None, 'model_requests': [{}] * 2}
MISCONFIGURED = {**NO_ANSWER, 'model_requests': [{'status': 'error', 'error_type': 'AuthenticationError'}]}


class WatcherTests(unittest.TestCase):
    """The watcher is the Investigate button on a timer: same claim, same run, same approval path."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.results, self.runs = [], []

        def runner(job_id, *, approval, incident_id, inject_failure, rows=None, dataset_name='orders'):
            self.runs.append({'incident_id': incident_id, 'rows': rows, 'dataset_name': dataset_name})
            result = self.results.pop(0)
            if result == 'ask':
                decision = approval(diagnosis='d', proposed_fix={'fix_type': 'schema_patch', 'target': 'orders', 'change': {}}, confidence=0.5)
                result = FIXED if decision['approved'] else REJECTED
            return {'incident_id': incident_id, 'job_id': job_id, **result}
        self.app = create_app(local_no_auth=True, runner=runner, state_dir=self.temp.name)
        self.client = self.app.test_client()
        self.watcher = self.app.extensions['watcher']

    def finished(self, run_id):
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            state = self.client.get('/api/runs/' + run_id).get_json()
            if state.get('status') == 'finished':
                return state['result']
            time.sleep(0.005)
        self.fail('Run did not finish')

    def flag(self, batch_id):
        response = self.client.post('/api/ingestion', json={'batchId': batch_id, 'rows': [ROW, BAD_ROW]})
        self.assertEqual(response.json['status'], 'schema_drift')

    def test_flagged_batches_are_investigated_without_a_click(self):
        self.assertEqual(self.watcher.tick(), {'state': 'idle'})
        self.client.post('/api/ingestion', json={'batchId': 'good', 'rows': [ROW]})
        self.assertEqual(self.watcher.tick(), {'state': 'idle'})
        self.flag('bad')
        self.results.append(FIXED)
        started = self.watcher.tick()
        self.assertEqual((started['state'], started['batch_id'], started['attempt']), ('started', 'bad', 1))
        self.assertEqual(self.finished(started['run_id'])['outcome'], 'fixed')
        self.assertEqual(self.runs, [{'incident_id': started['run_id'], 'rows': [ROW, BAD_ROW], 'dataset_name': 'orders'}])
        batches = {b['batch_id']: b['run_id'] for b in self.client.get('/api/ingestion').json['batches']}
        self.assertEqual(batches, {'good': None, 'bad': started['run_id']})
        self.assertEqual(self.watcher.tick(), {'state': 'idle'})
        status = self.client.get('/api/watcher').json
        self.assertEqual((status['running'], status['investigations'], status['budget']['used_today'],
                          status['budget']['daily_limit']), (False, 1, 2, 40))
        # The click path still works and returns the same investigation.
        self.assertEqual(self.client.post('/api/ingestion/bad/investigate').json, {'incident_id': started['run_id']})

    def test_only_runs_without_a_model_answer_are_retried_with_backoff(self):
        self.watcher.backoff = (10, 20)
        self.flag('bad')
        self.results.extend([NO_ANSWER, NO_ANSWER, FIXED])
        first = self.watcher.tick(now=1000)
        self.assertEqual(first['attempt'], 1)
        self.finished(first['run_id'])
        self.assertEqual(self.watcher.tick(now=1005), {'state': 'idle'})
        second = self.watcher.tick(now=1010)
        self.assertEqual((second['state'], second['attempt']), ('started', 2))
        self.assertNotEqual(second['run_id'], first['run_id'])
        self.finished(second['run_id'])
        self.assertEqual(self.watcher.tick(now=1015), {'state': 'idle'})
        third = self.watcher.tick(now=1030)
        self.assertEqual((third['state'], third['attempt']), ('started', 3))
        self.assertEqual(self.finished(third['run_id'])['outcome'], 'fixed')
        self.assertEqual(self.client.get('/api/ingestion').json['batches'][0]['run_id'], third['run_id'])
        self.assertEqual(self.watcher.tick(now=10 ** 6), {'state': 'idle'})
        self.assertEqual(self.client.get('/api/watcher').json['budget']['used_today'], 4)

        # A human decision is final, so is a configuration error, and so is the attempt limit.
        for batch_id, result in (('rejected', REJECTED), ('misconfigured', MISCONFIGURED)):
            self.flag(batch_id)
            self.results.append(result)
            self.finished(self.watcher.tick(now=2000)['run_id'])
            self.assertEqual(self.watcher.tick(now=10 ** 6), {'state': 'idle'})
        self.flag('hopeless')
        self.results.extend([NO_ANSWER] * 3)
        for now in (3000, 3010, 3030):
            self.finished(self.watcher.tick(now=now)['run_id'])
        self.assertEqual(self.watcher.tick(now=10 ** 6), {'state': 'idle'})
        self.assertEqual(len(self.runs), 8)

    def test_daily_budget_counts_every_run_and_stops_new_investigations(self):
        self.watcher.budget = 4
        self.results.append(FIXED)
        self.client.post('/api/runs', json={'runId': 'manual'})
        self.finished('manual')
        self.assertEqual(self.watcher.used_today(), 2)
        self.flag('bad')
        self.assertEqual(self.watcher.tick(), {'state': 'budget_exhausted', 'used_today': 2, 'daily_limit': 4})
        self.assertIsNone(self.client.get('/api/ingestion').json['batches'][0]['run_id'])
        self.watcher.budget = 5
        self.results.append(FIXED)
        started = self.watcher.tick()
        self.assertEqual(started['state'], 'started')
        self.finished(started['run_id'])
        self.assertEqual(self.watcher.used_today(), 4)

    def test_watcher_waits_while_an_incident_is_active(self):
        self.results.append('ask')
        self.client.post('/api/runs', json={'runId': 'manual'})
        deadline = time.monotonic() + 3
        while self.client.get('/api/runs/manual').get_json()['status'] != 'awaiting_approval':
            self.assertLess(time.monotonic(), deadline)
            time.sleep(0.005)
        self.flag('bad')
        self.assertEqual(self.watcher.tick(), {'state': 'busy'})
        pending = self.client.get('/api/runs/manual').get_json()['approval']
        self.client.post('/api/runs/manual/decision', json={'approved': False, 'fix_hash': pending['fix_hash']})
        self.assertEqual(self.finished('manual')['outcome'], 'needs_human')
        self.results.append(FIXED)
        started = self.watcher.tick()
        self.assertEqual(started['state'], 'started')
        self.finished(started['run_id'])

    def test_watch_mode_runs_in_the_background(self):
        def runner(job_id, **kwargs):
            return {'incident_id': kwargs['incident_id'], 'job_id': job_id, **FIXED}
        with patch.dict(os.environ, {'AGENT_WATCH_INTERVAL_SECONDS': '0.05'}):
            app = create_app(local_no_auth=True, runner=runner, state_dir=self.temp.name, watch=True)
        watcher = app.extensions['watcher']
        self.addCleanup(watcher.stop)
        client = app.test_client()
        self.assertTrue(client.get('/api/watcher').json['running'])
        client.post('/api/ingestion', json={'batchId': 'bad', 'rows': [ROW, BAD_ROW]})
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            batch = client.get('/api/ingestion').json['batches'][0]
            if batch['run_id'] and client.get('/api/runs/' + batch['run_id']).get_json()['status'] == 'finished':
                break
            time.sleep(0.01)
        else:
            self.fail('Watcher did not investigate the flagged batch')
        status = client.get('/api/watcher').json
        self.assertEqual((status['investigations'], status['budget']['used_today']), (1, 2))
        watcher.stop()
        self.assertFalse(client.get('/api/watcher').json['running'])
