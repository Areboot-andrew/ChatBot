"""External spare-part pricing: supplier site + route prompts + answer parts section.

Wires the structured supplier-price path into the DB so it shows up in the panel:
- bot_settings.meta.parts_sites := "gsm-forsage.com.ua" (only if empty) — appears in
  Settings ("Сайти постачальників запчастин").
- knowledge_types code='external_price' meta := new source/query/validation prompts
  (brand+model+part query, structured-price validation) — appears in Logic pipeline
  -> external_price -> prompts.
- bot_settings.meta.lean_answer_prompt := LEAN_ANSWER_PROMPT (now with the parts
  presentation section: variants original/copy, part + work orientation).

Revision ID: 37f0a1b2c3d4
Revises: 36f0a1b2c3d4
Create Date: 2026-06-21 02:00:00.000000
"""

from __future__ import annotations

import json

import sqlalchemy as sa
from alembic import op

from app.core.prompt_defaults import ROUTE_PROMPTS, LEAN_ANSWER_PROMPT


revision = "37f0a1b2c3d4"
down_revision = "36f0a1b2c3d4"
branch_labels = None
depends_on = None

SUPPLIER_SITE = "gsm-forsage.com.ua"


def upgrade() -> None:
    conn = op.get_bind()
    ext = ROUTE_PROMPTS["external_price"]

    # 1) bot_settings: supplier site (only if empty) + answer prompt with parts section
    rows = conn.execute(sa.text("SELECT id, meta FROM bot_settings")).mappings().all()
    for row in rows:
        meta = dict(row["meta"] or {})
        if not str(meta.get("parts_sites") or "").strip():
            meta["parts_sites"] = SUPPLIER_SITE
        meta["lean_answer_prompt"] = LEAN_ANSWER_PROMPT
        conn.execute(
            sa.text("UPDATE bot_settings SET meta = CAST(:m AS jsonb) WHERE id = :id"),
            {"m": json.dumps(meta, ensure_ascii=False), "id": row["id"]},
        )

    # 2) external_price route prompts (Logic pipeline)
    rows = conn.execute(
        sa.text("SELECT id, meta FROM knowledge_types WHERE code = 'external_price'")
    ).mappings().all()
    for row in rows:
        meta = dict(row["meta"] or {})
        meta["tool_name"] = ext["tool_name"]
        meta["source_description"] = ext["source_description"]
        meta["query_prompt"] = ext["query_prompt"]
        meta["result_validation_prompt"] = ext["result_validation_prompt"]
        conn.execute(
            sa.text("UPDATE knowledge_types SET meta = CAST(:m AS jsonb), enabled = true WHERE id = :id"),
            {"m": json.dumps(meta, ensure_ascii=False), "id": row["id"]},
        )


def downgrade() -> None:
    # Config/prompt seed; parts_sites and prompts can be re-edited from the panel.
    pass
