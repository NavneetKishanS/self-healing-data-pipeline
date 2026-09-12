"""Dataset-backed tool adapter; each run owns its rows and expected schema."""
import csv
from copy import deepcopy
from datetime import datetime
import math
from pathlib import Path

DATASET = Path(__file__).resolve().parent.parent / 'pipeline/fixtures/orders.csv'


class OrdersDataset:
    def __init__(self, inject_failure=None, rows=None):
        with DATASET.open(newline='') as source:
            self.rows = list(csv.DictReader(source))[:30]
        for row in self.rows:
            row['amount'] = float(row['amount'])
        if rows is not None:
            self.rows = deepcopy(rows)
        self.expected_count = len(self.rows)
        self.columns = [
            {'name': 'order_id', 'type': 'string', 'nullable': False},
            {'name': 'customer_id', 'type': 'string', 'nullable': False},
            {'name': 'amount', 'type': 'float', 'nullable': False},
            {'name': 'created_at', 'type': 'timestamp', 'nullable': False},
        ]
        if inject_failure == 'schema_drift':
            for row in self.rows:
                row['amount'] = str(row['amount'])
        elif inject_failure:
            raise ValueError('Dataset supports schema_drift only')

    def errors(self):
        errors = []
        for index, row in enumerate(self.rows):
            for column in self.columns:
                value = row.get(column['name'])
                valid = isinstance(value, str) and bool(value)
                if column['type'] == 'float':
                    valid = type(value) in (int, float) and math.isfinite(value)
                elif column['type'] == 'integer':
                    valid = type(value) is int
                elif column['type'] == 'timestamp':
                    try:
                        datetime.fromisoformat(value)
                    except (TypeError, ValueError):
                        valid = False
                if not valid:
                    errors.append({'row': index + 1, 'column': column['name'],
                                   'expected_type': column['type'], 'actual_type': type(value).__name__,
                                   'value': value})
        return errors

    def get_recent_logs(self, job_id):
        if job_id != 'job_1':
            return {'job_id': job_id, 'status': 'unknown'}
        errors = self.errors()
        return {'job_id': job_id, 'status': 'failed' if errors else 'success',
                'error_type': 'schema_drift' if errors else None,
                'error_message': 'Incoming rows do not match the expected schema' if errors else '',
                'affected_table': 'orders', 'row_count': 0 if errors else len(self.rows),
                'expected_row_count': self.expected_count, 'mismatch_count': len(errors),
                'mismatches': errors[:5], 'sample_rows': deepcopy(self.rows[:5])}

    def get_schema(self, table_name):
        return {'table_name': table_name, 'columns': deepcopy(self.columns) if table_name == 'orders' else [],
                'repair_contract': 'schema_patch amount to float converts incoming values; never change the expected schema'}

    def apply_fix(self, fix):
        expected = {'fix_type': 'schema_patch', 'target': 'orders',
                    'change': {'column': 'amount', 'new_type': 'float'}}
        if fix != expected:
            return {'applied': False, 'message': 'Only amount-to-float conversion is supported'}
        candidate = deepcopy(self.rows)
        try:
            for row in candidate:
                value = row['amount']
                if type(value) not in (str, int, float):
                    raise ValueError('Not numeric')
                row['amount'] = float(value)
                if not math.isfinite(row['amount']):
                    raise ValueError('Not finite')
        except (ValueError, TypeError, KeyError, OverflowError):
            return {'applied': False, 'message': 'Unconvertible amount; no rows changed'}
        self.rows = candidate
        return {'applied': True, 'message': 'Converted amount values; expected schema unchanged'}

    def rerun_pipeline(self, job_id):
        logs = self.get_recent_logs(job_id)
        return {**logs, 'sample_rows': deepcopy(self.rows[:5]),
                'schema_valid': logs.get('status') == 'success',
                'dataset': str(DATASET)}
