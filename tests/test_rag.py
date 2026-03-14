"""Tests for the RAG pipeline — chunker, store, retriever (mocked embeddings)."""

import json
import os
import sys
import shutil
import tempfile
import unittest
from unittest.mock import MagicMock, patch

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from rag.store import save_index, load_index, index_exists
from rag.chunker import _extract_class_name, _extract_member_name, _parse_page
from rag.retriever import _cosine_similarity


# ---------------------------------------------------------------------------
# Test: Store (save/load round-trip)
# ---------------------------------------------------------------------------

class TestStore(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.tmpdir)

    def test_save_and_load_round_trip(self):
        chunks = [
            {"text": "Class: Foo\nMethod: bar", "source_url": "http://example.com"},
            {"text": "Class: Baz\nProperty: qux", "source_url": "http://example.com/2"},
        ]
        vectors = np.random.randn(2, 8).astype(np.float32)

        save_index(chunks, vectors, index_dir=self.tmpdir)
        loaded_chunks, loaded_vectors = load_index(index_dir=self.tmpdir)

        self.assertEqual(len(loaded_chunks), 2)
        self.assertEqual(loaded_chunks[0]["text"], chunks[0]["text"])
        np.testing.assert_array_almost_equal(loaded_vectors, vectors)

    def test_index_exists_false_when_empty(self):
        self.assertFalse(index_exists(index_dir=self.tmpdir))

    def test_index_exists_true_after_save(self):
        save_index([{"text": "x"}], np.zeros((1, 4), dtype=np.float32), index_dir=self.tmpdir)
        self.assertTrue(index_exists(index_dir=self.tmpdir))

    def test_load_returns_none_when_no_index(self):
        chunks, vecs = load_index(index_dir=self.tmpdir)
        self.assertIsNone(chunks)
        self.assertIsNone(vecs)


# ---------------------------------------------------------------------------
# Test: Chunker utilities
# ---------------------------------------------------------------------------

class TestChunkerUtils(unittest.TestCase):
    def test_extract_class_name_simple(self):
        self.assertEqual(_extract_class_name("ExtrudeFeatureInput.htm"), "ExtrudeFeatureInput")

    def test_extract_class_name_with_member(self):
        self.assertEqual(
            _extract_class_name("ExtrudeFeatureInput_setOneSideExtent.htm"),
            "ExtrudeFeatureInput",
        )

    def test_extract_member_name(self):
        self.assertEqual(
            _extract_member_name("ExtrudeFeatureInput_setOneSideExtent.htm"),
            "setOneSideExtent",
        )

    def test_extract_member_name_none_for_class_page(self):
        self.assertIsNone(_extract_member_name("ExtrudeFeatureInput.htm"))


class TestChunkerParsing(unittest.TestCase):
    def test_parse_simple_class_page(self):
        html = """
        <html><head><title>ExtrudeFeatureInput</title></head>
        <body>
        <p>Represents an extrude feature input for creating extrude features.</p>
        <a href="ExtrudeFeatureInput_setOneSideExtent.htm">setOneSideExtent</a>
        <a href="ExtrudeFeatureInput_startExtent.htm">startExtent</a>
        </body></html>
        """
        chunks = _parse_page(html, "ExtrudeFeatureInput.htm", "http://example.com")
        self.assertEqual(len(chunks), 1)
        self.assertIn("ExtrudeFeatureInput", chunks[0]["text"])
        self.assertIn("setOneSideExtent", chunks[0]["text"])
        self.assertEqual(chunks[0]["class_name"], "ExtrudeFeatureInput")
        self.assertIsNone(chunks[0]["member_name"])

    def test_parse_method_page(self):
        html = """
        <html><head><title>setOneSideExtent</title></head>
        <body>
        <p>Sets the extrude to be a single-direction extent.</p>
        <code>returnValue = extrudeFeatureInput.setOneSideExtent(extent, direction)</code>
        <table>
        <tr><th>Name</th><th>Type</th></tr>
        <tr><td>extent</td><td>ExtentDefinition</td></tr>
        <tr><td>direction</td><td>ExtentDirections</td></tr>
        </table>
        </body></html>
        """
        chunks = _parse_page(
            html,
            "ExtrudeFeatureInput_setOneSideExtent.htm",
            "http://example.com",
        )
        self.assertEqual(len(chunks), 1)
        chunk = chunks[0]
        self.assertEqual(chunk["class_name"], "ExtrudeFeatureInput")
        self.assertEqual(chunk["member_name"], "setOneSideExtent")
        self.assertIn("setOneSideExtent", chunk["text"])
        self.assertIn("ExtentDefinition", chunk["text"])

    def test_empty_page_returns_no_chunks(self):
        html = "<html><head></head><body></body></html>"
        chunks = _parse_page(html, "Empty.htm", "http://example.com")
        self.assertEqual(len(chunks), 0)


# ---------------------------------------------------------------------------
# Test: Cosine similarity
# ---------------------------------------------------------------------------

class TestCosineSimilarity(unittest.TestCase):
    def test_identical_vectors_score_one(self):
        v = np.array([1.0, 0.0, 0.0], dtype=np.float32)
        all_v = np.array([[1.0, 0.0, 0.0]], dtype=np.float32)
        scores = _cosine_similarity(v, all_v)
        self.assertAlmostEqual(scores[0], 1.0, places=5)

    def test_orthogonal_vectors_score_zero(self):
        v = np.array([1.0, 0.0, 0.0], dtype=np.float32)
        all_v = np.array([[0.0, 1.0, 0.0]], dtype=np.float32)
        scores = _cosine_similarity(v, all_v)
        self.assertAlmostEqual(scores[0], 0.0, places=5)

    def test_opposite_vectors_score_negative(self):
        v = np.array([1.0, 0.0, 0.0], dtype=np.float32)
        all_v = np.array([[-1.0, 0.0, 0.0]], dtype=np.float32)
        scores = _cosine_similarity(v, all_v)
        self.assertAlmostEqual(scores[0], -1.0, places=5)

    def test_ranking_order(self):
        query = np.array([1.0, 0.0, 0.0], dtype=np.float32)
        docs = np.array([
            [0.9, 0.1, 0.0],  # most similar
            [0.0, 1.0, 0.0],  # orthogonal
            [0.5, 0.5, 0.0],  # middle
        ], dtype=np.float32)
        scores = _cosine_similarity(query, docs)
        ranked = np.argsort(scores)[::-1]
        self.assertEqual(ranked[0], 0)  # most similar first
        self.assertEqual(ranked[-1], 1)  # orthogonal last

    def test_zero_query_returns_zeros(self):
        v = np.array([0.0, 0.0, 0.0], dtype=np.float32)
        all_v = np.array([[1.0, 0.0, 0.0]], dtype=np.float32)
        scores = _cosine_similarity(v, all_v)
        self.assertAlmostEqual(scores[0], 0.0, places=5)


# ---------------------------------------------------------------------------
# Test: Retriever with mocked embeddings
# ---------------------------------------------------------------------------

class TestRetriever(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        # Build a tiny test index
        self.chunks = [
            {"text": "Class: ExtrudeFeatures\nMethod: createInput\nCreates extrude input",
             "source_url": "http://docs/1", "class_name": "ExtrudeFeatures", "member_name": "createInput"},
            {"text": "Class: FilletFeatures\nMethod: createInput\nCreates fillet input",
             "source_url": "http://docs/2", "class_name": "FilletFeatures", "member_name": "createInput"},
            {"text": "Class: HoleFeatures\nMethod: createSimpleInput\nCreates hole input",
             "source_url": "http://docs/3", "class_name": "HoleFeatures", "member_name": "createSimpleInput"},
        ]
        # Fake embeddings: make extrude vector close to a query about extrusion
        self.vectors = np.array([
            [1.0, 0.0, 0.0, 0.0],  # extrude
            [0.0, 1.0, 0.0, 0.0],  # fillet
            [0.0, 0.0, 1.0, 0.0],  # hole
        ], dtype=np.float32)
        save_index(self.chunks, self.vectors, index_dir=self.tmpdir)

    def tearDown(self):
        shutil.rmtree(self.tmpdir)

    def test_retrieve_returns_ranked_results(self):
        from rag import retriever
        # Override the retriever's cache
        retriever._cached_chunks = self.chunks
        retriever._cached_vectors = self.vectors
        retriever._cache_loaded = True

        # Mock the embedding call to return a vector similar to "extrude"
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.data = [MagicMock()]
        mock_response.data[0].embedding = [0.9, 0.1, 0.0, 0.0]  # close to extrude
        mock_client.embeddings.create.return_value = mock_response

        results = retriever.retrieve("extrude a box", mock_client, k=2)

        self.assertGreater(len(results), 0)
        self.assertIn("ExtrudeFeatures", results[0])  # extrude should be first

        # Cleanup
        retriever.clear_cache()

    def test_retrieve_graceful_when_no_index(self):
        from rag import retriever
        retriever._cached_chunks = None
        retriever._cached_vectors = None
        retriever._cache_loaded = True

        mock_client = MagicMock()
        results = retriever.retrieve("anything", mock_client)
        self.assertEqual(results, [])

        retriever.clear_cache()


if __name__ == "__main__":
    unittest.main()
