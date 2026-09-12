import tempfile
import unittest
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
