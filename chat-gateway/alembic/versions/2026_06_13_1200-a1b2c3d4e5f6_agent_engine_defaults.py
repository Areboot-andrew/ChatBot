"""agent engine defaults: engine switch, tools, business_info placeholder

Populates bot_settings.meta with agent-engine defaults for existing tenants
so the new agentic loop works right after deploy. Existing keys are preserved
(only missing ones are added).

Revision ID: a1b2c3d4e5f6
Revises: 5c8f1e2b4a3d
Create Date: 2026-06-13 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'a1b2c3d4e5f6'
down_revision: Union[str, None] = '5c8f1e2b4a3d'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Merge defaults UNDER existing meta (existing keys win), so tenant
    # customizations made earlier are never overwritten.
    op.execute("""
    UPDATE bot_settings
    SET meta = jsonb_build_object(
        'engine', 'agent',
        'agent_max_iterations', '4',
        'enabled_tools', '[]'::jsonb,
        'business_info', '{}'::jsonb
    ) || CASE WHEN meta IS NULL THEN '{}'::jsonb ELSE meta END;
    """)


def downgrade() -> None:
    op.execute("""
    UPDATE bot_settings
    SET meta = (CASE WHEN meta IS NULL THEN '{}'::jsonb ELSE meta END)
        - 'engine' - 'agent_max_iterations' - 'enabled_tools' - 'business_info';
    """)
