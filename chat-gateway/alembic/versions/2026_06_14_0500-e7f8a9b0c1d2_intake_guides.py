"""texno intake flow and common device symptom guides

Revision ID: e7f8a9b0c1d2
Revises: d6e7f8a9b0c1
Create Date: 2026-06-14 05:00:00.000000
"""
from typing import Sequence, Union
import json
import uuid

from alembic import op
import sqlalchemy as sa

from app.core.prompt_defaults import (
    DEFAULT_ANSWER_STYLE,
    DEFAULT_DECISION_RULES,
    DEFAULT_INTAKE_POLICY,
    ROUTE_PROMPTS,
)

revision: str = "e7f8a9b0c1d2"
down_revision: Union[str, None] = "d6e7f8a9b0c1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


INTAKE_GUIDES = [
    {
        "question": "Первинне уточнення: навушники або гарнітура",
        "variants": ["навушники bose", "навушники marshall", "tws навушники", "гарнітура не працює"],
        "answer": (
            "Якщо клієнт лише назвав навушники, бренд або модель, спочатку запитайте, що саме не працює: "
            "один чи обидва навушники, заряджання/кейс, швидкий розряд, тихий або спотворений звук, "
            "Bluetooth, мікрофон чи механічне пошкодження. TWS/вкладиші/накладні уточнюйте лише коли це "
            "впливає на наступне питання. Точна модель потрібна для підбору деталі або конкретної ціни, "
            "а не для першого повідомлення."
        ),
    },
    {
        "question": "Первинне уточнення: смартфон або смарт-гаджет",
        "variants": ["телефон зламався", "смартфон не працює", "айфон ремонт", "redmi ремонт"],
        "answer": (
            "Спочатку уточніть симптом: розбитий екран, не заряджається, не вмикається, швидко сідає, "
            "немає звуку, не працює камера/сенсор або було залиття. Після води одразу скажіть не вмикати "
            "і не заряджати. Модель потрібна, коли клієнт питає про дисплей, батарею, іншу запчастину, "
            "сумісність або ціну конкретного ремонту."
        ),
    },
    {
        "question": "Первинне уточнення: ноутбук, комп'ютер або планшет",
        "variants": ["ноутбук не працює", "комп'ютер зламався", "планшет ремонт", "макбук проблема"],
        "answer": (
            "Запитайте про симптом: не вмикається, немає зображення, не заряджається, перегрівається, "
            "шумить, повільно працює, пошкоджений екран/клавіатура або було залиття. Для чорного екрана "
            "корисно уточнити, чи світяться індикатори і чи чути запуск. Не називайте плату, живлення чи "
            "інший вузол причиною без діагностики. Модель потрібна для запчастини або точної сумісності."
        ),
    },
    {
        "question": "Первинне уточнення: колонка або акустика",
        "variants": ["колонка jbl", "marshall колонка", "акустика не працює", "bluetooth колонка ремонт"],
        "answer": (
            "Запитайте, що саме сталося: не вмикається, не заряджається, швидко сідає, хрипить, немає "
            "звуку, не підключається Bluetooth або пошкоджений роз'єм/корпус. Якщо була вода — уточніть "
            "це одразу. Модель потрібна переважно для акумулятора, динаміка, роз'єму чи конкретної ціни."
        ),
    },
    {
        "question": "Первинне уточнення: дрібна побутова техніка",
        "variants": ["чайник не вмикається", "блендер зламався", "мікрохвильовка ремонт", "мультиварка проблема"],
        "answer": (
            "Спочатку визначте тип приладу і симптом: не вмикається, не гріє, не крутиться, не вимикається, "
            "іскрить, пахне горілим, протікає або не працюють кнопки. Для чайника, який не вмикається, не "
            "називайте конкретну причину: без огляду це може бути різне. Запитайте, чи є індикація, запах "
            "горілого або сліди протікання. Модель потрібна лише для деталі чи точної оцінки."
        ),
    },
    {
        "question": "Первинне уточнення: телевізор, монітор або проектор",
        "variants": ["телевізор не працює", "монітор чорний екран", "проектор ремонт", "є звук немає картинки"],
        "answer": (
            "Уточніть симптом: не вмикається, є звук без зображення, темний екран, смуги/плями, немає "
            "звуку, не працює HDMI/USB або зависає Smart TV. Запитайте, чи горить або блимає індикатор. "
            "Не називайте підсвітку, блок живлення, T-CON чи матрицю встановленою причиною до діагностики. "
            "Модель і діагональ потрібні для запчастини або ціни."
        ),
    },
    {
        "question": "Первинне уточнення: зарядна станція або павербанк",
        "variants": ["зарядна станція не працює", "ecoflow ремонт", "павербанк не заряджається", "power station repair"],
        "answer": (
            "Запитайте про симптом: не вмикається, не заряджається, не дає 220 В/USB, швидко розряджається, "
            "показує помилку, гріється або була перевантажена/залита. Для станції корисно попросити код "
            "помилки, якщо він є. Не називайте BMS, інвертор або акумулятор причиною без перевірки. "
            "Модель потрібна для характеристик, сумісної деталі або оцінки ремонту."
        ),
    },
    {
        "question": "Первинне уточнення: кавоварка або кавомашина",
        "variants": ["кавомашина не працює", "кавоварка ремонт", "не подає каву", "протікає кавомашина"],
        "answer": (
            "Уточніть симптом: не вмикається, не гріє воду, не подає воду/каву, не меле, протікає, слабкий "
            "тиск, показує помилку або просить декальцинацію. Запитайте текст чи код помилки, якщо він є. "
            "Не називайте помпу, нагрівач або плату встановленою причиною без діагностики. Модель потрібна "
            "для деталі, інструкції помилки або конкретної ціни."
        ),
    },
    {
        "question": "Первинне уточнення: складання або апгрейд ПК",
        "variants": ["хочу зібрати пк", "апгрейд комп'ютера", "замінити відеокарту", "підібрати комплектуючі"],
        "answer": (
            "Спочатку запитайте мету: ігри, робота, монтаж, навчання чи тихий домашній ПК; потім бюджет і "
            "що вже є з комплектуючих. Для апгрейду уточніть поточні процесор, материнську плату, блок "
            "живлення, корпус і відеокарту лише в тій мірі, у якій це потрібно для сумісності. Не просіть "
            "фото всього ПК замість конкретних характеристик."
        ),
    },
    {
        "question": "Як відповідати, коли клієнт перепитує вигадану причину несправності?",
        "variants": ["блоком живлення", "чому ви вирішили що це плата", "звідки такий діагноз", "ви впевнені в причині"],
        "answer": (
            "Якщо попередня відповідь назвала деталь або причину без підтвердження, треба прямо виправитись: "
            "«Криво сказав. Без діагностики конкретну причину не визначити». Не повторюйте вигаданий вузол, "
            "не шукайте ціну для нього і не захищайте припущення. Поверніться до фактичного симптому."
        ),
    },
]


def upgrade() -> None:
    conn = op.get_bind()
    rows = conn.execute(sa.text(
        "SELECT id, tenant_id, meta, system_prompt FROM bot_settings"
    )).mappings().all()
    for row in rows:
        persona = str(row["system_prompt"] or "")
        if "Інженер Андрон" not in persona and "texno.plus" not in persona:
            continue
        meta = dict(row["meta"] or {})
        meta["agent_decision_rules"] = DEFAULT_DECISION_RULES
        meta["answer_style"] = DEFAULT_ANSWER_STYLE
        meta["intake_policy"] = DEFAULT_INTAKE_POLICY
        conn.execute(
            sa.text("UPDATE bot_settings SET meta = CAST(:meta AS jsonb) WHERE id = :id"),
            {"id": row["id"], "meta": json.dumps(meta, ensure_ascii=False)},
        )

        try:
            with open("/app/app/givi_system_prompt.md", "r", encoding="utf-8") as prompt_file:
                conn.execute(
                    sa.text("UPDATE bot_settings SET system_prompt = :persona WHERE id = :id"),
                    {"id": row["id"], "persona": prompt_file.read()},
                )
        except OSError:
            pass

        tenant_id = row["tenant_id"]
        route_rows = conn.execute(sa.text(
            "SELECT id, meta FROM knowledge_types WHERE tenant_id = :tenant_id"
        ), {"tenant_id": tenant_id}).mappings().all()
        for route in route_rows:
            route_meta = dict(route["meta"] or {})
            if str(route_meta.get("tool_name") or "") != "web_research":
                continue
            route_meta.update(ROUTE_PROMPTS["web_search"])
            conn.execute(sa.text(
                "UPDATE knowledge_types SET meta = CAST(:meta AS jsonb) WHERE id = :id"
            ), {"id": route["id"], "meta": json.dumps(route_meta, ensure_ascii=False)})

        for guide in INTAKE_GUIDES:
            exists = conn.execute(sa.text(
                "SELECT 1 FROM qa_pairs WHERE tenant_id = :tenant_id AND question = :question LIMIT 1"
            ), {"tenant_id": tenant_id, "question": guide["question"]}).first()
            if exists:
                conn.execute(sa.text(
                    "UPDATE qa_pairs SET answer = :answer, question_variants = CAST(:variants AS jsonb), "
                    "category = :category, enabled = true, meta = CAST(:meta AS jsonb) "
                    "WHERE tenant_id = :tenant_id AND question = :question"
                ), {
                    "tenant_id": tenant_id,
                    "question": guide["question"],
                    "answer": guide["answer"],
                    "variants": json.dumps(guide["variants"], ensure_ascii=False),
                    "category": "Первинне приймання та типові несправності",
                    "meta": json.dumps({"kind": "intake_guide", "source": "system_seed"}, ensure_ascii=False),
                })
            else:
                conn.execute(sa.text(
                    "INSERT INTO qa_pairs "
                    "(id, tenant_id, question, question_variants, answer, category, enabled, meta) "
                    "VALUES (:id, :tenant_id, :question, CAST(:variants AS jsonb), :answer, "
                    ":category, true, CAST(:meta AS jsonb))"
                ), {
                    "id": uuid.uuid4(),
                    "tenant_id": tenant_id,
                    "question": guide["question"],
                    "variants": json.dumps(guide["variants"], ensure_ascii=False),
                    "answer": guide["answer"],
                    "category": "Первинне приймання та типові несправності",
                    "meta": json.dumps({"kind": "intake_guide", "source": "system_seed"}, ensure_ascii=False),
                })


def downgrade() -> None:
    pass
