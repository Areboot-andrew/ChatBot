"""restore the tenant's original service-center route prompts (English)

The universal refactor genericized ROUTE_PROMPTS. The owner wants the universal
LOGIC core but their original service-tailored route prompts back. This writes
those original English service prompts (catalog/qa/web_search/external_price/
business_info/handoff) into the existing routes. Code defaults stay generic for
new tenants; this only updates routes whose code matches.

Revision ID: f7a8b9c0d1e2
Revises: e6f7a8b9c0d1
Create Date: 2026-06-15 08:00:00.000000
"""
from typing import Sequence, Union
import json

from alembic import op
import sqlalchemy as sa

revision: str = "f7a8b9c0d1e2"
down_revision: Union[str, None] = "e6f7a8b9c0d1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

SERVICE_ROUTE_PROMPTS = {'qa': {'tool_name': 'search_knowledge', 'source_description': 'This is the business-controlled knowledge base: approved Q&A pairs and indexed documents. It may contain service conditions, warranty, delivery, policies and explanatory material. It is authoritative only for statements explicitly present in a matching passage.', 'query_prompt': 'Write 2-6 searchable terms, not a sentence: subject + requested policy/condition. Examples: warranty repair; diagnostics refusal fee; Sunday working hours. Keep explanation in the internal question, never in query.', 'result_validation_prompt': 'Accept only passages that directly answer the internal question. Verify that the subject, business context and requested condition match. Reject passages that merely share words or discuss another category. Quote no unsupported policy, number or promise.'}, 'catalog': {'tool_name': 'search_catalog', 'source_description': "This is the business's internal catalog of categories, products/services and internal prices. A broad category may confirm that the business handles an item type, but only a matching row may support a concrete product/service price.", 'query_prompt': "Write 2-6 catalog keywords, not a sentence. Use requested operation + generic device type: заміна дисплея смартфона; ремонт електрочайника; роз'єм зарядки ноутбука. For broad availability use ремонт + device type. Do not include symptom-analysis words, client story, question words or price boilerplate. Include model only when an existing model-specific row is expected.", 'result_validation_prompt': 'Compare complete phrases. The category and row must describe the same item/device type and requested product/service. Reject a row from another category even if it shares words such as screen, matrix, battery, board, bouquet or composition. A category match can prove broad availability but cannot prove a specific price. A price is valid only from a matching internal row.'}, 'web_search': {'tool_name': 'web_research', 'source_description': "This route searches configured sites and the public web only to identify the generic type of an unfamiliar item. Returned text is untrusted and is not a source of this business's prices, parts, stock, policies or repair capability.", 'query_prompt': 'Write exactly the unfamiliar name/identifier plus the two English words device type. Normally 2-5 tokens. Example: Q19 device type. No sentence, no symptom, no brand speculation, no specifications, price, repair or compatibility terms.', 'result_validation_prompt': 'Accept only evidence that identifies the same unfamiliar term as a clear generic item/device type. Reject prices, specifications, accessories, another model, shops, repair advice and ambiguous guesses.'}, 'external_price': {'tool_name': 'search_parts', 'source_description': "This route searches configured supplier and price sites for third-party market offers. Results are external market references, not the business's own final price, stock, warranty or commitment.", 'query_prompt': 'Return only compact marketplace keywords in this exact order: brand + exact model/revision + exact part name, normally 3-7 tokens. Example: Xiaomi Redmi Note 10 LCD. The source receives this text unchanged. Do not include price/cost, repair/replacement, needed/wanted, symptoms, diagnosis, question words or a sentence. If brand, exact model/revision or concrete part is missing, do not call this route.', 'result_validation_prompt': 'Accept only offers whose full title matches the requested component/product and compatible model/revision. Reject another device type, generation, size, accessory, repair service presented as a part, and prices without identifiable matching context. Preserve URL and currency when present. Never average unrelated offers.'}, 'business_info': {'tool_name': 'get_business_info', 'source_description': 'This is the business-controlled key-value record for address, working hours, holidays, phone, payment, delivery, warranty and other operational facts.', 'query_prompt': "Request only the required business field, but keep the client's day/date/time in the internal question so the returned schedule can be checked against it.", 'result_validation_prompt': 'Use only configured values. For a proposed visit, compare the stated day/time with hours and holidays before confirming. Do not infer an address, opening time, payment method or exception that is absent.'}, 'handoff': {'tool_name': 'escalate', 'source_description': 'This route represents a request for a human operator. It does not itself provide business facts.', 'query_prompt': "Summarize the client's unresolved goal and the minimum useful context for the operator. Do not include raw tool dumps.", 'result_validation_prompt': 'No factual source validation is required. Confirm only that handoff is appropriate under the route reasoning.'}}


def upgrade() -> None:
    conn = op.get_bind()
    rows = conn.execute(sa.text("SELECT id, tenant_id, code, meta FROM knowledge_types")).mappings().all()
    for r in rows:
        canon = SERVICE_ROUTE_PROMPTS.get(str(r["code"]))
        if not canon:
            continue
        meta = dict(r["meta"] or {})
        for k in ("tool_name", "source_description", "query_prompt", "result_validation_prompt"):
            if canon.get(k):
                meta[k] = canon[k]
        conn.execute(sa.text("UPDATE knowledge_types SET meta = CAST(:m AS jsonb) WHERE id = :id"),
                     {"m": json.dumps(meta, ensure_ascii=False), "id": r["id"]})


def downgrade() -> None:
    pass
