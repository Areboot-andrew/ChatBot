import logging
import yaml
from sqlalchemy.future import select
from app.database import async_session_maker
from app.models.auth import User
from app.models.tenant import Tenant
from app.admin.auth import hash_password
from app.config import settings

logger = logging.getLogger(__name__)

async def seed_admin():
    async with async_session_maker() as db:
        # Seed Admin
        result = await db.execute(select(User).where(User.username == "admin"))
        admin = result.scalars().first()
        
        if not admin:
            logger.info("Creating default admin user...")
            hashed_pw = hash_password(settings.ADMIN_DEFAULT_PASSWORD)
            new_admin = User(username="admin", hashed_password=hashed_pw)
            db.add(new_admin)
            await db.commit()
            logger.info("Default admin user created successfully.")

        # Seed First Tenant if empty
        res_t = await db.execute(select(Tenant))
        tenant = res_t.scalars().first()
        if not tenant:
            logger.info("Creating default tenant...")
            tenant = Tenant(name="Default Service", description="Main company")
            db.add(tenant)
            await db.commit()
            await db.refresh(tenant)

        # Seed Prices from YAML if empty
        from app.models.services import ServiceCategory, ServicePrice
        from app.models.knowledge import QaPair
        res_c = await db.execute(select(ServiceCategory).where(ServiceCategory.tenant_id == tenant.id))
        if not res_c.scalars().first():
            logger.info("Database is empty. Seeding test data from knowledge_template.yaml...")
            try:
                with open("/app/knowledge_template.yaml", "r", encoding="utf-8") as f:
                    data = yaml.safe_load(f)
                from app.core.texno_price_catalog import TEXNO_SERVICE_PRICES
                for cat_data in data.get("categories", []):
                    expanded = TEXNO_SERVICE_PRICES.get(cat_data.get("slug"))
                    if expanded:
                        cat_data["services"] = [
                            {"name": name, "price": price} for name, price in expanded
                        ]
                    
                for cat_data in data.get("categories", []):
                    cat = ServiceCategory(tenant_id=tenant.id, slug=cat_data["slug"])
                    db.add(cat)
                    cat.title = cat_data.get("title", "")
                    cat.description = cat_data.get("description", "")
                    cat.meta = {
                        "detailed_description": cat_data.get("detailed_description", ""),
                        "problems": cat_data.get("problems", [])
                    }
                    await db.commit()
                    await db.refresh(cat)
                    
                    for price_data in cat_data.get("services", []):
                        p = ServicePrice(
                            tenant_id=tenant.id,
                            category_id=cat.id,
                            name=price_data.get("name", ""),
                            price=str(price_data.get("price", ""))
                        )
                        db.add(p)
                        
                    for faq_data in cat_data.get("faqs", []):
                        q = QaPair(
                            tenant_id=tenant.id,
                            question=faq_data.get("question", ""),
                            answer=faq_data.get("answer", ""),
                            category=f"FAQ_{cat.slug}"
                        )
                        db.add(q)
                        
                for faq_data in data.get("global_faq", []):
                    q = QaPair(
                        tenant_id=tenant.id,
                        question=faq_data.get("question", ""),
                        answer=faq_data.get("answer", ""),
                        category="FAQ_Global"
                    )
                    db.add(q)
                    
                await db.commit()
                logger.info("Test data seeded successfully!")
            except Exception as e:
                logger.error(f"Failed to seed data: {e}")

        # Seed Intents (KnowledgeType) if empty
        from app.models.tenant import KnowledgeType, BotSetting
        res_kt = await db.execute(select(KnowledgeType).where(KnowledgeType.tenant_id == tenant.id))
        if not res_kt.scalars().first():
            logger.info("Seeding default intents...")
            from app.core.prompt_defaults import ROUTE_PROMPTS
            intents_data = [
                {"code": "catalog", "label": "Каталог: товари/послуги, ціни, описи",
                 "intent_patterns": ["чи є", "чи робите", "ремонтуєте", "скільки коштує", "ціна", "прайс"],
                 "prompt_key": "catalog"},
                {"code": "qa", "label": "Записи знань та документи",
                 "intent_patterns": ["гарантія", "умови", "як відбувається", "терміни", "правила"],
                 "prompt_key": "qa"},
                {"code": "web_search", "label": "Зовнішній веб-пошук",
                 "intent_patterns": ["характеристики", "сумісність", "що це", "яка модель", "специфікація"],
                 "prompt_key": "web_search"},
                {"code": "external_price", "label": "Зовнішні ціни та постачальники",
                 "intent_patterns": ["ціна деталі", "ціна комплектуючої", "у постачальників", "ринкова ціна", "наявність деталі"],
                 "prompt_key": "external_price"},
                {"code": "business_info", "label": "Бізнес-факти",
                 "intent_patterns": ["коли працюєте", "адреса", "телефон", "оплата", "доставка", "коли прийти"],
                 "prompt_key": "business_info"},
                {"code": "handoff", "label": "Передача оператору",
                 "intent_patterns": ["людина", "менеджер", "оператор", "скарга", "подзвонити"],
                 "prompt_key": "handoff"},
            ]
            for intent in intents_data:
                route_meta = dict(ROUTE_PROMPTS[intent["prompt_key"]])
                kt = KnowledgeType(
                    tenant_id=tenant.id,
                    code=intent["code"],
                    label=intent["label"],
                    handler=route_meta.get("tool_name") or "route",
                    intent_patterns=intent["intent_patterns"],
                    meta=route_meta,
                )
                db.add(kt)
            await db.commit()
            logger.info("Default intents seeded successfully.")

        # Update default system prompt
        res_s = await db.execute(select(BotSetting).where(BotSetting.tenant_id == tenant.id))
        settings_db = res_s.scalars().first()
        from app.core.prompt_defaults import DEFAULT_UNIVERSAL_PERSONA
        default_prompt = DEFAULT_UNIVERSAL_PERSONA

        if not settings_db:
            default_settings = BotSetting(
                tenant_id=tenant.id,
                system_prompt=default_prompt,
                llm_model="gemma-4",
                temperature="0.7",
                max_tokens="1024",
                meta={
                    "agent_max_iterations": "3",
                    # Temporary Serper (Google) key — replace with your own in Settings
                    "serper_api_key": "2d030163fbd463059411ab1c1f7ba67220a8510d",
                    "business_info": {},
                }
            )
            db.add(default_settings)
            await db.commit()
            logger.info("Default BotSettings created.")
        elif settings_db.system_prompt == "Ти корисний асистент.":
            settings_db.system_prompt = default_prompt
            await db.commit()
            logger.info("BotSettings system prompt updated to the universal default.")

        # Fill the control prompts into the DB for ALL tenants where empty, so the
        # panel shows them populated (and they are editable from there).
        await seed_default_prompts(db)


async def seed_default_prompts(db):
    """Populate only live universal settings. Route prompts live on routes."""
    from sqlalchemy.future import select as _select
    from sqlalchemy.orm.attributes import flag_modified
    from app.models.tenant import BotSetting
    from app.core.prompt_defaults import (
        LEAN_CONTROLLER_PROMPT, LEAN_ANSWER_PROMPT, LEAN_CONDUCT_PROMPT,
        LEAN_WARNING_PROMPT,
    )

    defaults = {
        "ban_message": "Вітаю, вас забанено.",
        "catalog_synonyms": "",
        "conduct_enabled": "1",
        "conduct_warnings": "2",
        "marketing_enabled": "0",
        "lean_controller_prompt": LEAN_CONTROLLER_PROMPT,
        "lean_answer_prompt": LEAN_ANSWER_PROMPT,
        "lean_conduct_prompt": LEAN_CONDUCT_PROMPT,
        "lean_warning_prompt": LEAN_WARNING_PROMPT,
    }
    legacy_keys = (
        "price_triggers", "capability_triggers", "business_info_triggers",
        "brand_words", "part_words", "agent_decision_rules", "answer_style",
        "intake_policy", "conduct_policy", "parts_instruction",
        "tpl_evaluation_rules", "web_research_mode", "parts_sales_mode",
        "external_part_price_mode", "fallback_sites", "tpl_escalate_instruction",
        "enabled_tools", "router_json_mode", "lean_query_prompt",
        "lean_validator_prompt", "tpl_business_header", "tpl_marketing_header",
        "tpl_escalation_header", "tpl_qa_header", "tpl_rag_header",
        "tpl_footer", "tpl_router_rules",
    )
    res = await db.execute(_select(BotSetting))
    changed = 0
    for s in res.scalars().all():
        meta = dict(s.meta or {})
        touched = False
        for k in legacy_keys:
            if k in meta:
                meta.pop(k, None)
                touched = True
        for k, v in defaults.items():
            if not (meta.get(k) or "").strip():
                meta[k] = v
                touched = True
        if touched:
            s.meta = meta
            flag_modified(s, "meta")
            changed += 1
    if changed:
        await db.commit()
        logger.info(f"Seeded default control prompts into {changed} tenant(s).")
