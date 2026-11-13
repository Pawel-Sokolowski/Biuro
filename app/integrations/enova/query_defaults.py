"""Resolve shipped adapters separately from user-owned SQL overrides."""
from pathlib import Path


def with_query_defaults(config):
    bundled = Path(__file__).resolve().parents[3] / 'docs' / 'enova' / 'queries'
    sources = []
    for source in config.get('sources') or []:
        source = dict(source)
        for name in ('payroll_query', 'declarations_query', 'hr_events_query', 'jpk_query'):
            # A company-specific override wins over the shared override.
            override = str(source.get(name) or '').strip() or str(config.get(name) or '').strip()
            if override:
                source[name] = override
            elif name == 'payroll_query':
                source[name] = str(bundled / 'payroll.sql')
        sources.append(source)
    return {**config, 'sources': sources}
