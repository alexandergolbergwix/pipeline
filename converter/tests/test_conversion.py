"""Integration tests for MARC to TTL conversion."""

import unittest

from converter.api import converter_api
from converter.config.namespaces import CIDOC, HM, LRMOO
from converter.rdf.graph_builder import GraphBuilder
from converter.transformer.field_handlers import FieldHandlers
from converter.transformer.mapper import MarcToRdfMapper
from converter.transformer.uri_generator import UriGenerator
from converter.validation.shacl_validator import ShaclValidator


class TestFieldHandlers(unittest.TestCase):
    """Test MARC field handlers."""

    def test_parse_dimensions_mm(self):
        result = FieldHandlers._parse_dimensions("280 x 200 mm")
        self.assertEqual(result["height_mm"], 280)
        self.assertEqual(result["width_mm"], 200)

    def test_parse_dimensions_cm(self):
        result = FieldHandlers._parse_dimensions("28 cm")
        self.assertEqual(result["height_mm"], 280)

    def test_parse_extent_leaves(self):
        result = FieldHandlers._parse_extent("248 leaves")
        self.assertEqual(result, 248)

    def test_parse_extent_ff(self):
        result = FieldHandlers._parse_extent("150 ff.")
        self.assertEqual(result, 150)

    def test_parse_extent_hebrew(self):
        result = FieldHandlers._parse_extent("151 דפים")
        self.assertEqual(result, 151)

    def test_parse_person_dates_range(self):
        result = FieldHandlers._parse_person_dates("1135-1204")
        self.assertEqual(result["birth_year"], 1135)
        self.assertEqual(result["death_year"], 1204)

    def test_parse_person_dates_active(self):
        result = FieldHandlers._parse_person_dates("פעיל 1407")
        self.assertEqual(result["active_year"], 1407)


class TestUriGenerator(unittest.TestCase):
    """Test URI generation."""

    def setUp(self):
        self.gen = UriGenerator()

    def test_manuscript_uri(self):
        uri = self.gen.manuscript_uri("990001234560205171")
        self.assertIn("MS_", str(uri))

    def test_work_uri(self):
        uri = self.gen.work_uri("גינת אגוז")
        self.assertIn("Work_", str(uri))

    def test_person_uri(self):
        uri = self.gen.person_uri("משה בן יצחק")
        self.assertIn("Person_", str(uri))

    def test_normalize_hebrew(self):
        result = self.gen.normalize_hebrew("שלום עולם")
        self.assertIn("שלום", result)

    def test_work_creation_event_uri_with_author(self):
        uri = self.gen.work_creation_event_uri("משנה תורה", 'רמב"ם')
        self.assertIn("WorkCreation_", str(uri))
        self.assertIn("_by_", str(uri))

    def test_evidence_chain_uri_without_field(self):
        uri = self.gen.evidence_chain_uri("990012345680205171")
        self.assertIn("EvidenceChain_990012345680205171", str(uri))


class TestGraphBuilder(unittest.TestCase):
    """Test RDF graph building."""

    def setUp(self):
        self.builder = GraphBuilder()

    def test_build_minimal_graph(self):
        from converter.transformer.field_handlers import ExtractedData

        data = ExtractedData()
        data.title = "Test Work"
        data.external_ids = {"nli": "12345"}

        graph = self.builder.build_graph(data, "12345")

        self.assertGreater(len(graph), 0)

    def test_work_creation_event_created_for_author(self):
        from rdflib import URIRef
        from rdflib.namespace import RDF

        from converter.transformer.field_handlers import ExtractedData

        control_number = "990012345680205171"
        data = ExtractedData()
        data.title = "משנה תורה"
        data.authors = [{"name": 'רמב"ם'}]

        graph = self.builder.build_graph(data, control_number)

        expected_creation_uri = URIRef(f"{str(HM)}WorkCreation_משנה_תורה_by_רמבם")
        self.assertIn((expected_creation_uri, RDF.type, LRMOO.F27_Work_Creation), graph)
        work_uri = URIRef(f"{str(HM)}Work_משנה_תורה_by_רמבם")
        person_uri = URIRef(f"{str(HM)}Person_רמבם")
        self.assertIn((expected_creation_uri, LRMOO.R16_created, work_uri), graph)
        self.assertIn((expected_creation_uri, CIDOC.P14_carried_out_by, person_uri), graph)


class TestMapper(unittest.TestCase):
    """Test the main mapper."""

    def test_mapper_initialization(self):
        mapper = MarcToRdfMapper()
        self.assertIsNotNone(mapper.uri_generator)
        self.assertIsNotNone(mapper.graph_builder)


class TestShaclValidator(unittest.TestCase):
    """Test SHACL validation."""

    def test_validator_initialization(self):
        validator = ShaclValidator()
        self.assertIsNotNone(validator.shapes_path)

    def test_validate_empty_graph(self):
        from rdflib import Graph

        validator = ShaclValidator()
        graph = Graph()

        result = validator.validate(graph)
        self.assertTrue(result.conforms)


class TestConverterApi(unittest.TestCase):
    """Test converter API metadata alignment."""

    def test_get_version_reports_ontology_v14(self):
        version = converter_api.get_version()
        self.assertEqual(version.get("ontology_version"), "1.4")


def run_tests():
    """Run all tests."""
    unittest.main(module="converter.tests.test_conversion", exit=False)


if __name__ == "__main__":
    unittest.main()
