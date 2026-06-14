import json
import unittest
from unittest.mock import patch

from app.core import agent_lean


class LeanRouteIsolationTests(unittest.IsolatedAsyncioTestCase):
    async def test_route_owns_query_and_validation_in_one_private_memory(self):
        calls = []

        async def fake_chat(messages, *args, **kwargs):
            calls.append([dict(message) for message in messages])
            if len(calls) == 1:
                return json.dumps({"query": "Xiaomi Redmi Note 10 LCD"})
            return json.dumps({
                "relevant": True,
                "sufficient": True,
                "facts": ["LCD коштує 1200 грн у зовнішнього постачальника"],
                "fallback": None,
            }, ensure_ascii=False)

        async def fake_tool(route, query, *args, **kwargs):
            self.assertEqual(query, "Xiaomi Redmi Note 10 LCD")
            return "Xiaomi Redmi Note 10 LCD — 1200 грн", "search_parts"

        route = {
            "code": "external_price",
            "label": "Parts",
            "source_description": "SUPPLIER SOURCE PROMPT",
            "query_prompt": "ROUTE QUERY PROMPT",
            "result_validation_prompt": "ROUTE VALIDATION PROMPT",
        }
        request = {
            "question": "Find the display price",
            "needed_fact": "price",
            "device_type": "smartphone",
            "brand": "Xiaomi",
            "model": "Redmi Note 10",
            "service": "display replacement",
            "part": "LCD",
        }
        with patch.object(agent_lean, "_safe_chat", fake_chat), patch.object(agent_lean, "_run_tool", fake_tool):
            result = await agent_lean._run_route_session(
                route, request, "client text", None, None, None, {}, None,
                "model", None, None, lambda *a: None, 1,
            )

        self.assertTrue(result["sufficient"])
        self.assertEqual(len(calls), 2)
        first_system = calls[0][0]["content"]
        self.assertIn("SUPPLIER SOURCE PROMPT", first_system)
        self.assertIn("ROUTE QUERY PROMPT", first_system)
        self.assertIn("ROUTE VALIDATION PROMPT", first_system)
        self.assertNotIn("Інженер Андрон", first_system)
        self.assertNotIn("MARKETING", first_system)

        # The validation turn keeps the route's first query turn as private,
        # route-local memory and adds only the raw result from its own source.
        self.assertEqual(calls[1][0:2], calls[0])
        self.assertEqual(calls[1][2]["role"], "assistant")
        self.assertIn("Xiaomi Redmi Note 10 LCD", calls[1][2]["content"])
        self.assertIn("1200 грн", calls[1][3]["content"])

    async def test_irrelevant_route_cannot_export_facts(self):
        responses = iter([
            json.dumps({"query": "ремонт бетономішалки"}),
            json.dumps({
                "relevant": False,
                "sufficient": False,
                "facts": ["Сервіс ремонтує бетономішалки"],
                "fallback": "Каталог цього не підтвердив",
            }, ensure_ascii=False),
        ])

        async def fake_chat(*args, **kwargs):
            return next(responses)

        async def fake_tool(*args, **kwargs):
            return "Категорія: дрібна побутова техніка", "search_catalog"

        route = {
            "code": "catalog",
            "label": "Каталог",
            "source_description": "catalog",
            "query_prompt": "build catalog query",
            "result_validation_prompt": "validate category",
        }
        with patch.object(agent_lean, "_safe_chat", fake_chat), patch.object(agent_lean, "_run_tool", fake_tool):
            result = await agent_lean._run_route_session(
                route, {"question": "бетономішалки", "needed_fact": "availability"},
                "text", None, None, None, {}, None, "m", None, None, lambda *a: None, 1,
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
