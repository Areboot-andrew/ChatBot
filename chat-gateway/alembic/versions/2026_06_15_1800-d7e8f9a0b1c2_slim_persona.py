"""slim the persona to tone + hard rules + examples (no procedural rulebook)

The system_prompt was a 112-line procedural rulebook (CONVERSATION LOGIC, CATALOG
RULES, WEB RESEARCH, TECHNICAL DISCIPLINE, CONVERSATION CONTROL...) that
duplicated and contradicted the lean stage prompts (e.g. 'ремонтуєте -> catalog'
while the controller now routes capability to qa) and bloated every ANSWER call.
Replaces it with a lean persona: voice + hard rules + safety + examples. Only
updates tenants whose persona is the texno «Інженер Андрон» one.

Revision ID: d7e8f9a0b1c2
Revises: c6d7e8f9a0b1
Create Date: 2026-06-15 18:00:00.000000
"""
from typing import Sequence, Union
from pathlib import Path
from alembic import op
import sqlalchemy as sa

revision: str = "d7e8f9a0b1c2"
down_revision: Union[str, None] = "c6d7e8f9a0b1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _persona() -> str:
    for p in (Path("/app/app/givi_system_prompt.md"),
              Path(__file__).resolve().parents[2] / "app" / "givi_system_prompt.md"):
        if p.is_file():
            return p.read_text(encoding="utf-8")
    raise FileNotFoundError("givi_system_prompt.md not found")


def upgrade() -> None:
    conn = op.get_bind()
    persona = _persona()
    rows = conn.execute(sa.text("SELECT id, system_prompt FROM bot_settings")).mappings().all()
    for r in rows:
        cur = str(r["system_prompt"] or "")
        if "Інженер Андрон" in cur or "texno.plus" in cur:
            conn.execute(sa.text("UPDATE bot_settings SET system_prompt = :p WHERE id = :id"),
                         {"p": persona, "id": r["id"]})


def downgrade() -> None:
    pass
