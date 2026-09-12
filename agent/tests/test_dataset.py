import unittest
from copy import deepcopy
from agent.dataset import OrdersDataset

FIX = {'fix_type': 'schema_patch', 'target': 'orders', 'change': {'column': 'amount', 'new_type': 'float'}}

class DatasetTests(unittest.TestCase):
    def test_real_rows_require_conversion(self):
        data = OrdersDataset('schema_drift')
        schema = deepcopy(data.columns)
        self.assertEqual(data.expected_count, 30)
        self.assertEqual(data.rerun_pipeline('job_1')['status'], 'failed')
        self.assertIsInstance(data.rows[0]['amount'], str)
        self.assertFalse(data.apply_fix({**FIX, 'target': 'other'})['applied'])
        self.assertEqual(data.rerun_pipeline('job_1')['status'], 'failed')
        self.assertTrue(data.apply_fix(FIX)['applied'])
        self.assertIsInstance(data.rows[0]['amount'], float)
        self.assertEqual(data.rerun_pipeline('job_1')['status'], 'success')
        self.assertEqual(data.columns, schema)

    def test_invalid_value_blocks_atomic_conversion(self):
        data = OrdersDataset('schema_drift')
        data.rows[-1]['amount'] = 'unknown'
        before = deepcopy(data.rows)
        self.assertFalse(data.apply_fix(FIX)['applied'])
        self.assertEqual(data.rows, before)
        self.assertEqual(data.rerun_pipeline('job_1')['status'], 'failed')
