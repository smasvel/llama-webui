"""Disabled model-test registry kept as a compatibility stub."""

ACTIVE = {'loading', 'running', 'repairing', 'cancelling'}
SUITES = ()

class Cancelled(Exception):
    pass

class Registry:
    def list(self):
        return []

def performance_report(jobs):
    return []
