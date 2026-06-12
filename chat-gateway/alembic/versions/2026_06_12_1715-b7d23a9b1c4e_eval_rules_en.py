"""eval rules en

Revision ID: b7d23a9b1c4e
Revises: a82b39c0f7d1
Create Date: 2026-06-12 17:15:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

# revision identifiers, used by Alembic.
revision: str = 'b7d23a9b1c4e'
down_revision: Union[str, None] = 'a82b39c0f7d1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    en_rules = """--- [ABSOLUTE RULE: HALLUCINATION BAN] ---
1. If the question is about technical specs, compatibility, availability, or prices - use ONLY the data from the blocks above (Web Search, Price, FAQ).
2. If there is NO direct answer in the provided context - YOU ARE STRICTLY FORBIDDEN from inventing it from your own memory.
3. If data is missing, answer in your style, something like: "I don't have exact technical data on this hardware, I need to see it" or ask the client to provide the exact model.
4. NO assumptions about compatibility. Either 100% confirmation in context, or you don't know."""

    en_rules_escaped = en_rules.replace("'", "''")
    
    op.execute(f"""
    UPDATE bot_settings 
    SET meta = CASE WHEN meta IS NULL THEN '{{}}'::jsonb ELSE meta END || jsonb_build_object('tpl_evaluation_rules', '{en_rules_escaped}')
    WHERE meta->>'tpl_evaluation_rules' IS NOT NULL OR meta IS NULL OR meta->>'tpl_evaluation_rules' = '';
    """)


def downgrade() -> None:
    pass
