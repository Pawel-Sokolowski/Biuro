"""Check the explicit API catalogue against the desktop and server code.

The catalogue in app/remote/operations.py is maintained in review. Functions and
classes in DAO modules are private to the server unless the catalogue lists them.
This command validates the contract and never writes source files.
"""
import argparse
import ast
from pathlib import Path
import runpy

ROOT = Path(__file__).resolve().parents[1]
LEGACY_READS = {
    'app.companies.checking_obligations.profile',
    'app.companies.checking_obligations.report_applies',
    'app.db.dao_dashboard.format_duration',
    'app.db.dao_dashboard.format_event_time',
    'app.integrations.enova.accounting.active_types',
    'app.integrations.enova.accounting.declaration_entries',
    'app.integrations.enova.accounting.declarations',
    'app.integrations.enova.accounting.documents',
    'app.services.checking_refresh.retry_request',
}


def named_decorators(node, name):
    return [
        ast.literal_eval(decorator.args[0])
        for decorator in node.decorator_list
        if isinstance(decorator, ast.Call)
        and isinstance(decorator.func, ast.Name)
        and decorator.func.id == name
    ]


def parameters(node):
    if node is None:
        return []
    args = node.args
    return [arg.arg for arg in (*args.posonlyargs, *args.args, *args.kwonlyargs) if arg.arg != 'self']


def catalogue_errors(operations, source):
    declared = set()
    callables = {}
    for path in source.rglob('*.py'):
        module = '.'.join(path.relative_to(source.parent).with_suffix('').parts)
        tree = ast.parse(path.read_text(encoding='utf-8-sig'), filename=str(path))
        for node in tree.body:
            if isinstance(node, ast.FunctionDef):
                callables[(module, None, node.name)] = node
                declared.update(named_decorators(node, 'office_endpoint'))
            elif isinstance(node, ast.ClassDef):
                for qualified in named_decorators(node, 'office_remote_class'):
                    declared.add(qualified + '.__new__')
                methods = {m.name: m for m in node.body if isinstance(m, ast.FunctionDef)}
                callables[(module, node.name, '__new__')] = methods.get('__init__')
                callables.update({(module, node.name, name): method for name, method in methods.items()})
    for name in sorted(declared - operations.keys()):
        yield f'{name}: brak w katalogu.'
    for name, spec in sorted(operations.items()):
        cls = spec.get('class')
        routine = spec.get('method' if cls else 'function')
        key = (spec['module'], cls, routine)
        if key not in callables:
            yield f'{name}: brak implementacji.'
            continue
        if not cls and name not in declared and name not in LEGACY_READS:
            yield f'{name}: brak oznaczenia office_endpoint.'
        signature = parameters(callables[key])
        if spec['arguments'] != signature:
            yield f"{name}: katalog {spec['arguments']} różni się od sygnatury {signature}."
        bound_options = [*spec.get('actors', ()), *spec.get('self_only', ()),
                         *spec.get('callbacks', ()), *spec.get('cancellation', ()), *spec.get('files', ())]
        if set(bound_options) - set(signature):
            yield f'{name}: reguła wiązania dotyczy nieznanego parametru.'
        for rule in spec['capability'].split('&'):
            if rule not in ('admin', 'authenticated') and not rule.startswith(('tab:', 'permission:')):
                yield f'{name}: nieprawidłowa reguła dostępu {rule}.'
        if cls and f"{spec['module']}.{cls}.__new__" not in declared:
            yield f'{name}: klasa nie udostępnia obiektu widoku.'


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check', action='store_true', help='Sprawdza katalog bez modyfikowania plików.')
    parser.parse_args()
    operations = runpy.run_path(str(ROOT / 'app/remote/operations.py'))['OPERATIONS']
    errors = list(catalogue_errors(operations, ROOT / 'app'))
    if errors:
        raise SystemExit('\n'.join(errors))
    print(f'Zweryfikowano katalog HTTPS ({len(operations)} operacji).')
