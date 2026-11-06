"""Uploaded CRD resources and strict offline dependency resolution."""
import hashlib
import json
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch

from lxml import etree

from app.documents import crd_forms as crd


class CrdResourcesTests(unittest.TestCase):
    def test_supplied_resources_match_manifest_and_parse_as_xml(self):
        manifest = json.loads((crd.RESOURCES / 'manifest.json').read_text())
        for url, entry in manifest.items():
            if entry.get('status') == 'missing':
                continue
            with self.subTest(url=url):
                path = crd.RESOURCES / entry['path']
                self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), entry['sha256'])
                etree.parse(str(path), crd._parser())

    def test_all_transitive_dependencies_and_both_full_schemas_are_available_offline(self):
        for kind in crd.VERSIONS:
            with self.subTest(kind=kind):
                self.assertEqual(crd.missing_dependencies(kind), [])
                self.assertIsInstance(crd.schema(kind), etree.XMLSchema)

    def test_both_official_stylesheets_compile_with_external_access_disabled(self):
        for kind in crd.VERSIONS:
            stylesheet = etree.parse(str(crd.RESOURCES / kind / 'styl.xsl'), crd._parser())
            transform = etree.XSLT(stylesheet, access_control=etree.XSLTAccessControl.DENY_ALL)
            document = etree.Element('{' + crd.VERSIONS[kind] + '}Dokument')
            self.assertIn('html', str(transform(document)).lower())

    def test_unsigned_xml_is_not_rendered_without_full_schema_validation(self):
        for kind in crd.VERSIONS:
            xml = ('<Dokument xmlns="' + crd.VERSIONS[kind] + '"/>').encode()
            with self.assertRaises(etree.DocumentInvalid):
                crd.render_xml_to_html(kind, xml)

    def test_a_missing_dependency_still_blocks_validation_without_network_fallback(self):
        with tempfile.TemporaryDirectory() as directory:
            resources = Path(directory) / 'crd'
            shutil.copytree(crd.RESOURCES, resources)
            (resources / 'shared/CountryCode.xsd').unlink()
            with patch.object(crd, 'RESOURCES', resources):
                for kind in crd.VERSIONS:
                    with self.assertRaisesRegex(crd.MissingSchemaDependency, 'CountryCode.xsd'):
                        crd.schema(kind)

    def test_wrong_versions_and_external_entities_are_rejected(self):
        with self.assertRaisesRegex(ValueError, 'wersję'):
            crd.validate_xml('PEL', b'<Dokument xmlns="https://example.test/"/>')
        xml = ('<!DOCTYPE Dokument [<!ENTITY local SYSTEM "file:///etc/passwd">]>'
               '<Dokument xmlns="' + crd.VERSIONS['PEL'] + '">&local;</Dokument>').encode()
        with self.assertRaisesRegex(ValueError, 'DTD'):
            crd.validate_xml('PEL', xml)
        with self.assertRaisesRegex(ValueError, 'Nieznany'):
            crd.schema('../PEL')

    def test_resolver_never_falls_back_to_network_or_arbitrary_local_files(self):
        resolver = crd._OfflineResolver()
        for uri in ('https://example.test/schema.xsd', Path('/etc/passwd').as_uri()):
            with self.subTest(uri=uri):
                with self.assertRaisesRegex(crd.MissingSchemaDependency, 'Nieznana zależność'):
                    resolver.resolve(uri, None, None)
