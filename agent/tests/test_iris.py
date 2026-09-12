import tempfile
import unittest
from copy import deepcopy
from agent.server import create_app
from agent.iris import IrisDataset

class IrisTests(unittest.TestCase):
    def test_replay_stores_original_and_detects_exact_mutation(self):
        with tempfile.TemporaryDirectory() as directory:
            client = create_app(local_no_auth=True, state_dir=directory).test_client()
            for _ in range(10):
                response = client.post('/api/ingestion/replay', json={'corrupt': True})
                self.assertEqual(response.status_code, 201)
                data = response.json
                self.assertEqual(data['flagged_count'], 1)
                original = data['replay']['original']
                incoming = data['flagged_samples'][0]['data']
                changed = [k for k in original if original[k] != incoming[k]]
                self.assertEqual(changed, [data['replay']['changed_column']])
                self.assertIsInstance(incoming[changed[0]], str)
                model = IrisDataset([incoming])
                schema = deepcopy(model.columns)
                fix = {'fix_type': 'schema_patch', 'target': 'iris', 'change': {'column': changed[0], 'new_type': 'float'}}
                self.assertTrue(model.apply_fix(fix)['applied'])
                self.assertEqual(model.rows, [original])
                self.assertEqual(model.columns, schema)
                self.assertTrue(model.rerun_pipeline('job_1')['schema_valid'])
            self.assertEqual(client.post('/api/ingestion/replay', json={'corrupt': False}).json['status'], 'valid')
            restarted = create_app(local_no_auth=True, state_dir=directory).test_client()
            self.assertEqual(restarted.get('/api/ingestion').json['total_batches'], 11)
