"""populate default meta templates

Revision ID: c3d6259b497c
Revises: c3d6259b497b
Create Date: 2026-06-12 15:15:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

# revision identifiers, used by Alembic.
revision: str = 'c3d6259b497c'
down_revision: Union[str, None] = 'c3d6259b497b'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Use raw SQL to merge the default templates into the JSONB meta field
    op.execute("""
    UPDATE bot_settings 
    SET meta = CASE WHEN meta IS NULL THEN '{}'::jsonb ELSE meta END || 
    jsonb_build_object(
        'tpl_business_header', '--- [БІЗНЕС ПРАВИЛА ТА ОБМЕЖЕННЯ] ---\nТи ПОВИНЕН неухильно дотримуватись наступних правил. Ніколи не порушуй їх:',
        'tpl_marketing_header', '--- [МАРКЕТИНГОВІ ПРОТОКОЛИ] ---\nЯкщо це доречно і природно звучить у контексті діалогу, спробуй застосувати ці настанови (ненав'язливо):',
        'tpl_escalation_header', '--- [ПОЛІТИКА ЕСКАЛАЦІЇ] ---\nЯкщо у тебе немає інформації для відповіді на питання клієнта, не вигадуй. Замість цього використай цю настанову своїми словами:',
        'tpl_qa_header', '--- [ТОЧНІ ФАКТИ (Q&A)] ---\nВикористай ці затверджені відповіді:',
        'tpl_rag_header', '--- [ДОКУМЕНТИ / БАЗА ЗНАНЬ] ---\nВикористай наступні уривки документів для формування відповіді:',
        'tpl_evaluation_rules', '--- [RULES FOR CONTEXT EVALUATION] ---\nCritically evaluate any data provided from the internet or knowledge base.\nIf the injected context does not contain the specific answer or technical specs needed to fulfill the user's request, YOU MUST state that the information is missing. DO NOT hallucinate missing details.',
        'tpl_footer', '--- [КІНЕЦЬ СИСТЕМНИХ ІНСТРУКЦІЙ] ---',
        'tpl_price_data', '
[Price List Data]:
',
        'tpl_web_search', '
[Web Search Results for ''{query}'']:
',
        'tpl_site_search', '
[Site Search Results ({url})]:
',
        'tpl_trusted_search', '
[Trusted Sites Data ({sites})]:
',
        'tpl_general_search', '
[General Web Search Results]:
',
        'tpl_escalate_instruction', '
[INSTRUCTION]: The user wants to speak with a human agent. Inform them that you are transferring the conversation to a live operator.'
    );
    """)


def downgrade() -> None:
    pass
