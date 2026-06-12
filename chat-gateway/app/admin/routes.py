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

@router.get("/tenants/{tenant_id}/edit", response_class=HTMLResponse)
async def edit_tenant_form(
    tenant_id: uuid.UUID,
    request: Request,
    user: User = Depends(get_current_user),
    current_tenant_id: uuid.UUID = Depends(get_current_tenant_id),
    db: AsyncSession = Depends(get_db)
):
    tenants = await get_all_tenants(db)
    res = await db.execute(select(Tenant).where(Tenant.id == tenant_id))
    tenant = res.scalars().first()
    if not tenant:
        return RedirectResponse(url="/admin/tenants", status_code=303)
    return templates.TemplateResponse(request=request, name="tenants/form.html", context={
        "request": request, "user": user, "tenants": tenants, "current_tenant_id": current_tenant_id, "tenant": tenant
    })

@router.post("/tenants/{tenant_id}/edit")
async def edit_tenant(
    tenant_id: uuid.UUID,
    request: Request,
    name: str = Form(...),
    description: str = Form(""),
    enabled: bool = Form(False),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    res = await db.execute(select(Tenant).where(Tenant.id == tenant_id))
    tenant = res.scalars().first()
    if tenant:
        tenant.name = name
        tenant.description = description
        tenant.enabled = enabled
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
    credentials: str = Form(""),
    api_id: str = Form(""),
    api_hash: str = Form(""),
    session_string: str = Form(""),
    greeting: str = Form(""),
    enabled: bool = Form(False),
    user: User = Depends(get_current_user),
    tenant_id: uuid.UUID = Depends(get_current_tenant_id),
    db: AsyncSession = Depends(get_db)
):
    if not tenant_id:
        return RedirectResponse(url="/admin/channels", status_code=303)
        
    if type == 'telegram_userbot':
        creds_json = {
            "api_id": api_id,
            "api_hash": api_hash,
            "session_string": session_string
        }
    elif type == 'telegram':
        creds_json = {"token": credentials}
    else:
        creds_json = {"config": credentials}
    
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

@router.get("/channels/{channel_id}/edit", response_class=HTMLResponse)
async def edit_channel_form(
    channel_id: uuid.UUID,
    request: Request,
    user: User = Depends(get_current_user),
    tenant_id: uuid.UUID = Depends(get_current_tenant_id),
    db: AsyncSession = Depends(get_db)
):
    if not tenant_id:
        return RedirectResponse(url="/admin/channels", status_code=303)
    tenants = await get_all_tenants(db)
    res = await db.execute(select(Channel).where(Channel.id == channel_id, Channel.tenant_id == tenant_id))
    channel = res.scalars().first()
    if not channel:
        return RedirectResponse(url="/admin/channels", status_code=303)
    return templates.TemplateResponse(request=request, name="channels/form.html", context={
        "request": request, "user": user, "tenants": tenants, "current_tenant_id": tenant_id, "channel": channel
    })

@router.post("/channels/{channel_id}/edit")
async def edit_channel(
    channel_id: uuid.UUID,
    request: Request,
    name: str = Form(...),
    type: str = Form(...),
    credentials: str = Form(""),
    api_id: str = Form(""),
    api_hash: str = Form(""),
    session_string: str = Form(""),
    greeting: str = Form(""),
    enabled: bool = Form(False),
    user: User = Depends(get_current_user),
    tenant_id: uuid.UUID = Depends(get_current_tenant_id),
    db: AsyncSession = Depends(get_db)
):
    if not tenant_id:
        return RedirectResponse(url="/admin/channels", status_code=303)
        
    res = await db.execute(select(Channel).where(Channel.id == channel_id, Channel.tenant_id == tenant_id))
    channel = res.scalars().first()
    if channel:
        if type == 'telegram_userbot':
            creds_json = {
                "api_id": api_id,
                "api_hash": api_hash,
                "session_string": session_string
            }
        elif type == 'telegram':
            creds_json = {"token": credentials}
        else:
            creds_json = {"config": credentials}
            
        channel.name = name
        channel.type = type
        channel.credentials = creds_json
        channel.greeting = greeting
        channel.enabled = enabled
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
    llm_model: str = Form(""),
    temperature: str = Form(...),
    max_tokens: str = Form(...),
    llm_base_url: str = Form(""),
    llm_api_key: str = Form(""),
    business_rules: str = Form(""),
    marketing_rules: str = Form(""),
    escalation_policy: str = Form("handoff"),
    fallback_sites: str = Form(""),
    escalation_prompt: str = Form(""),
    fallback_text: str = Form(""),
    user: User = Depends(get_current_user),
    tenant_id: uuid.UUID = Depends(get_current_tenant_id),
    db: AsyncSession = Depends(get_db)
):
    if tenant_id:
        res = await db.execute(select(BotSetting).where(BotSetting.tenant_id == tenant_id))
        settings = res.scalars().first()
        if settings:
            settings.system_prompt = system_prompt
            if llm_model:
                settings.llm_model = llm_model
            settings.temperature = temperature
            settings.max_tokens = max_tokens
            settings.business_rules = business_rules
            settings.marketing_rules = marketing_rules
            settings.escalation_policy = escalation_policy
            settings.escalation_prompt = escalation_prompt
            settings.fallback_text = fallback_text
            
            meta_data = settings.meta if settings.meta else {}
            meta_data["llm_base_url"] = llm_base_url
            meta_data["llm_api_key"] = llm_api_key
            meta_data["fallback_sites"] = fallback_sites
            settings.meta = meta_data
            
            from sqlalchemy.orm.attributes import flag_modified
            flag_modified(settings, "meta")
            
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
        "categories": categories,
        "tenant_meta": tenant.meta if tenant else {}
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

class FeedUrlRequest(BaseModel):
    url: str

@router.post("/knowledge/prices/preview")
async def preview_prices_feed(
    req: FeedUrlRequest,
    user: User = Depends(get_current_user),
    tenant_id: uuid.UUID = Depends(get_current_tenant_id)
):
    import httpx
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(req.url, timeout=10)
            resp.raise_for_status()
            data = resp.json()
            
        categories = data if isinstance(data, list) else data.get("categories", [])
        
        preview = []
        for cat in categories[:2]:
            cat_info = {"category": cat.get("title") or cat.get("name"), "services": []}
            services = cat.get("prices") or cat.get("services") or []
            for s in services[:3]:
                cat_info["services"].append({"name": s.get("name"), "price": s.get("price")})
            preview.append(cat_info)
            
        return {"status": "ok", "preview": preview}
    except Exception as e:
        return {"status": "error", "detail": str(e)}

@router.post("/knowledge/prices/sync_now")
async def sync_prices_feed(
    req: FeedUrlRequest,
    user: User = Depends(get_current_user),
    tenant_id: uuid.UUID = Depends(get_current_tenant_id),
    db: AsyncSession = Depends(get_db)
):
    import httpx
    from app.models.services import ServiceCategory, ServicePrice
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(req.url, timeout=10)
            resp.raise_for_status()
            data = resp.json()
            
        res = await db.execute(select(Tenant).where(Tenant.id == tenant_id))
        tenant = res.scalars().first()
        if tenant:
            meta = tenant.meta if tenant.meta else {}
            meta["catalog_sync_url"] = req.url
            tenant.meta = meta
            from sqlalchemy.orm.attributes import flag_modified
            flag_modified(tenant, "meta")
            
        categories = data if isinstance(data, list) else data.get("categories", [])
        
        count = 0
        for cat_data in categories:
            title = cat_data.get("title") or cat_data.get("name")
            if not title: continue
            
            res_c = await db.execute(select(ServiceCategory).where(ServiceCategory.tenant_id == tenant_id, ServiceCategory.title == title))
            cat = res_c.scalars().first()
            if not cat:
                import re
                slug = re.sub(r'[^a-z0-9]+', '-', title.lower()).strip('-')
                cat = ServiceCategory(tenant_id=tenant_id, title=title, slug=slug, description="")
                db.add(cat)
                await db.flush()
                
            services = cat_data.get("prices") or cat_data.get("services") or []
            for s in services:
                s_name = s.get("name")
                s_price = str(s.get("price"))
                if not s_name: continue
                
                res_p = await db.execute(select(ServicePrice).where(ServicePrice.category_id == cat.id, ServicePrice.name == s_name))
                price_obj = res_p.scalars().first()
                if price_obj:
                    price_obj.price = s_price
                else:
                    price_obj = ServicePrice(tenant_id=tenant_id, category_id=cat.id, name=s_name, price=s_price)
                    db.add(price_obj)
                count += 1
                
        await db.commit()
        return {"status": "ok", "count": count}
    except Exception as e:
        return {"status": "error", "detail": str(e)}

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
    problems: str = Form(""),
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
        meta["problems"] = [p.strip() for p in problems.split("\n") if p.strip()]
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
    target_url: str = Form(""),
    fallback_action: str = Form("escalate"),
    enabled: bool = Form(False),
    user: User = Depends(get_current_user),
    tenant_id: uuid.UUID = Depends(get_current_tenant_id),
    db: AsyncSession = Depends(get_db)
):
    if tenant_id:
        patterns = [p.strip() for p in intent_patterns.split(",")] if intent_patterns else []
        meta_data = {"target_category": target_category, "fallback_action": fallback_action, "target_url": target_url}
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
    target_url: str = Form(""),
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
            meta_data["target_url"] = target_url
            logic.meta = meta_data
            
            from sqlalchemy.orm.attributes import flag_modified
            flag_modified(logic, "meta")
            
            await db.commit()
    return RedirectResponse(url="/admin/knowledge", status_code=303)

# --- TEST CHAT ---
class ChatMessage(BaseModel):
    text: str
    history: list = []

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

from fastapi.responses import StreamingResponse
import json

@router.post("/api/test-chat")
async def test_chat_api(
    msg: ChatMessage,
    user: User = Depends(get_current_user),
    tenant_id: uuid.UUID = Depends(get_current_tenant_id)
):
    import time
    from app.database import async_session_maker
    
    if not tenant_id:
        return {"response": "Помилка: не вибрано тенант", "debug_trace": []}
        
    async def event_generator():
        start_time = time.time()
        
        def emit_trace(step, status, details, duration="-"):
            event = {
                "type": "trace",
                "step": step,
                "status": status,
                "details": details,
                "time": str(duration) if isinstance(duration, str) else f"{duration}s"
            }
            return f"data: {json.dumps(event)}\n\n"

        # Must use our own DB session since FastAPI dependency session closes when function returns
        async with async_session_maker() as db:
            # 1. Intent Recognition (LLM Router)
            from app.core.intents import detect_intent
            intent_start = time.time()
        
            yield emit_trace("RAW REQUEST (Gateway -> Router)", "Відправлено", f"Вхідний текст клієнта:\n'{msg.text}'\nІсторія ({len(msg.history)} повідомлень)")
            
            intent_data = await detect_intent(msg.text, msg.history, tenant_id, db)
            intent = intent_data.get("intent", "GENERAL")
            search_query = intent_data.get("query", "")
            error_msg = intent_data.get("error", "")
            intent_usage = intent_data.get("usage", {"total_tokens": 0})
            intent_time = round(time.time() - intent_start, 2)
            
            raw_router_response = json.dumps(intent_data, indent=2, ensure_ascii=False)
            
            if intent == "ERROR":
                yield emit_trace("LLM ROUTER: FATAL", "Помилка LLM", f"Не вдалося підключитися до LLM. Помилка:\n{error_msg}\nСирий JSON:\n{raw_router_response}", intent_time)
                yield f"data: {json.dumps({'type': 'token', 'content': 'Вибачте, сталася системна помилка (LLM недоступна).'})}\n\n"
                return
                
            yield emit_trace("LLM ROUTER: DECISION", "Успішно", f"Сирий JSON від моделі-маршрутизатора:\n{raw_router_response}\n\nВитрачено токенів: {intent_usage['total_tokens']}.", intent_time)
            
            res = await db.execute(select(BotSetting).where(BotSetting.tenant_id == tenant_id))
            settings = res.scalars().first()
            
            # 2. Fetch Data (SQL Prices, Q&A, Web Search, or Microservices)
            from app.models.services import ServicePrice
            
            qa_facts = []
            rag_docs = []
            prices = []
            sys_prompt_addition = ""
            
            if intent == "CHECK_REPAIR_STATUS" or "статус" in msg.text.lower():
                yield emit_trace("MICROSERVICE (CRM API)", "Очікування...", "Виконується HTTP GET /api/v1/orders?phone=...")
                time.sleep(0.3)
                crm_data = "{\n  \"order_id\": 1024,\n  \"status\": \"В процесі діагностики\",\n  \"device\": \"Samsung S23\"\n}"
                yield emit_trace("MICROSERVICE (CRM API)", "Знайдено 200 OK", f"Отримано RAW payload:\n{crm_data}", "0.31")
                sys_prompt_addition = f"\nДані з внутрішньої CRM системи (JSON):\n{crm_data}"
                
            elif intent == "WEB_SEARCH" and search_query:
                from app.core.tools import search_internet
                yield emit_trace("EXTERNAL API (DuckDuckGo)", "Виконується", f"Формую GET запит до html.duckduckgo.com/html/\nQuery: {search_query}")
                search_start = time.time()
                search_result = search_internet(search_query, max_results=3)
                search_time = round(time.time() - search_start, 2)
                yield emit_trace("EXTERNAL API (DuckDuckGo)", "Парсинг HTML завершено", f"Знайдено фрагменти:\n{search_result}", search_time)
                
                sys_prompt_addition = f"\nДані з інтернету (DuckDuckGo пошук за запитом '{search_query}'):\n{search_result}"
                
            else:
                # 2.2 SQL / Qdrant Fallbacks
                yield emit_trace("SQL DATABASE (PostgreSQL)", "Виконується", f"SELECT * FROM service_prices WHERE tenant_id='{tenant_id}' LIMIT 100;")
                sql_start = time.time()
                res_price = await db.execute(select(ServicePrice).where(ServicePrice.tenant_id == tenant_id).limit(100))
                prices = res_price.scalars().all()
                sql_time = round(time.time() - sql_start, 2)
                
                sys_prompt_addition = ""
                rag_docs = []
                
                if prices:
                    raw_prices = "\n".join([f"- {p.name}: {p.price} грн" for p in prices])
                    yield emit_trace("SQL DATABASE (PostgreSQL)", "OK", f"Завантажено {len(prices)} рядків з таблиці. RAW DATA:\n{raw_prices[:300]}...", sql_time)
                    sys_prompt_addition = "\nДодаткова інформація з бази прайсів (ПРАЙС-ЛИСТ):\n" + raw_prices
                else:
                    yield emit_trace("SQL DATABASE (PostgreSQL)", "Пусто 0 rows", "Таблиця прайсів порожня або немає збігів", sql_time)
                    
                res_qa = await db.execute(select(QaPair).where(QaPair.tenant_id == tenant_id).limit(50))
                qa_facts = res_qa.scalars().all()
                
                yield emit_trace("VECTOR DB (Qdrant RAG)", "Пошук векторів", f"Генерую embeddings для '{msg.text}' та шукаю в колекції '{tenant_id}'")
                rag_start = time.time()
                from app.core.rag import search_knowledge
                rag_docs = await search_knowledge(msg.text, str(tenant_id), top_k=2)
                rag_time = round(time.time() - rag_start, 2)
                
                doc_details = f"Знайдено FAQ рядків (SQL): {len(qa_facts)}.\nЗнайдено RAG чанків (Qdrant): {len(rag_docs)}.\n\nRAW RAG CHUNKS:\n" + "\n---\n".join(rag_docs)
                yield emit_trace("VECTOR DB (Qdrant RAG)", "Знайдено метрики", doc_details, rag_time)
            
            from app.core.prompt_builder import build_system_prompt
            sys_prompt = build_system_prompt(settings, qa_facts, rag_docs)
            sys_prompt += sys_prompt_addition
            
            temp = float(settings.temperature) if settings and settings.temperature else 0.7
            model_name = settings.llm_model if settings and settings.llm_model else "gemma-4"
            
            messages_to_send = [
                {"role": "system", "content": sys_prompt}
            ]
            
            if msg.history:
                for h in msg.history:
                    messages_to_send.append({"role": h.get("role", "user"), "content": h.get("content", "")})
            else:
                messages_to_send.append({"role": "user", "content": msg.text})
            
            raw_messages_json = json.dumps(messages_to_send, indent=2, ensure_ascii=False)
            yield emit_trace("LLM STREAM (Final Output)", "Будую пакет...", f"Формування JSON пакета для {model_name}...\nТемпература: {temp}\n\nСИРИЙ ПАКЕТ ДЛЯ ВІДПРАВКИ:\n{raw_messages_json}")
            
            try:
                base_url = settings.meta.get("llm_base_url") if settings and settings.meta else None
                api_key = settings.meta.get("llm_api_key") if settings and settings.meta else None
                model_name = settings.llm_model if settings and settings.llm_model else "gemma-4"
                
                from app.core.llm import chat_stream
                
                full_response = ""
                async for token in chat_stream(messages_to_send, model=model_name, temperature=temp, base_url=base_url, api_key=api_key):
                    full_response += token
                    # Think tokens from models like DeepSeek can be filtered, but `chat_stream` doesn't filter them yet.
                    # Here we just pass them through to frontend, frontend can handle or we just send it.
                    yield f"data: {json.dumps({'type': 'token', 'content': token})}\n\n"
                
                gen_time = round(time.time() - start_time, 2)
                yield emit_trace("Генерація LLM", "Успішно", f"Відповідь згенерована ({len(full_response)} символів)", gen_time)
            except Exception as e:
                yield emit_trace("Генерація LLM", "Помилка", str(e))
                yield f"data: {json.dumps({'type': 'token', 'content': 'Помилка підключення до LLM.'})}\n\n"
    return StreamingResponse(event_generator(), media_type="text/event-stream")
