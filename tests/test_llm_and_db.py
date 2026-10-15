import os
import unittest
from unittest.mock import patch

from app.database import engine
from app.services.openrouter import get_llm_credentials
from app.services.rag import KeywordEmbeddings, _resolve_llm_config, get_embeddings
from langchain_core.documents import Document
from langchain_core.vectorstores import InMemoryVectorStore


class TestLLMAndDatabaseConfig(unittest.TestCase):

    def test_database_pool_pre_ping_configured(self):
        """Ensure connection pool pre-ping is enabled to prevent stale SSL connection drops."""
        self.assertTrue(engine.pool._pre_ping)

    def test_get_llm_credentials_deepseek(self):
        """Verify DeepSeek credentials resolution when DEEPSEEK_API_KEY is present."""
        with patch.dict(os.environ, {"GROQ_API_KEY": "", "GEMINI_API_KEY": "", "DEEPSEEK_API_KEY": "sk-deepseek-test", "OPENROUTER_API_KEY": ""}, clear=False):
            api_key, url, model = get_llm_credentials()
            self.assertEqual(api_key, "sk-deepseek-test")
            self.assertIn("deepseek.com", url)
            self.assertEqual(model, "deepseek-chat")

    def test_get_llm_credentials_openrouter_fallback(self):
        """Verify OpenRouter credentials resolution when only OPENROUTER_API_KEY is present."""
        with patch.dict(os.environ, {"GROQ_API_KEY": "", "GEMINI_API_KEY": "", "DEEPSEEK_API_KEY": "", "OPENROUTER_API_KEY": "sk-or-test"}, clear=False):
            api_key, url, model = get_llm_credentials()
            self.assertEqual(api_key, "sk-or-test")
            self.assertIn("openrouter.ai", url)
            self.assertEqual(model, "openai/gpt-4o-mini")

    def test_keyword_embeddings_local_search(self):
        """Verify local KeywordEmbeddings work with InMemoryVectorStore without any external API calls."""
        emb = KeywordEmbeddings(dim=128)
        docs = [
            Document(page_content="The Canteen serves fresh chai and samosas.", metadata={"title": "Canteen"}),
            Document(page_content="The Main Gate provides ticket validation and security.", metadata={"title": "Gate"}),
        ]
        vs = InMemoryVectorStore.from_documents(docs, embedding=emb)
        results = vs.similarity_search("Where can I buy chai and snacks?", k=1)
        self.assertEqual(len(results), 1)
        self.assertIn("Canteen", results[0].page_content)

    def test_get_llm_credentials_groq(self):
        """Verify Groq credentials resolution when GROQ_API_KEY is present."""
        with patch.dict(os.environ, {"GROQ_API_KEY": "gsk-test", "GEMINI_API_KEY": "", "DEEPSEEK_API_KEY": "", "OPENROUTER_API_KEY": ""}, clear=False):
            api_key, url, model = get_llm_credentials()
            self.assertEqual(api_key, "gsk-test")
            self.assertIn("groq.com", url)
            self.assertEqual(model, "openai/gpt-oss-120b")

    def test_get_llm_credentials_gemini(self):
        """Verify Gemini credentials resolution when GEMINI_API_KEY is present."""
        with patch.dict(os.environ, {"GROQ_API_KEY": "", "GEMINI_API_KEY": "AIzaSy-test", "DEEPSEEK_API_KEY": "", "OPENROUTER_API_KEY": ""}, clear=False):
            api_key, url, model = get_llm_credentials()
            self.assertEqual(api_key, "AIzaSy-test")
            self.assertIn("generativelanguage.googleapis.com", url)
            self.assertEqual(model, "gemini-1.5-flash")

    def test_resolve_llm_config_deepseek(self):
        """Verify RAG resolves to DeepSeek when DEEPSEEK_API_KEY is set."""
        with patch.dict(os.environ, {"GROQ_API_KEY": "", "GEMINI_API_KEY": "", "DEEPSEEK_API_KEY": "sk-deepseek-test", "OPENROUTER_API_KEY": ""}, clear=False):
            api_key, base_url, model = _resolve_llm_config()
            self.assertEqual(api_key, "sk-deepseek-test")
            self.assertIn("deepseek.com", base_url)
            self.assertEqual(model, "deepseek-chat")


if __name__ == "__main__":
    unittest.main()
