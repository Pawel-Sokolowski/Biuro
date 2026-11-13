"""Resolve shipped adapters separately from user-owned SQL overrides."""
from pathlib import Path

ADAPTER_REVISION = 2


def bundled_directory():
    return Path(__file__).resolve().parents[3] / 'docs' / 'enova' / 'queries'


def saved_source(source):
    defaults = source.get('_query_defaults') or {}
    return {key: value for key, value in source.items()
            if key != '_query_defaults' and not (key in defaults and value == defaults[key])}


def with_query_defaults(config):
    bundled = bundled_directory()
    sources = []
    for source in config.get('sources') or []:
        source = saved_source(source)
        defaults = {}
        for name in ('payroll_query', 'declarations_query', 'hr_events_query', 'jpk_query'):
            # A company-specific override wins over the shared override.
            override = str(source.get(name) or '').strip()
            shared = str(config.get(name) or '').strip()
            if override:
                source[name] = override
            elif shared:
                source[name] = shared
                defaults[name] = shared
            elif name in ('payroll_query', 'declarations_query', 'jpk_query'):
                source[name] = str(bundled / (name.removesuffix('_query') + '.sql'))
                defaults[name] = source[name]
        if defaults:
            source['_query_defaults'] = defaults
        sources.append(source)
    return {**config, 'sources': sources}
