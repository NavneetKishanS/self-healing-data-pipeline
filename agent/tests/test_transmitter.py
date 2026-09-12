import tempfile
import time
import unittest
from agent.server import create_app

class TransmitterTests(unittest.TestCase):
    def test_background_sender_flags_and_stops(self):
        with tempfile.TemporaryDirectory() as directory:
            app = create_app(local_no_auth=True, state_dir=directory)
            client = app.test_client()
            response = client.post('/api/transmitter/start', json={'interval_seconds': 0.2, 'corruption_probability': 1})
            self.assertEqual(response.status_code, 200)
            try:
                deadline = time.monotonic() + 3
                while app.extensions['transmitter'].status('local-demo')['sent'] < 2 and time.monotonic() < deadline:
                    time.sleep(0.02)
                self.assertGreaterEqual(app.extensions['transmitter'].status('local-demo')['sent'], 2)
                batches = client.get('/api/ingestion').json['batches']
                self.assertTrue(all(b['row_count'] == 1 and b['flagged_count'] == 1 for b in batches))
                client.post('/api/transmitter/stop')
                deadline = time.monotonic() + 2
                while app.extensions['transmitter'].status('local-demo')['running'] and time.monotonic() < deadline:
                    time.sleep(0.02)
                self.assertFalse(client.get('/api/transmitter').json['running'])
                sent = client.get('/api/transmitter').json['sent']
                time.sleep(0.25)
                self.assertEqual(client.get('/api/transmitter').json['sent'], sent)
            finally:
                app.extensions['transmitter'].stop('local-demo')

    def test_invalid_configuration_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            client = create_app(local_no_auth=True, state_dir=directory).test_client()
            for config in ({'interval_seconds': 0}, {'corruption_probability': 2}, {'corruption_probability': True}):
                self.assertEqual(client.post('/api/transmitter/start', json=config).status_code, 400)
