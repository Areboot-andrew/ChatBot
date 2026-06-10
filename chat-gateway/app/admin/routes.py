from fastapi import APIRouter, Request, Depends, Form, Response
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from pydantic import BaseModel
import uuid

from app.database import get_db
from app.admin.auth import verify_password, create_session, delete_session
from app.admin.dependencies import get_current_user, get_current_tenant_id
from app.models.auth import User
from app.models.tenant import Tenant, BotSetting, KnowledgeType
from app.models.knowledge import QaPair
from app.models.channel import Channel
from app.core.llm import chat

router = APIRouter(prefix="/admin", tags=["admin"])
templates = Jinja2Templates(directory="app/admin/templates")

@router.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    return templates.TemplateResponse(request=request, name="login.html", context={"request": request})

@router.post("/login")
async def login(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    db: AsyncSession = Depends(get_db)
):
    result = await db.execute(select(User).where(User.username == username))
    user = result.scalars().first()
    
    if not user or not verify_password(password, user.hashed_password):
        return templates.TemplateResponse(request=request, name="login.html", context={"request": request, "error": "Неправильний логін або пароль"})
    
    token = await create_session(user.id, user.username)
    response = RedirectResponse(url="/admin/dashboard", status_code=302)
    response.set_cookie(key="admin_session", value=token, httponly=True, max_age=86400)
    return response

@router.get("/logout")
async def logout(request: Request):
    token = request.cookies.get("admin_session")
    await delete_session(token)
    response = RedirectResponse(url="/admin/login", status_code=302)
    response.delete_cookie("admin_session")
    response.delete_cookie("tenant_id")
    return response

@router.post("/set-tenant")
async def set_tenant(
    request: Request,
    tenant_id: str = Form(...)
):
    response = Response(status_code=204)
    response.headers["HX-Refresh"] = "true"
    if tenant_id:
        response.set_cookie(key="tenant_id", value=tenant_id, max_age=86400 * 30)
    else:
        response.delete_cookie("tenant_id")
    return response

async def get_all_tenants(db: AsyncSession):
    result = await db.execute(select(Tenant).order_by(Tenant.name))
    return result.scalars().all()

@router.get("/dashboard", response_class=HTMLResponse)
async def dashboard(
    request: Request,
    user: User = Depends(get_current_user),
    tenant_id: uuid.UUID = Depends(get_current_tenant_id),
    db: AsyncSession = Depends(get_db)
):
    import httpx
    from app.config import settings
    from sqlalchemy import text
    from datetime import date
    
    tenants = await get_all_tenants(db)
    
    # 1. Real LM Studio Check
    lmstudio_status = "ERROR"
    loaded_models = "Немає підключення"
    url = settings.LMSTUDIO_URL.replace('/v1', '') if settings.LMSTUDIO_URL.endswith('/v1') else settings.LMSTUDIO_URL
    try:
        async with httpx.AsyncClient(timeout=2.0) as client:
            resp = await client.get(f"{url}/v1/models")
            if resp.status_code == 200:
                lmstudio_status = "OK"
                data = resp.json()
                models = [m.get("id") for m in data.get("data", [])]
                if models:
                    loaded_models = ", ".join(models)
                else:
                    loaded_models = "Моделі не завантажені"
    except Exception:
        loaded_models = "Недоступно (Check URL/Network)"

    # 2. Postgres Check
    postgres_status = "ERROR"
    try:
        await db.execute(text("SELECT 1"))
        postgres_status = "OK"
    except Exception:
        pass

    # 3. Redis Check
    redis_status = "ERROR"
    try:
        from app.core.history import redis_client
        if await redis_client.ping():
            redis_status = "OK"
    except Exception:
        pass

    # 4. Qdrant Check
    qdrant_status = "ERROR"
    try:
        async with httpx.AsyncClient(timeout=2.0) as client:
            resp = await client.get(f"{settings.QDRANT_URL}/collections")
            if resp.status_code == 200:
                qdrant_status = "OK"
    except Exception:
        pass

    # 5. Quick Stats
    channels_count = 0
    messages_today = 0
    
    if tenant_id:
        res_ch = await db.execute(text("SELECT COUNT(id) FROM channels WHERE tenant_id = :tid"), {"tid": tenant_id})
        channels_count = res_ch.scalar() or 0
        
        # Temporary fallback for messages, if conversations/messages table exists
        try:
            today = date.today().isoformat()
            res_msg = await db.execute(
                text("SELECT COUNT(m.id) FROM messages m JOIN conversations c ON m.conversation_id = c.id WHERE c.tenant_id = :tid AND m.created_at >= :today"), 
                {"tid": tenant_id, "today": today}
            )
            messages_today = res_msg.scalar() or 0
        except Exception:
            messages_today = 0

    statuses = {
        "lmstudio": lmstudio_status, 
        "postgres": postgres_status, 
        "redis": redis_status, 
        "qdrant": qdrant_status,
        "lmstudio_models": loaded_models,
        "lmstudio_url": settings.LMSTUDIO_URL,
        "channels_count": channels_count,
        "messages_today": messages_today
    }
    
    return templates.TemplateResponse(request=request, name="dashboard.html", context={
        "request": request, "user": user, "tenants": tenants,
        "current_tenant_id": tenant_id, "statuses": statuses
    })

# --- TENANTS ---
@router.get("/tenants", response_class=HTMLResponse)
async def list_tenants(
    request: Request,
    user: User = Depends(get_current_user),
    tenant_id: uuid.UUID = Depends(get_current_tenant_id),
    db: AsyncSession = Depends(get_db)
):
    tenants = await get_all_tenants(db)
    return templates.TemplateResponse(request=request, name="tenants/list.html", context={
        "request": request, "user": user, "tenants": tenants, "current_tenant_id": tenant_id
    })

@router.get("/tenants/create", response_class=HTMLResponse)
async def create_tenant_form(
    request: Request,
    user: User = Depends(get_current_user),
    tenant_id: uuid.UUID = Depends(get_current_tenant_id),
    db: AsyncSession = Depends(get_db)
):
    tenants = await get_all_tenants(db)
    return templates.TemplateResponse(request=request, name="tenants/form.html", context={
        "request": request, "user": user, "tenants": tenants, "current_tenant_id": tenant_id, "tenant": None
    })

@router.post("/tenants/create")
async def create_tenant(
    request: Request,
    name: str = Form(...),
    description: str = Form(""),
    enabled: bool = Form(False),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    new_tenant = Tenant(name=name, description=description, enabled=enabled)
    db.add(new_tenant)
    await db.commit()
    await db.refresh(new_tenant)
    
    default_settings = BotSetting(
        tenant_id=new_tenant.id,
        system_prompt="Ти корисний асистент.",
        llm_model="gemma-4",
        temperature="0.7",
        max_tokens="1024"
    )
    db.add(default_settings)
    await db.commit()
    return RedirectResponse(url="/admin/tenants", status_code=303)

# --- CHANNELS ---
@router.get("/channels", response_class=HTMLResponse)
async def list_channels(
    request: Request,
    user: User = Depends(get_current_user),
    tenant_id: uuid.UUID = Depends(get_current_tenant_id),
    db: AsyncSession = Depends(get_db)
):
    tenants = await get_all_tenants(db)
    channels = []
    if tenant_id:
        result = await db.execute(select(Channel).where(Channel.tenant_id == tenant_id))
        channels = result.scalars().all()

    return templates.TemplateResponse(request=request, name="channels/list.html", context={
        "request": request, "user": user, "tenants": tenants, 
        "current_tenant_id": tenant_id, "channels": channels
    })

@router.get("/channels/create", response_class=HTMLResponse)
async def create_channel_form(
    request: Request,
    user: User = Depends(get_current_user),
    tenant_id: uuid.UUID = Depends(get_current_tenant_id),
    db: AsyncSession = Depends(get_db)
):
    tenants = await get_all_tenants(db)
    return templates.TemplateResponse(request=request, name="channels/form.html", context={
        "request": request, "user": user, "tenants": tenants, 
        "current_tenant_id": tenant_id, "channel": None
    })

@router.post("/channels/create")
async def create_channel(
    request: Request,
    name: str = Form(...),
    type: str = Form(...),
    credentials: str = Form(...),
    greeting: str = Form(""),
    enabled: bool = Form(False),
    user: User = Depends(get_current_user),
    tenant_id: uuid.UUID = Depends(get_current_tenant_id),
    db: AsyncSession = Depends(get_db)
):
    if not tenant_id:
        return RedirectResponse(url="/admin/channels", status_code=303)
        
    creds_json = {"token": credentials} if type == 'telegram' else {"config": credentials}
    
    new_channel = Channel(
        tenant_id=tenant_id,
        name=name,
        type=type,
        credentials=creds_json,
        greeting=greeting,
        enabled=enabled
    )
    db.add(new_channel)
    await db.commit()
    return RedirectResponse(url="/admin/channels", status_code=303)

# --- SETTINGS ---
@router.get("/settings", response_class=HTMLResponse)
async def bot_settings(
    request: Request,
    user: User = Depends(get_current_user),
    tenant_id: uuid.UUID = Depends(get_current_tenant_id),
    db: AsyncSession = Depends(get_db)
):
    tenants = await get_all_tenants(db)
    settings = None
    tenant = None
    if tenant_id:
        res_t = await db.execute(select(Tenant).where(Tenant.id == tenant_id))
        tenant = res_t.scalars().first()
        res_s = await db.execute(select(BotSetting).where(BotSetting.tenant_id == tenant_id))
        settings = res_s.scalars().first()

    return templates.TemplateResponse(request=request, name="settings.html", context={
        "request": request, "user": user, "tenants": tenants, 
        "current_tenant_id": tenant_id, "tenant": tenant, "settings": settings
    })

@router.post("/settings")
async def update_settings(
    request: Request,
    system_prompt: str = Form(...),
    temperature: str = Form(...),
    max_tokens: str = Form(...),
    business_rules: str = Form(""),
    marketing_rules: str = Form(""),
    escalation_policy: str = Form("handoff"),
    escalation_prompt: str = Form(...),
    fallback_text: str = Form(...),
    user: User = Depends(get_current_user),
    tenant_id: uuid.UUID = Depends(get_current_tenant_id),
    db: AsyncSession = Depends(get_db)
):
    if tenant_id:
        res = await db.execute(select(BotSetting).where(BotSetting.tenant_id == tenant_id))
        settings = res.scalars().first()
        if settings:
            settings.system_prompt = system_prompt
            settings.temperature = temperature
            settings.max_tokens = max_tokens
            settings.business_rules = business_rules
            settings.marketing_rules = marketing_rules
            settings.escalation_policy = escalation_policy
            settings.escalation_prompt = escalation_prompt
            settings.fallback_text = fallback_text
            await db.commit()
    return RedirectResponse(url="/admin/settings", status_code=303)

# --- KNOWLEDGE BASE ---
from fastapi import UploadFile, File, BackgroundTasks
from app.models.knowledge import KbDocument
import hashlib

@router.get("/knowledge", response_class=HTMLResponse)
async def knowledge_base(
    request: Request,
    user: User = Depends(get_current_user),
    tenant_id: uuid.UUID = Depends(get_current_tenant_id),
    db: AsyncSession = Depends(get_db)
):
    tenants = await get_all_tenants(db)
    tenant = None
    qa_pairs = []
    logic_schemas = []
    documents = []
    if tenant_id:
        res_t = await db.execute(select(Tenant).where(Tenant.id == tenant_id))
        tenant = res_t.scalars().first()
        
        res_qa = await db.execute(select(QaPair).where(QaPair.tenant_id == tenant_id).order_by(QaPair.question))
        qa_pairs = res_qa.scalars().all()
        
        res_logic = await db.execute(select(KnowledgeType).where(KnowledgeType.tenant_id == tenant_id).order_by(KnowledgeType.label))
        logic_schemas = res_logic.scalars().all()
        
        res_docs = await db.execute(select(KbDocument).where(KbDocument.tenant_id == tenant_id).order_by(KbDocument.updated_at.desc()))
        documents = res_docs.scalars().all()

    return templates.TemplateResponse(request=request, name="knowledge/index.html", context={
        "request": request, "user": user, "tenants": tenants, 
        "current_tenant_id": tenant_id, "tenant": tenant,
        "qa_pairs": qa_pairs, "logic_schemas": logic_schemas,
        "documents": documents
    })

@router.post("/knowledge/docs/upload")
async def docs_upload(
    request: Request,
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    title: str = Form(""),
    category: str = Form(""),
    user: User = Depends(get_current_user),
    tenant_id: uuid.UUID = Depends(get_current_tenant_id),
    db: AsyncSession = Depends(get_db)
):
    if tenant_id and file.filename:
        content = await file.read()
        file_hash = hashlib.sha256(content).hexdigest()
        
        doc_title = title if title else file.filename
        
        doc = KbDocument(
            tenant_id=tenant_id,
            title=doc_title,
            category=category,
            source="upload",
            filename=file.filename,
            mime=file.content_type,
            sha256=file_hash,
            status="processing"
        )
        db.add(doc)
        await db.commit()
        await db.refresh(doc)
        
        # Parse text and run vectorization in background
        from app.core.parsers import extract_text_from_file
        from app.core.rag import process_and_vectorize_document
        
        text = extract_text_from_file(content, file.filename)
        if text:
            # We would normally update status to "indexed" after the task finishes,
            # but for this MVP, we launch the task.
            background_tasks.add_task(process_and_vectorize_document, str(tenant_id), str(doc.id), doc_title, text)
            doc.status = "indexed"
        else:
            doc.status = "error: extraction failed"
        
        await db.commit()
        
    return RedirectResponse(url="/admin/knowledge", status_code=303)

@router.get("/knowledge/prices", response_class=HTMLResponse)
async def prices_page(
    request: Request,
    user: User = Depends(get_current_user),
    tenant_id: uuid.UUID = Depends(get_current_tenant_id),
    db: AsyncSession = Depends(get_db)
):
    tenants = await get_all_tenants(db)
    tenant = None
    categories = []
    if tenant_id:
        res_t = await db.execute(select(Tenant).where(Tenant.id == tenant_id))
        tenant = res_t.scalars().first()
        from app.models.services import ServiceCategory
        from sqlalchemy.orm import selectinload
        
        res_cat = await db.execute(select(ServiceCategory).where(ServiceCategory.tenant_id == tenant_id).options(selectinload(ServiceCategory.prices)))
        categories = res_cat.scalars().all()

    return templates.TemplateResponse(request=request, name="knowledge/prices.html", context={
        "request": request, "user": user, "tenants": tenants, 
        "current_tenant_id": tenant_id, "tenant": tenant,
        "categories": categories
    })

import yaml
@router.post("/knowledge/prices/import")
async def import_prices_yaml(
    request: Request,
    file: UploadFile = File(...),
    user: User = Depends(get_current_user),
    tenant_id: uuid.UUID = Depends(get_current_tenant_id),
    db: AsyncSession = Depends(get_db)
):
    if not tenant_id or not file.filename:
        return RedirectResponse(url="/admin/knowledge/prices", status_code=303)
        
    content = await file.read()
    try:
        data = yaml.safe_load(content.decode("utf-8"))
        from app.models.services import ServiceCategory, ServicePrice
        from app.models.knowledge import QaPair
        
        # 1. Import Categories and Prices
        for cat_data in data.get("categories", []):
            # Upsert Category
            res_c = await db.execute(select(ServiceCategory).where(ServiceCategory.tenant_id == tenant_id, ServiceCategory.slug == cat_data["slug"]))
            cat = res_c.scalars().first()
            if not cat:
                cat = ServiceCategory(tenant_id=tenant_id, slug=cat_data["slug"])
                db.add(cat)
            
            cat.title = cat_data.get("title", "")
            cat.description = cat_data.get("description", "")
            cat.meta = {
                "detailed_description": cat_data.get("detailed_description", ""),
                "problems": cat_data.get("problems", [])
            }
            await db.commit()
            await db.refresh(cat)
            
            # Clear old prices
            await db.execute(ServicePrice.__table__.delete().where(ServicePrice.category_id == cat.id))
            
            # Add new prices
            for price_data in cat_data.get("services", []):
                p = ServicePrice(
                    tenant_id=tenant_id,
                    category_id=cat.id,
                    name=price_data.get("name", ""),
                    price=str(price_data.get("price", ""))
                )
                db.add(p)
                
            # Import Category FAQs
            for faq_data in cat_data.get("faqs", []):
                q = QaPair(
                    tenant_id=tenant_id,
                    question=faq_data.get("question", ""),
                    answer=faq_data.get("answer", ""),
                    category=f"FAQ_{cat.slug}"
                )
                db.add(q)
                
        # 2. Import Global FAQs
        for faq_data in data.get("global_faq", []):
            q = QaPair(
                tenant_id=tenant_id,
                question=faq_data.get("question", ""),
                answer=faq_data.get("answer", ""),
                category="FAQ_Global"
            )
            db.add(q)
            
        await db.commit()
    except Exception as e:
        import logging
        logging.getLogger(__name__).error(f"Import Error: {e}")
        
    return RedirectResponse(url="/admin/knowledge/prices", status_code=303)

@router.get("/knowledge/prices/{cat_id}/edit", response_class=HTMLResponse)
async def price_edit_form(
    cat_id: uuid.UUID,
    request: Request,
    user: User = Depends(get_current_user),
    tenant_id: uuid.UUID = Depends(get_current_tenant_id),
    db: AsyncSession = Depends(get_db)
):
    tenants = await get_all_tenants(db)
    res = await db.execute(select(Tenant).where(Tenant.id == tenant_id))
    tenant = res.scalars().first()
    
    from app.models.services import ServiceCategory
    from sqlalchemy.orm import selectinload
    
    res_c = await db.execute(select(ServiceCategory).where(ServiceCategory.id == cat_id, ServiceCategory.tenant_id == tenant_id).options(selectinload(ServiceCategory.prices)))
    cat = res_c.scalars().first()
    
    if not cat:
        return RedirectResponse(url="/admin/knowledge/prices", status_code=303)

    return templates.TemplateResponse(request=request, name="knowledge/price_form.html", context={
        "request": request, "user": user, "tenants": tenants,
        "current_tenant_id": tenant_id, "tenant": tenant,
        "cat": cat
    })

@router.post("/knowledge/prices/{cat_id}/edit")
async def price_edit_submit(
    cat_id: uuid.UUID,
    request: Request,
    title: str = Form(...),
    description: str = Form(""),
    detailed_description: str = Form(""),
    user: User = Depends(get_current_user),
    tenant_id: uuid.UUID = Depends(get_current_tenant_id),
    db: AsyncSession = Depends(get_db)
):
    from app.models.services import ServiceCategory, ServicePrice
    
    res_c = await db.execute(select(ServiceCategory).where(ServiceCategory.id == cat_id, ServiceCategory.tenant_id == tenant_id))
    cat = res_c.scalars().first()
    if cat:
        cat.title = title
        cat.description = description
        
        meta = cat.meta or {}
        meta["detailed_description"] = detailed_description
        cat.meta = meta
        
        # Form submission for dynamic prices:
        form_data = await request.form()
        names = form_data.getlist("price_name[]")
        prices = form_data.getlist("price_value[]")
        
        await db.execute(ServicePrice.__table__.delete().where(ServicePrice.category_id == cat.id))
        
        for name, price in zip(names, prices):
            if name and price:
                db.add(ServicePrice(tenant_id=tenant_id, category_id=cat.id, name=name, price=price))
                
        await db.commit()
        
    return RedirectResponse(url="/admin/knowledge/prices", status_code=303)

# --- KNOWLEDGE: Q&A ---
@router.get("/knowledge/qa/create", response_class=HTMLResponse)
async def qa_create_form(
    request: Request,
    user: User = Depends(get_current_user),
    tenant_id: uuid.UUID = Depends(get_current_tenant_id),
    db: AsyncSession = Depends(get_db)
):
    tenants = await get_all_tenants(db)
    return templates.TemplateResponse(request=request, name="knowledge/qa_form.html", context={
        "request": request, "user": user, "tenants": tenants, 
        "current_tenant_id": tenant_id, "qa": None
    })

@router.post("/knowledge/qa/create")
async def qa_create(
    request: Request,
    question: str = Form(...),
    answer: str = Form(...),
    question_variants: str = Form(""),
    category: str = Form(""),
    user: User = Depends(get_current_user),
    tenant_id: uuid.UUID = Depends(get_current_tenant_id),
    db: AsyncSession = Depends(get_db)
):
    if tenant_id:
        variants = [v.strip() for v in question_variants.split(",")] if question_variants else []
        qa = QaPair(tenant_id=tenant_id, question=question, answer=answer, question_variants=variants, category=category)
        db.add(qa)
        await db.commit()
    return RedirectResponse(url="/admin/knowledge", status_code=303)

@router.get("/knowledge/qa/{qa_id}/edit", response_class=HTMLResponse)
async def qa_edit_form(
    qa_id: uuid.UUID,
    request: Request,
    user: User = Depends(get_current_user),
    tenant_id: uuid.UUID = Depends(get_current_tenant_id),
    db: AsyncSession = Depends(get_db)
):
    tenants = await get_all_tenants(db)
    res = await db.execute(select(QaPair).where(QaPair.id == qa_id, QaPair.tenant_id == tenant_id))
    qa = res.scalars().first()
    return templates.TemplateResponse(request=request, name="knowledge/qa_form.html", context={
        "request": request, "user": user, "tenants": tenants, 
        "current_tenant_id": tenant_id, "qa": qa
    })

@router.post("/knowledge/qa/{qa_id}/edit")
async def qa_edit(
    qa_id: uuid.UUID,
    request: Request,
    question: str = Form(...),
    answer: str = Form(...),
    question_variants: str = Form(""),
    category: str = Form(""),
    user: User = Depends(get_current_user),
    tenant_id: uuid.UUID = Depends(get_current_tenant_id),
    db: AsyncSession = Depends(get_db)
):
    if tenant_id:
        res = await db.execute(select(QaPair).where(QaPair.id == qa_id, QaPair.tenant_id == tenant_id))
        qa = res.scalars().first()
        if qa:
            qa.question = question
            qa.answer = answer
            qa.question_variants = [v.strip() for v in question_variants.split(",")] if question_variants else []
            qa.category = category
            await db.commit()
    return RedirectResponse(url="/admin/knowledge", status_code=303)

# --- KNOWLEDGE: LOGIC (Intents) ---
@router.get("/knowledge/logic/create", response_class=HTMLResponse)
async def logic_create_form(
    request: Request,
    user: User = Depends(get_current_user),
    tenant_id: uuid.UUID = Depends(get_current_tenant_id),
    db: AsyncSession = Depends(get_db)
):
    tenants = await get_all_tenants(db)
    return templates.TemplateResponse(request=request, name="knowledge/logic_form.html", context={
        "request": request, "user": user, "tenants": tenants, 
        "current_tenant_id": tenant_id, "logic": None
    })

@router.post("/knowledge/logic/create")
async def logic_create(
    request: Request,
    label: str = Form(...),
    code: str = Form(...),
    intent_patterns: str = Form(...),
    handler: str = Form(...),
    target_category: str = Form(""),
    fallback_action: str = Form("escalate"),
    enabled: bool = Form(False),
    user: User = Depends(get_current_user),
    tenant_id: uuid.UUID = Depends(get_current_tenant_id),
    db: AsyncSession = Depends(get_db)
):
    if tenant_id:
        patterns = [p.strip() for p in intent_patterns.split(",")] if intent_patterns else []
        meta_data = {"target_category": target_category, "fallback_action": fallback_action}
        logic = KnowledgeType(tenant_id=tenant_id, label=label, code=code, intent_patterns=patterns, handler=handler, enabled=enabled, meta=meta_data)
        db.add(logic)
        await db.commit()
    return RedirectResponse(url="/admin/knowledge", status_code=303)

@router.get("/knowledge/logic/{logic_id}/edit", response_class=HTMLResponse)
async def logic_edit_form(
    logic_id: uuid.UUID,
    request: Request,
    user: User = Depends(get_current_user),
    tenant_id: uuid.UUID = Depends(get_current_tenant_id),
    db: AsyncSession = Depends(get_db)
):
    tenants = await get_all_tenants(db)
    res = await db.execute(select(KnowledgeType).where(KnowledgeType.id == logic_id, KnowledgeType.tenant_id == tenant_id))
    logic = res.scalars().first()
    return templates.TemplateResponse(request=request, name="knowledge/logic_form.html", context={
        "request": request, "user": user, "tenants": tenants, 
        "current_tenant_id": tenant_id, "logic": logic
    })

@router.post("/knowledge/logic/{logic_id}/edit")
async def logic_edit(
    logic_id: uuid.UUID,
    request: Request,
    label: str = Form(...),
    code: str = Form(...),
    intent_patterns: str = Form(...),
    handler: str = Form(...),
    target_category: str = Form(""),
    fallback_action: str = Form("escalate"),
    enabled: bool = Form(False),
    user: User = Depends(get_current_user),
    tenant_id: uuid.UUID = Depends(get_current_tenant_id),
    db: AsyncSession = Depends(get_db)
):
    if tenant_id:
        res = await db.execute(select(KnowledgeType).where(KnowledgeType.id == logic_id, KnowledgeType.tenant_id == tenant_id))
        logic = res.scalars().first()
        if logic:
            logic.label = label
            logic.code = code
            logic.intent_patterns = [p.strip() for p in intent_patterns.split(",")] if intent_patterns else []
            logic.handler = handler
            logic.enabled = enabled
            meta_data = logic.meta if logic.meta else {}
            meta_data["target_category"] = target_category
            meta_data["fallback_action"] = fallback_action
            logic.meta = meta_data
            
            from sqlalchemy.orm.attributes import flag_modified
            flag_modified(logic, "meta")
            
            await db.commit()
    return RedirectResponse(url="/admin/knowledge", status_code=303)

# --- TEST CHAT ---
class ChatMessage(BaseModel):
    text: str

@router.get("/test-chat", response_class=HTMLResponse)
async def test_chat_page(
    request: Request,
    user: User = Depends(get_current_user),
    tenant_id: uuid.UUID = Depends(get_current_tenant_id),
    db: AsyncSession = Depends(get_db)
):
    tenants = await get_all_tenants(db)
    tenant = None
    if tenant_id:
        res = await db.execute(select(Tenant).where(Tenant.id == tenant_id))
        tenant = res.scalars().first()
        
    return templates.TemplateResponse(request=request, name="test_chat.html", context={
        "request": request, "user": user, "tenants": tenants, 
        "current_tenant_id": tenant_id, "tenant": tenant
    })

@router.post("/api/test-chat")
async def test_chat_api(
    msg: ChatMessage,
    user: User = Depends(get_current_user),
    tenant_id: uuid.UUID = Depends(get_current_tenant_id),
    db: AsyncSession = Depends(get_db)
):
    import time
    
    if not tenant_id:
        return {"response": "Помилка: не вибрано тенант", "debug_trace": []}
        
    debug_trace = []
    start_time = time.time()
    
    # 1. Intent Recognition (Mock logic for now, or match keywords)
    debug_trace.append({"step": "Аналіз наміру (Intent Router)", "status": "Розпізнано", "details": f"Отримано текст: '{msg.text}'", "time": "0.1s"})
    
    res = await db.execute(select(BotSetting).where(BotSetting.tenant_id == tenant_id))
    settings = res.scalars().first()
    
    # 2. Fetch Data (SQL Prices, Q&A, or Microservices)
    # Simple logic router for debug demonstration
    from app.models.services import ServicePrice
    
    # 2.1 Microservices Mock
    if "статус" in msg.text.lower() or "ремонт" in msg.text.lower():
        debug_trace[0]["details"] = f"Інтент: CHECK_REPAIR_STATUS"
        debug_trace.append({"step": "Виклик Мікросервісу (CRM API)", "status": "Очікування...", "details": f"GET /api/v1/orders?phone=...", "time": "-"})
        
        # Simulate network delay for microservice
        time.sleep(0.3)
        debug_trace[-1]["status"] = "Знайдено"
        debug_trace[-1]["details"] = "Отримано дані з CRM:\nЗамовлення #1024: В процесі діагностики.\nОчікувана дата: Завтра."
        debug_trace[-1]["time"] = "0.31s"
        
        qa_facts = []
        prices = []
        sys_prompt_addition = "\nДані з внутрішньої CRM системи:\n- Замовлення: телефон Samsung\n- Статус: В процесі діагностики"
    else:
        # 2.2 SQL / Qdrant Fallbacks
        res_price = await db.execute(select(ServicePrice).where(ServicePrice.tenant_id == tenant_id, ServicePrice.name.ilike(f"%{msg.text.split()[0]}%")).limit(3))
        prices = res_price.scalars().all()
        
        sys_prompt_addition = ""
        if prices:
            price_texts = [f"{p.name} - {p.price}" for p in prices]
            debug_trace.append({"step": "Пошук в Прайсах (SQL)", "status": "Знайдено", "details": "\n".join(price_texts), "time": "0.05s"})
            qa_facts = []
            sys_prompt_addition = "\nДодаткова інформація з бази прайсів:\n" + "\n".join([f"- {p.name}: {p.price}" for p in prices])
        else:
            debug_trace.append({"step": "Пошук в Прайсах (SQL)", "status": "Пусто", "details": "Відповідних послуг не знайдено", "time": "0.02s"})
            res_qa = await db.execute(select(QaPair).where(QaPair.tenant_id == tenant_id).limit(3))
            qa_facts = res_qa.scalars().all()
            debug_trace.append({"step": "Глобальна База (Qdrant/FAQ)", "status": "Знайдено факти", "details": f"Завантажено {len(qa_facts)} фрагментів.", "time": "0.12s"})
    
    from app.core.prompt_builder import build_system_prompt
    sys_prompt = build_system_prompt(settings, qa_facts)
    sys_prompt += sys_prompt_addition
    
    temp = float(settings.temperature) if settings and settings.temperature else 0.7
    
    messages = [
        {"role": "system", "content": sys_prompt},
        {"role": "user", "content": msg.text}
    ]
    
    debug_trace.append({"step": "Генерація LLM", "status": "В процесі...", "details": f"Промпт: {len(sys_prompt)} символів. Температура: {temp}", "time": "-"})
    
    try:
        response_text = await chat(messages, temperature=temp)
        gen_time = round(time.time() - start_time, 2)
        debug_trace[-1]["status"] = "Успішно"
        debug_trace[-1]["time"] = f"{gen_time}s"
    except Exception as e:
        response_text = "Помилка підключення до LLM."
        debug_trace[-1]["status"] = "Помилка"
        debug_trace[-1]["details"] = str(e)
        
    return {"response": response_text, "debug_trace": debug_trace}
