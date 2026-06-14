import json
import unittest
from unittest.mock import patch

from app.core import agent_lean


class LeanRouteIsolationTests(unittest.IsolatedAsyncioTestCase):
    async def test_query_worker_receives_only_route_prompt_and_structured_request(self):
        captured = []

        async def fake_chat(messages, *args, **kwargs):
            captured.extend(messages)
            return "Xiaomi Redmi Note 10 LCD"

        route = {"code": "external_price", "query_prompt": "brand + model + exact part"}
        request = {
            "question": "Find the display price",
            "needed_fact": "price",
            "device_type": "smartphone",
            "brand": "Xiaomi",
            "model": "Redmi Note 10",
            "service": "display replacement",
            "part": "LCD",
        }
        with patch.object(agent_lean, "_safe_chat", fake_chat):
            query = await agent_lean._build_query(route, request, "m", None, None, lambda *a: None, 1)

        self.assertEqual(query, "Xiaomi Redmi Note 10 LCD")
        self.assertEqual(len(captured), 2)
        self.assertIn(route["query_prompt"], captured[0]["content"])
        self.assertEqual(json.loads(captured[1]["content"]), request)
        self.assertNotIn("marketing", " ".join(m["content"].lower() for m in captured))

    async def test_validator_receives_only_its_route_instructions(self):
        captured = []

        async def fake_chat(messages, *args, **kwargs):
            captured.extend(messages)
            return json.dumps({
                "relevant": True,
                "sufficient": True,
                "facts": ["Робота коштує 800-1200 грн"],
                "fallback": None,
            }, ensure_ascii=False)

        route = {
            "label": "Каталог",
            "source_description": "Internal service catalog only",
            "result_validation_prompt": "Accept the same device category and service",
        }
        request = {"question": "Price", "needed_fact": "price", "device_type": "smartphone"}
        with patch.object(agent_lean, "_safe_chat", fake_chat):
            result = await agent_lean._clean_source(
                route, request, "Заміна роз'єму смартфона — 800-1200 грн", "m", None, None,
                lambda *a: None, 1,
            )

        self.assertTrue(result["sufficient"])
        system = captured[0]["content"]
        self.assertIn(route["source_description"], system)
        self.assertIn(route["result_validation_prompt"], system)
        self.assertNotIn("Інженер Андрон", system)
        self.assertNotIn("MARKETING", system)

    async def test_irrelevant_route_cannot_export_facts(self):
        async def fake_chat(messages, *args, **kwargs):
            return json.dumps({
                "relevant": False,
                "sufficient": False,
                "facts": ["Сервіс ремонтує бетономішалки"],
                "fallback": "Каталог цього не підтвердив",
            }, ensure_ascii=False)

        route = {"label": "Каталог", "source_description": "catalog", "result_validation_prompt": "validate"}
        with patch.object(agent_lean, "_safe_chat", fake_chat):
            result = await agent_lean._clean_source(
                route, {"question": "бетономішалки", "needed_fact": "availability"},
                "Категорії: дрібна побутова техніка", "m", None, None, lambda *a: None, 1,
            )

        self.assertFalse(result["relevant"])
        self.assertEqual(result["facts"], [])
        self.assertEqual(result["fallback"], "Каталог цього не підтвердив")

    def test_controller_map_keeps_meaningful_route_description(self):
        description = "First sentence. Critical second sentence: use only for an unknown generic device type."
        source_map = agent_lean._source_map({
            "web": {"code": "web", "label": "Web", "triggers": [], "source_description": description}
        })
        self.assertIn("Critical second sentence", source_map)


if __name__ == "__main__":
    unittest.main()
