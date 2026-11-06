"""Offline validation and official XSL visualization for the supplied CRD versions.

This module does not create signatures or establish legal currency of a form.
Missing schema dependencies must never be replaced with permissive placeholders.
"""
from __future__ import annotations

import json
from pathlib import Path
from urllib.parse import urlsplit

from lxml import etree

RESOURCES = Path(__file__).with_name('templates') / 'crd'
VERSIONS = {'UPL': 'http://crd.gov.pl/wzor/2018/11/02/6046/',
            'PEL': 'http://crd.gov.pl/wzor/2012/11/29/1053/'}


class MissingSchemaDependency(ValueError):
    pass


def _manifest():
    return json.loads((RESOURCES / 'manifest.json').read_text(encoding='utf-8'))


def _kind(kind):
    if kind not in VERSIONS:
        raise ValueError('Nieznany wzór CRD.')
    return kind


class _OfflineResolver(etree.Resolver):
    def __init__(self):
        super().__init__()
        self.paths = {}
        for url, entry in _manifest().items():
            path = (RESOURCES / entry['path']).resolve()
            if not path.is_relative_to(RESOURCES.resolve()):
                raise ValueError('Nieprawidłowa ścieżka zasobu CRD.')
            self.paths[url] = path
            self.paths[url.replace('http://', 'https://', 1)] = path
            self.paths[str(path)] = path
            self.paths[path.as_uri()] = path

    def resolve(self, url, public_id, context):
        path = self.paths.get(url)
        if path is None:
            raise MissingSchemaDependency('Nieznana zależność schematu: ' + Path(urlsplit(url).path).name)
        if not path.is_file():
            raise MissingSchemaDependency('Brak pliku ' + path.name + '. Pełna walidacja XSD jest niedostępna.')
        return self.resolve_filename(str(path), context)


def _parser():
    parser = etree.XMLParser(resolve_entities=False, no_network=True, load_dtd=False)
    parser.resolvers.add(_OfflineResolver())
    return parser


def missing_dependencies(kind):
    """Return missing transitive dependencies of this particular supplied schema."""
    pending = [VERSIONS[_kind(kind)] + 'schemat.xsd']
    manifest = _manifest()
    visited, missing = set(), []
    while pending:
        url = pending.pop()
        if url in visited:
            continue
        visited.add(url)
        entry = manifest.get(url)
        if not entry or not (RESOURCES / entry['path']).is_file():
            missing.append(url)
            continue
        root = etree.parse(str(RESOURCES / entry['path']), _parser())
        pending.extend(root.xpath('//xs:import/@schemaLocation | //xs:include/@schemaLocation',
                                  namespaces={'xs': 'http://www.w3.org/2001/XMLSchema'}))
    return sorted(missing)


def schema(kind):
    kind = _kind(kind)
    missing = missing_dependencies(kind)
    if missing:
        names = ', '.join(Path(urlsplit(url).path).name for url in missing)
        raise MissingSchemaDependency('Brak plików: ' + names + '. Pełna walidacja XSD jest niedostępna.')
    return etree.XMLSchema(etree.parse(str(RESOURCES / kind / 'schemat.xsd'), _parser()))


def validate_xml(kind, xml):
    """Validate against the unmodified full XSD, without resolving network URLs."""
    kind = _kind(kind)
    document = etree.fromstring(xml, _parser())
    if document.getroottree().docinfo.doctype or any(isinstance(n, etree._Entity) for n in document.iter()):
        raise ValueError('Dokument CRD nie może zawierać DTD ani encji zewnętrznych.')
    if document.tag != '{' + VERSIONS[kind] + '}Dokument':
        raise ValueError('Dokument ma inną wersję lub przestrzeń nazw niż wybrany wzór CRD.')
    validator = schema(kind)
    validator.assertValid(document)
    return document


def render_xml_to_html(kind, xml):
    """Render only a validated document using the matching official local XSL."""
    document = validate_xml(kind, xml)
    stylesheet = etree.parse(str(RESOURCES / kind / 'styl.xsl'), _parser())
    transform = etree.XSLT(stylesheet, access_control=etree.XSLTAccessControl.DENY_ALL)
    return str(transform(document))
