import unittest
from unittest.mock import patch, MagicMock, AsyncMock

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.models import HeritageSite, Node, Prompt
from app.services.rag import (
    build_site_documents,
    get_active_guardrails_and_node,
    format_retrieved_context,
    invalidate_site_cache,
    clear_all_caches,
    _SITE_VECTORSTORES,
    run_rag_chat,
)
from langchain_core.documents import Document
from langchain_core.messages import AIMessage


class TestLangChainRAG(unittest.TestCase):
    def setUp(self):
        # Create an in-memory SQLite database for testing
        self.engine = create_engine("sqlite:///:memory:", echo=False)
        Base.metadata.create_all(bind=self.engine)
        self.Session = sessionmaker(bind=self.engine)
        self.db = self.Session()

        # Seed test heritage site
        self.site = HeritageSite(
            id=1,
            name="IIIT Sonepat",
            location="Sonepat, Haryana",
            latitude=28.98,
            longitude=77.15,
            summary="Premier technical institute in Sonepat.",
            history="Established under PPP model.",
            fun_facts="Offers cutting-edge research facilities.",
            helpline_number="1800-123-456",
        )
        self.db.add(self.site)

        # Seed nodes
        self.node1 = Node(
            id=1,
            site_id=1,
            name="Main Entrance Gate",
            description="Primary security threshold and navigation point.",
            sequence_order=1,
            latitude=28.981, longitude=77.151, is_king=True,
            qr_code_value="Q0v",
        )
        self.node2 = Node(
            id=2,
            site_id=1,
            name="Canteen Area",
            description="Social gathering area for meals and snacks.",
            sequence_order=2,
            latitude=28.982, longitude=77.152, is_king=False,
            qr_code_value="Q0w",
        )
        self.db.add_all([self.node1, self.node2])

        # Seed prompts (with guardrails)
        self.prompt_site = Prompt(
            id=1,
            site_id=1,
            node_id=None,
            title="IIIT Sonepat - General",
            content="Welcome to IIIT Sonepat. As Shree, answer general questions about campus life.",
        )
        self.prompt_node1 = Prompt(
            id=2,
            site_id=1,
            node_id=1,
            title="Main Entrance Gate - IIIT Sonepat - SHREE",
            content="The visitor is at the Main Entrance Gate. Keep responses strictly limited to this location. From here visitors can access Canteen and Mess. Direct amenity questions to the directions feature.",
        )
        self.db.add_all([self.prompt_site, self.prompt_node1])
        self.db.commit()

        clear_all_caches()

    def tearDown(self):
        self.db.close()
        clear_all_caches()

    def test_build_site_documents(self):
        docs, site_name, site = build_site_documents(self.db, site_id=1)
        self.assertEqual(site_name, "IIIT Sonepat")
        self.assertIsNotNone(site)

        # We seeded 2 prompts, 2 nodes, 1 site overview => at least 5 documents
        self.assertGreaterEqual(len(docs), 5)

        # Check document contents
        sources = [d.metadata["source"] for d in docs]
        self.assertIn("prompts_table", sources)
        self.assertIn("nodes_table", sources)
        self.assertIn("heritage_sites_table", sources)

    def test_get_active_guardrails_node_level(self):
        site_name, node_name, guardrails = get_active_guardrails_and_node(
            self.db, site_id=1, node_id=1
        )
        self.assertEqual(site_name, "IIIT Sonepat")
        self.assertEqual(node_name, "Main Entrance Gate")
        self.assertIn("Main Entrance Gate", guardrails)
        self.assertIn("Keep responses strictly limited", guardrails)

    def test_get_active_guardrails_site_level(self):
        site_name, node_name, guardrails = get_active_guardrails_and_node(
            self.db, site_id=1, node_id=None
        )
        self.assertEqual(site_name, "IIIT Sonepat")
        self.assertEqual(node_name, "General Site Entrance")
        self.assertIn("Welcome to IIIT Sonepat", guardrails)

    def test_format_retrieved_context(self):
        docs = [
            Document(page_content="Canteen serves snacks.", metadata={"title": "Canteen"}),
            Document(page_content="Main gate is secure.", metadata={"title": "Entrance"}),
        ]
        formatted = format_retrieved_context(docs)
        self.assertIn("Context Snippet 1 (Canteen)", formatted)
        self.assertIn("Context Snippet 2 (Entrance)", formatted)
        self.assertIn("Canteen serves snacks.", formatted)

    def test_cache_invalidation(self):
        _SITE_VECTORSTORES[1] = MagicMock()
        self.assertIn(1, _SITE_VECTORSTORES)

        invalidate_site_cache(1)
        self.assertNotIn(1, _SITE_VECTORSTORES)

    @patch("app.services.rag.get_llm")
    @patch("app.services.rag.get_or_build_site_vectorstore")
    async def _async_test_rag_chat(self, mock_vs, mock_get_llm):
        mock_retriever_doc = Document(
            page_content="From the Main Entrance, the Canteen is straight ahead.",
            metadata={"title": "Main Entrance Guide"}
        )
        mock_vectorstore_inst = MagicMock()
        mock_vectorstore_inst.similarity_search.return_value = [mock_retriever_doc]
        mock_vs.return_value = mock_vectorstore_inst

        mock_llm_inst = MagicMock()
        mock_llm_inst.ainvoke = AsyncMock(return_value=AIMessage(content="From here, you can head straight to the canteen for snacks."))
        mock_get_llm.return_value = mock_llm_inst

        reply = await run_rag_chat(
            site_id=1,
            node_id=1,
            message="Where can I get snacks?",
            history=[],
            db=self.db,
        )

        self.assertIn("canteen", reply.lower())
        mock_vectorstore_inst.similarity_search.assert_called_once()
        mock_llm_inst.ainvoke.assert_called_once()

    def test_run_rag_chat(self):
        import asyncio
        asyncio.run(self._async_test_rag_chat())


if __name__ == "__main__":
    unittest.main()
