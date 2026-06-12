"""strict eval rules

Revision ID: d4e736a5088d
Revises: c3d6259b497c
Create Date: 2026-06-12 16:20:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

# revision identifiers, used by Alembic.
revision: str = 'd4e736a5088d'
down_revision: Union[str, None] = 'c3d6259b497c'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Use raw SQL to merge the strict evaluation rules into the JSONB meta field
    op.execute("""
    UPDATE bot_settings 
    SET meta = CASE WHEN meta IS NULL THEN '{}'::jsonb ELSE meta END || 
    jsonb_build_object(
        'tpl_evaluation_rules', '--- [АБСОЛЮТНЕ ПРАВИЛО: ЗАБОРОНА ГАЛЮЦИНАЦІЙ] ---\n1. Якщо питання стосується технічних характеристик, сумісності, наявності чи цін — використовуй ВИКЛЮЧНО дані з блоків вище (Web Search, Прайс, FAQ).\n2. Якщо в наданих текстах НЕМАЄ прямої відповіді — СУВОРО ЗАБОРОНЕНО вигадувати її з власної пам''яті.\n3. Якщо даних немає, дай відповідь у своєму стилі, щось на кшталт: "Не маю точних технічних даних по цьому залізу, треба дивитись по факту" або запропонуй клієнту надати точну модель.\n4. НІЯКИХ припущень щодо сумісності. Або 100% підтвердження в контексті, або ти не знаєш.'
    );
    """)


def downgrade() -> None:
    pass
