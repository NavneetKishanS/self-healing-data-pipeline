"""Kaggle Iris schema, replay, and isolated repair adapter."""
import csv
import json
import math
from pathlib import Path
from copy import deepcopy
from .dataset import OrdersDataset

FIXTURE = Path(__file__).parent / 'fixtures/iris.csv'
MEASUREMENTS = ('SepalLengthCm', 'SepalWidthCm', 'PetalLengthCm', 'PetalWidthCm')
COLUMNS = [{'name': 'Id', 'type': 'integer', 'nullable': False}] + [
    {'name': name, 'type': 'float', 'nullable': False} for name in MEASUREMENTS
] + [{'name': 'Species', 'type': 'string', 'nullable': False}]


def seed_iris(db):
    db.execute('CREATE TABLE IF NOT EXISTS iris_source (id INTEGER PRIMARY KEY, row_json TEXT NOT NULL)')
    with FIXTURE.open(newline='') as source:
        for row in csv.DictReader(source):
            row['Id'] = int(row['Id'])
            for name in MEASUREMENTS:
                row[name] = float(row[name])
            db.execute('INSERT OR IGNORE INTO iris_source VALUES (?, ?)', (row['Id'], json.dumps(row)))


class IrisDataset(OrdersDataset):
    def __init__(self, rows):
        self.rows = deepcopy(rows)
        self.columns = deepcopy(COLUMNS)
        self.expected_count = len(rows)

    def get_recent_logs(self, job_id):
        result = super().get_recent_logs(job_id)
        result['affected_table'] = 'iris'
        return result

    def get_schema(self, table_name):
        return {'table_name': table_name, 'columns': deepcopy(self.columns) if table_name == 'iris' else [],
                'repair_contract': 'schema_patch with change {column: affected column, new_type: expected type} converts values; expected schema stays fixed'}

    def apply_fix(self, fix):
        change = fix.get('change', {})
        column = change.get('column')
        expected = next((c['type'] for c in COLUMNS if c['name'] == column), None)
        if (fix.get('fix_type') != 'schema_patch' or fix.get('target') != 'iris'
                or expected not in ('float', 'integer') or change != {'column': column, 'new_type': expected}):
            return {'applied': False, 'message': 'Only an exact conversion to the expected numeric type is supported'}
        candidate = deepcopy(self.rows)
        try:
            for row in candidate:
                value = row[column]
                if type(value) not in (str, float, int):
                    raise ValueError()
                number = float(value)
                if not math.isfinite(number) or (expected == 'integer' and not number.is_integer()):
                    raise ValueError()
                row[column] = int(number) if expected == 'integer' else number
        except (ValueError, TypeError, KeyError, OverflowError):
            return {'applied': False, 'message': 'Cannot convert safely; rows unchanged'}
        self.rows = candidate
        return {'applied': True, 'message': 'Converted actual values; expected schema unchanged'}

    def rerun_pipeline(self, job_id):
        result = super().rerun_pipeline(job_id)
        result['dataset'] = 'Kaggle uciml/iris'
        return result
