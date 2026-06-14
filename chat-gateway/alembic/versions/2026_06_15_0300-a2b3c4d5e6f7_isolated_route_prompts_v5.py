"""isolated route prompts v5

Move the live tenant to the Lean controller and refresh only the three prompts
used by each isolated route worker. Previous values are stored for rollback.

Revision ID: a2b3c4d5e6f7
Revises: f1a2b3c4d5e6
Create Date: 2026-06-15 03:00:00.000000
"""
from pathlib import Path
from typing import Sequence, Union
import json

from alembic import op
import sqlalchemy as sa


revision: str = "a2b3c4d5e6f7"
down_revision: Union[str, None] = "f1a2b3c4d5e6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

SETTINGS_BACKUP = "_isolated_route_v5_settings_backup"
ROUTE_BACKUP = "_isolated_route_v5_prompt_backup"
ROUTE_FIELDS = ("source_description", "query_prompt", "result_validation_prompt")
TOOL_TO_CANON = {
    "search_catalog": "catalog",
    "search_knowledge": "qa",
    "web_research": "web_search",
    "search_parts": "external_price",
    "get_business_info": "business_info",
    "escalate": "handoff",
}


def _persona() -> str:
    candidates = [
        Path("/app/app/givi_system_prompt.md"),
        Path(__file__).resolve().parents[2] / "app" / "givi_system_prompt.md",
    ]
    for path in candidates:
        if path.is_file():
            return path.read_text(encoding="utf-8")
    raise FileNotFoundError("givi_system_prompt.md not found")


def upgrade() -> None:
    conn = op.get_bind()
    from app.core.prompt_defaults import ROUTE_PROMPTS

    for row in conn.execute(sa.text(
        "SELECT id, system_prompt, meta FROM bot_settings"
    )).mappings().all():
        meta = dict(row["meta"] or {})
        if SETTINGS_BACKUP not in meta:
            meta[SETTINGS_BACKUP] = {
                "system_prompt": row["system_prompt"],
                "engine": meta.get("engine"),
            }
        meta["engine"] = "lean"
        conn.execute(sa.text(
            "UPDATE bot_settings SET system_prompt = :prompt, meta = CAST(:meta AS jsonb) WHERE id = :id"
        ), {
            "prompt": _persona(),
            "meta": json.dumps(meta, ensure_ascii=False),
            "id": row["id"],
        })

    for row in conn.execute(sa.text(
        "SELECT id, code, meta FROM knowledge_types"
    )).mappings().all():
        meta = dict(row["meta"] or {})
        code = str(row["code"])
        canon_code = code if code in ROUTE_PROMPTS else TOOL_TO_CANON.get(str(meta.get("tool_name") or ""))
        canon = ROUTE_PROMPTS.get(canon_code or "")
        if not canon:
            continue
        if ROUTE_BACKUP not in meta:
            meta[ROUTE_BACKUP] = {field: meta.get(field) for field in ROUTE_FIELDS}
        for field in ROUTE_FIELDS:
            meta[field] = canon[field]
        conn.execute(sa.text(
            "UPDATE knowledge_types SET meta = CAST(:meta AS jsonb) WHERE id = :id"
        ), {"meta": json.dumps(meta, ensure_ascii=False), "id": row["id"]})


def downgrade() -> None:
    conn = op.get_bind()
    for row in conn.execute(sa.text(
        "SELECT id, meta FROM bot_settings WHERE meta ? :key"
    ), {"key": SETTINGS_BACKUP}).mappings().all():
        meta = dict(row["meta"] or {})
        backup = meta.pop(SETTINGS_BACKUP)
        if backup.get("engine") is None:
            meta.pop("engine", None)
        else:
            meta["engine"] = backup["engine"]
        conn.execute(sa.text(
            "UPDATE bot_settings SET system_prompt = :prompt, meta = CAST(:meta AS jsonb) WHERE id = :id"
        ), {
            "prompt": backup.get("system_prompt"),
            "meta": json.dumps(meta, ensure_ascii=False),
            "id": row["id"],
        })

    for row in conn.execute(sa.text(
        "SELECT id, meta FROM knowledge_types WHERE meta ? :key"
    ), {"key": ROUTE_BACKUP}).mappings().all():
        meta = dict(row["meta"] or {})
        backup = meta.pop(ROUTE_BACKUP)
        for field in ROUTE_FIELDS:
            if backup.get(field) is None:
                meta.pop(field, None)
            else:
                meta[field] = backup[field]
        conn.execute(sa.text(
            "UPDATE knowledge_types SET meta = CAST(:meta AS jsonb) WHERE id = :id"
        ), {"meta": json.dumps(meta, ensure_ascii=False), "id": row["id"]})
