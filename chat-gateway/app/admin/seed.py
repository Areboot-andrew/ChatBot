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
                {"code": "catalog", "label": "Наш каталог: товари, послуги та ціни", "handler": "qa_handler",
                 "intent_patterns": ["чи є", "чи робите", "ремонтуєте", "скільки коштує", "ціна", "прайс"],
                 "prompt_key": "catalog"},
                {"code": "qa", "label": "Затверджені Q&A та документи", "handler": "qa_handler",
                 "intent_patterns": ["гарантія", "умови", "як відбувається", "терміни", "правила"],
                 "prompt_key": "qa"},
                {"code": "web_search", "label": "Зовнішні характеристики та ідентифікація", "handler": "web_search_handler",
                 "intent_patterns": ["характеристики", "сумісність", "що це", "яка модель", "специфікація"],
                 "prompt_key": "web_search"},
                {"code": "external_price", "label": "Зовнішні ціни та постачальники", "handler": "web_search_handler",
                 "intent_patterns": ["ціна деталі", "ціна комплектуючої", "у постачальників", "ринкова ціна", "наявність деталі"],
                 "prompt_key": "external_price"},
                {"code": "business_info", "label": "Графік, адреса, оплата та доставка", "handler": "qa_handler",
                 "intent_patterns": ["коли працюєте", "адреса", "телефон", "оплата", "доставка", "коли прийти"],
                 "prompt_key": "business_info"},
                {"code": "handoff", "label": "Передача оператору", "handler": "escalate",
                 "intent_patterns": ["людина", "менеджер", "оператор", "скарга", "подзвонити"],
                 "prompt_key": "handoff"},
            ]
            for intent in intents_data:
                route_meta = dict(ROUTE_PROMPTS[intent["prompt_key"]])
                kt = KnowledgeType(
                    tenant_id=tenant.id,
                    code=intent["code"],
                    label=intent["label"],
                    handler=intent["handler"],
                    intent_patterns=intent["intent_patterns"],
                    meta=route_meta,
                )
                db.add(kt)
            await db.commit()
            logger.info("Default intents seeded successfully.")

        # Update default system prompt
        res_s = await db.execute(select(BotSetting).where(BotSetting.tenant_id == tenant.id))
        settings_db = res_s.scalars().first()
        try:
            with open("/app/app/givi_system_prompt.md", "r", encoding="utf-8") as f:
                givi_prompt = f.read()
        except Exception as e:
            logger.error(f"Could not load givi_system_prompt.md: {e}")
            givi_prompt = "Ти корисний асистент."

        if not settings_db:
            default_settings = BotSetting(
                tenant_id=tenant.id,
                system_prompt=givi_prompt,
                llm_model="gemma-4",
                temperature="0.7",
                max_tokens="1024",
                meta={
                    "engine": "agent",
                    "agent_max_iterations": "5",
                    "enabled_tools": [],  # empty = all tools enabled
                    "fallback_sites": "texno.plus",  # check our own site first
                    # Temporary Serper (Google) key — replace with your own in Settings
                    "serper_api_key": "2d030163fbd463059411ab1c1f7ba67220a8510d",
                    "business_info": {
                        "phone": "066-170-12-82",
                        "hours": "Пн-Пт 10:00-19:00, Сб 10:00-15:00",
                        "payment": "картка, готівка, наложений платіж, крипта",
                        "delivery": "самовивіз з сервісу або відправка Новою Поштою",
                        "warranty": "1-6 місяців залежно від типу робіт та запчастини",
                        "extra": "Діагностика безкоштовна за умови ремонту в нас",
                    },
                }
            )
            db.add(default_settings)
            await db.commit()
            logger.info("Default BotSettings created.")
        elif settings_db.system_prompt == "Ти корисний асистент.":
            settings_db.system_prompt = givi_prompt
            await db.commit()
            logger.info("BotSettings system prompt updated to givi_system_prompt.md.")

        # Fill the control prompts into the DB for ALL tenants where empty, so the
        # panel shows them populated (and they are editable from there).
        await seed_default_prompts(db)


async def seed_default_prompts(db):
    """Populate editable control prompts (decision rules, answer style, parts
    instruction, synonyms) into bot_settings.meta where empty."""
    from sqlalchemy.future import select as _select
    from sqlalchemy.orm.attributes import flag_modified
    from app.models.tenant import BotSetting
    from app.core.agent import _CATALOG_SYNONYMS
    from app.core.prompt_defaults import (
        DEFAULT_DECISION_RULES, DEFAULT_ANSWER_STYLE, DEFAULT_PARTS_INSTRUCTION,
        DEFAULT_EVALUATION_RULES,
    )

    synonyms_text = "\n".join(f"{k}={','.join(v)}" for k, v in _CATALOG_SYNONYMS.items())
    defaults = {
        "agent_decision_rules": DEFAULT_DECISION_RULES,
        "answer_style": DEFAULT_ANSWER_STYLE,
        "parts_instruction": DEFAULT_PARTS_INSTRUCTION,
        "catalog_synonyms": synonyms_text,
        "tpl_evaluation_rules": DEFAULT_EVALUATION_RULES,
    }
    res = await db.execute(_select(BotSetting))
    changed = 0
    for s in res.scalars().all():
        meta = dict(s.meta or {})
        touched = False
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
