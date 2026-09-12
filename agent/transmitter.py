"""One bounded, stoppable background producer per authenticated owner."""
from threading import Event, Lock, Thread


class Transmitter:
    def __init__(self, send, changed=lambda owner: None):
        self.send = send
        self.changed = changed
        self.lock = Lock()
        self.jobs = {}

    def status(self, owner):
        with self.lock:
            job = self.jobs.get(owner)
            return {k: v for k, v in job.items() if k != 'stop'} if job else {'running': False, 'sent': 0, 'error': None}

    def start(self, owner, interval=2, probability=0.3):
        with self.lock:
            if self.jobs.get(owner, {}).get('running'):
                return
            job = {'running': True, 'sent': 0, 'error': None, 'interval_seconds': interval,
                   'corruption_probability': probability, 'stop': Event()}
            self.jobs[owner] = job
        def work():
            try:
                while not job['stop'].is_set():
                    self.send(owner, probability)
                    with self.lock:
                        job['sent'] += 1
                    self.changed(owner)
                    if job['stop'].wait(interval):
                        break
            except Exception as exc:
                with self.lock:
                    job['error'] = str(exc) if isinstance(exc, ValueError) else type(exc).__name__
            finally:
                with self.lock:
                    job['running'] = False
                self.changed(owner)
        Thread(target=work, daemon=True).start()

    def stop(self, owner):
        with self.lock:
            if owner in self.jobs:
                self.jobs[owner]['stop'].set()
