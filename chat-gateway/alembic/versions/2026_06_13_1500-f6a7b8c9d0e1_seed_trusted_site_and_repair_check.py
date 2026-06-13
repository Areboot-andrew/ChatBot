"""seed texno.plus trusted site + repair_check reasoning intent for existing tenants

Revision ID: f6a7b8c9d0e1
Revises: e5f6a7b8c9d0
Create Date: 2026-06-13 15:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = 'f6a7b8c9d0e1'
down_revision: Union[str, None] = 'e5f6a7b8c9d0'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

REPAIR_REASONING = (
    "Питання типу «ви ремонтуєте {прилад}». Витягни назву приладу. Якщо не знаєш точно "
    "що це за пристрій — знайди в інтернеті його категорію. Перевір каталог і наш сайт. "
    "Прайс — це НЕ повний список: відсутність у прайсі не означає що ми це не робимо "
    "(напр. блендер/міксер = дрібна побутова техніка). Якщо це електроніка або побутова "
    "техніка — ми ремонтуємо, запропонуй привезти на безкоштовну діагностику. Не "
    "відмовляй, поки не перевірив усі джерела."
)


def upgrade() -> None:
    conn = op.get_bind()

    # 1. Set trusted site to texno.plus where empty, only for the texno persona.
    conn.execute(sa.text(
        "UPDATE bot_settings "
        "SET meta = (CASE WHEN meta IS NULL THEN '{}'::jsonb ELSE meta END) "
        "         || jsonb_build_object('fallback_sites', 'texno.plus') "
        "WHERE system_prompt LIKE '%Інженер Андрон%' "
        "AND COALESCE(meta->>'fallback_sites', '') = ''"
    ))

    # 2. Add a repair_check reasoning intent as a ready example for ONE tenant.
    # (knowledge_types.code has a global unique constraint, so insert once.)
    conn.execute(sa.text(
        "INSERT INTO knowledge_types (id, tenant_id, code, label, handler, intent_patterns, enabled, meta) "
        "SELECT gen_random_uuid(), t.id, 'repair_check', 'Чи ремонтуємо прилад', 'qa_handler', "
        "  '[\"ви ремонтуєте\", \"чи робите\", \"берете в ремонт\", \"можете полагодити\", \"ремонтуєте\"]'::jsonb, "
        "  true, jsonb_build_object('reasoning', cast(:reasoning as text)) "
        "FROM tenants t "
        "WHERE NOT EXISTS (SELECT 1 FROM knowledge_types k WHERE k.code = 'repair_check') "
        "ORDER BY t.created_at LIMIT 1"
    ), {"reasoning": REPAIR_REASONING})


def downgrade() -> None:
    conn = op.get_bind()
    conn.execute(sa.text("DELETE FROM knowledge_types WHERE code = 'repair_check'"))
