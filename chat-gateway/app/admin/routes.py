from typing import List
from fastapi import APIRouter, Request, Depends, Form, Response, UploadFile, File
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from pydantic import BaseModel
import uuid
import logging

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
    from app.config import normalize_lmstudio_url, settings
    from sqlalchemy import text
    from datetime import date
    
    tenants = await get_all_tenants(db)
    
    # 1. Real LM Studio Check
    lmstudio_status = "ERROR"
    loaded_models = "Немає підключення"
    lmstudio_url = normalize_lmstudio_url(settings.LMSTUDIO_URL)
    url = lmstudio_url.replace('/v1', '') if lmstudio_url.endswith('/v1') else lmstudio_url
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
        "lmstudio_url": lmstudio_url,
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
    
    from app.core.prompt_defaults import (
        ROUTE_PROMPTS, DEFAULT_UNIVERSAL_PERSONA, LEAN_CONTROLLER_PROMPT,
        LEAN_ANSWER_PROMPT, LEAN_CONDUCT_PROMPT, LEAN_WARNING_PROMPT,
    )
    default_settings = BotSetting(
        tenant_id=new_tenant.id,
        system_prompt=DEFAULT_UNIVERSAL_PERSONA,
        llm_model="gemma-4",
        temperature="0.7",
        max_tokens="1024",
        meta={
            "agent_max_iterations": "3",
            "ban_message": "Вітаю, вас забанено.",
            "conduct_enabled": "1",
            "conduct_warnings": "2",
            "marketing_enabled": "0",
            "lean_controller_prompt": LEAN_CONTROLLER_PROMPT,
            "lean_answer_prompt": LEAN_ANSWER_PROMPT,
            "lean_conduct_prompt": LEAN_CONDUCT_PROMPT,
            "lean_warning_prompt": LEAN_WARNING_PROMPT,
            "catalog_synonyms": "",
        },
    )
    db.add(default_settings)
    route_rows = [
        ("catalog", "Каталог: товари/послуги, ціни, описи", ["наявність", "асортимент", "послуга", "ціна", "прайс"]),
        ("qa", "Записи знань та документи", ["умови", "правила", "політика", "гарантія"]),
        ("web_search", "Зовнішній веб-пошук", ["що це", "характеристика", "специфікація"]),
        ("external_price", "Зовнішні пропозиції та ціни", ["ринкова ціна", "у постачальників", "зовнішня пропозиція"]),
        ("business_info", "Бізнес-факти", ["графік", "адреса", "телефон", "оплата", "доставка"]),
        ("handoff", "Передача людині", ["людина", "оператор", "менеджер"]),
    ]
    for code_suffix, label, patterns in route_rows:
        route_meta = dict(ROUTE_PROMPTS[code_suffix])
        db.add(KnowledgeType(
            tenant_id=new_tenant.id,
            code=code_suffix,
            label=label,
            handler=route_meta.get("tool_name") or "route",
            intent_patterns=patterns,
            enabled=True,
            meta=route_meta,
        ))
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
    from app.config import settings as app_settings
    return templates.TemplateResponse(request=request, name="channels/form.html", context={
        "request": request, "user": user, "tenants": tenants,
        "current_tenant_id": tenant_id, "channel": None,
        "public_base_url": app_settings.PUBLIC_BASE_URL.rstrip("/")
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
    elif type == 'webchat':
        creds_json = {"allowed_origins": credentials}
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
    await db.refresh(new_channel)
    if type == 'telegram_userbot':
        import asyncio
        from app.channels.telegram_userbot import userbot_manager
        asyncio.create_task(userbot_manager.restart())
    elif type == 'telegram' and enabled:
        await _register_telegram_webhook(new_channel.id, creds_json.get("token", ""))
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
    from app.config import settings as app_settings
    return templates.TemplateResponse(request=request, name="channels/form.html", context={
        "request": request, "user": user, "tenants": tenants, "current_tenant_id": tenant_id, "channel": channel,
        "public_base_url": app_settings.PUBLIC_BASE_URL.rstrip("/")
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
        elif type == 'webchat':
            creds_json = {"allowed_origins": credentials}
        else:
            creds_json = {"config": credentials}

        channel.name = name
        channel.type = type
        channel.credentials = creds_json
        channel.greeting = greeting
        channel.enabled = enabled
        await db.commit()
        if type == 'telegram_userbot':
            import asyncio
            from app.channels.telegram_userbot import userbot_manager
            asyncio.create_task(userbot_manager.restart())
        elif type == 'telegram' and enabled:
            await _register_telegram_webhook(channel.id, creds_json.get("token", ""))
    return RedirectResponse(url="/admin/channels", status_code=303)


# --- UserBot session generation from the panel (no terminal needed) ---
# Pending Telethon logins between "send code" and "confirm code" requests.
_userbot_logins = {}


@router.post("/channels/userbot/send_code")
async def userbot_send_code(
    api_id: str = Form(...),
    api_hash: str = Form(...),
    phone: str = Form(...),
    user: User = Depends(get_current_user),
):
    from telethon import TelegramClient
    from telethon.sessions import StringSession
    try:
        client = TelegramClient(StringSession(), int(api_id.strip()), api_hash.strip())
        await client.connect()
        sent = await client.send_code_request(phone.strip())
        login_token = str(uuid.uuid4())
        _userbot_logins[login_token] = {
            "client": client,
            "phone": phone.strip(),
            "phone_code_hash": sent.phone_code_hash,
        }
        return {"status": "ok", "token": login_token}
    except Exception as e:
        return {"status": "error", "detail": str(e)}


@router.post("/channels/userbot/confirm_code")
async def userbot_confirm_code(
    token: str = Form(...),
    code: str = Form(...),
    password: str = Form(""),
    user: User = Depends(get_current_user),
):
    from telethon.errors import SessionPasswordNeededError
    data = _userbot_logins.get(token)
    if not data:
        return {"status": "error", "detail": "Сесія логіну прострочена. Надішліть код ще раз."}
    client = data["client"]
    try:
        try:
            await client.sign_in(data["phone"], code.strip(), phone_code_hash=data["phone_code_hash"])
        except SessionPasswordNeededError:
            if not password:
                return {"status": "need_password"}
            await client.sign_in(password=password)
        session_string = client.session.save()
        await client.disconnect()
        _userbot_logins.pop(token, None)
        return {"status": "ok", "session_string": session_string}
    except Exception as e:
        return {"status": "error", "detail": str(e)}


async def _register_telegram_webhook(channel_id: uuid.UUID, token: str):
    """Auto-register the Telegram webhook for a bot channel (spec §2.2)."""
    if not token:
        return
    import httpx
    from app.config import settings as app_settings
    webhook_url = f"{app_settings.PUBLIC_BASE_URL.rstrip('/')}/webhook/telegram/{channel_id}"
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(
                f"https://api.telegram.org/bot{token}/setWebhook",
                json={"url": webhook_url})
            data = resp.json()
            if not data.get("ok"):
                logging.getLogger(__name__).error(f"setWebhook failed for channel {channel_id}: {data}")
            else:
                logging.getLogger(__name__).info(f"Webhook registered: {webhook_url}")
    except Exception as e:
        logging.getLogger(__name__).error(f"setWebhook error for channel {channel_id}: {e}")
# --- CONVERSATIONS (Діалоги: жива стрічка + архів) ---
@router.get("/bans", response_class=HTMLResponse)
async def bans_page(
    request: Request,
    user: User = Depends(get_current_user),
    tenant_id: uuid.UUID = Depends(get_current_tenant_id),
    db: AsyncSession = Depends(get_db),
):
    from app.models.conversation import SessionBan
    from sqlalchemy import desc

    tenants = await get_all_tenants(db)
    bans = []
    if tenant_id:
        from app.core.bans import import_legacy_redis_bans
        await import_legacy_redis_bans(db, tenant_id)
        result = await db.execute(
            select(SessionBan, Channel.name)
            .join(Channel, Channel.id == SessionBan.channel_id, isouter=True)
            .where(SessionBan.tenant_id == tenant_id)
            .order_by(SessionBan.active.desc(), desc(SessionBan.banned_at))
            .limit(500)
        )
        bans = [{"ban": ban, "channel_name": channel_name or ""}
                for ban, channel_name in result.all()]
    return templates.TemplateResponse(request=request, name="bans.html", context={
        "request": request,
        "user": user,
        "tenants": tenants,
        "current_tenant_id": tenant_id,
        "bans": bans,
        "active_count": sum(1 for item in bans if item["ban"].active),
    })


@router.post("/bans/{ban_id}/unban")
async def unban_session(
    ban_id: uuid.UUID,
    user: User = Depends(get_current_user),
    tenant_id: uuid.UUID = Depends(get_current_tenant_id),
    db: AsyncSession = Depends(get_db),
):
    from app.models.conversation import SessionBan
    from app.core.history import MemoryManager
    from sqlalchemy import func

    if not tenant_id:
        return RedirectResponse(url="/admin/bans", status_code=303)
    result = await db.execute(select(SessionBan).where(
        SessionBan.id == ban_id,
        SessionBan.tenant_id == tenant_id,
    ))
    ban = result.scalars().first()
    if ban and ban.active:
        await MemoryManager.remove_ban(ban.chat_key)
        ban.active = False
        ban.unbanned_at = func.now()
        await db.commit()
    return RedirectResponse(url="/admin/bans?ok=unbanned", status_code=303)


@router.get("/conversations", response_class=HTMLResponse)
async def conversations_page(
    request: Request,
    user: User = Depends(get_current_user),
    tenant_id: uuid.UUID = Depends(get_current_tenant_id),
    db: AsyncSession = Depends(get_db)
):
    tenants = await get_all_tenants(db)
    return templates.TemplateResponse(request=request, name="conversations.html", context={
        "request": request, "user": user, "tenants": tenants, "current_tenant_id": tenant_id
    })


@router.get("/api/conversations/list")
async def conversations_list(
    user: User = Depends(get_current_user),
    tenant_id: uuid.UUID = Depends(get_current_tenant_id),
    db: AsyncSession = Depends(get_db)
):
    """Archive: conversations with last message + counts, newest first."""
    from app.models.conversation import Conversation, Message
    from app.models.channel import Channel
    from sqlalchemy import func, desc
    if not tenant_id:
        return {"conversations": []}
    res = await db.execute(
        select(Conversation, Channel.type, Channel.name,
               func.count(Message.id), func.max(Message.created_at))
        .join(Message, Message.conversation_id == Conversation.id, isouter=True)
        .join(Channel, Channel.id == Conversation.channel_id, isouter=True)
        .where(Conversation.tenant_id == tenant_id)
        .group_by(Conversation.id, Channel.type, Channel.name)
        .order_by(desc(func.max(Message.created_at)))
        .limit(200)
    )
    items = []
    for conv, ch_type, ch_name, cnt, last_at in res.all():
        # last message text
        rl = await db.execute(
            select(Message.role, Message.content).where(Message.conversation_id == conv.id)
            .order_by(Message.created_at.desc()).limit(1))
        last = rl.first()
        items.append({
            "id": str(conv.id),
            "chat_id": conv.external_chat_id,
            "channel_type": ch_type or "?",
            "channel_name": ch_name or "",
            "count": cnt or 0,
            "last_at": last_at.isoformat() if last_at else None,
            "last_role": last[0] if last else "",
            "last_text": (last[1][:120] if last and last[1] else ""),
        })
    return {"conversations": items}


@router.get("/api/conversations/{conv_id}/messages")
async def conversation_messages(
    conv_id: uuid.UUID,
    user: User = Depends(get_current_user),
    tenant_id: uuid.UUID = Depends(get_current_tenant_id),
    db: AsyncSession = Depends(get_db)
):
    from app.models.conversation import Conversation, Message
    res_c = await db.execute(select(Conversation).where(
        Conversation.id == conv_id, Conversation.tenant_id == tenant_id))
    if not res_c.scalars().first():
        return {"messages": []}
    res = await db.execute(
        select(Message.role, Message.content, Message.created_at, Message.meta)
        .where(Message.conversation_id == conv_id).order_by(Message.created_at))
    return {"messages": [
        {"role": r, "content": c, "at": t.isoformat() if t else None,
         "trace": (m or {}).get("trace", []) if isinstance(m, dict) else []}
        for r, c, t, m in res.all()
    ]}


@router.get("/api/conversations/feed")
async def conversations_feed(
    since: str = "",
    user: User = Depends(get_current_user),
    tenant_id: uuid.UUID = Depends(get_current_tenant_id),
    db: AsyncSession = Depends(get_db)
):
    """Live feed: messages newer than `since` (ISO ts), across all chats."""
    from app.models.conversation import Conversation, Message
    from app.models.channel import Channel
    from datetime import datetime, timezone, timedelta
    if not tenant_id:
        return {"messages": [], "now": ""}
    if since:
        try:
            since_dt = datetime.fromisoformat(since)
        except ValueError:
            since_dt = datetime.now(timezone.utc) - timedelta(minutes=5)
    else:
        since_dt = datetime.now(timezone.utc) - timedelta(minutes=2)
    res = await db.execute(
        select(Message.role, Message.content, Message.created_at,
               Conversation.external_chat_id, Channel.type, Message.meta)
        .join(Conversation, Conversation.id == Message.conversation_id)
        .join(Channel, Channel.id == Conversation.channel_id, isouter=True)
        .where(Conversation.tenant_id == tenant_id, Message.created_at > since_dt)
        .order_by(Message.created_at).limit(100)
    )
    msgs = [
        {"role": r, "content": c, "at": t.isoformat() if t else None,
         "chat_id": chat, "channel_type": ch or "?",
         "trace": (m or {}).get("trace", []) if isinstance(m, dict) else []}
        for r, c, t, chat, ch, m in res.all()
    ]
    now = datetime.now(timezone.utc).isoformat()
    return {"messages": msgs, "now": now}


@router.get("/api/conversations/stream")
async def conversations_stream(
    request: Request,
    after: int = 0,
    user: User = Depends(get_current_user),
    tenant_id: uuid.UUID = Depends(get_current_tenant_id),
):
    """Real-time SSE feed of pipeline steps and messages for the current tenant.
    Each `emit()` in the agent is pushed here the instant it fires — the admin
    sees what the routes and the model received live, step by step, not after the
    turn is persisted."""
    import asyncio
    import json
    from fastapi.responses import StreamingResponse
    from app.core.live_feed import subscribe, unsubscribe, recent

    async def gen():
        if not tenant_id:
            yield "event: end\ndata: {}\n\n"
            return
        q = subscribe(tenant_id)
        try:
            # Backfill anything that happened just before this connection opened.
            for ev in recent(tenant_id, after_seq=after):
                yield f"data: {json.dumps(ev, ensure_ascii=False)}\n\n"
            while True:
                if await request.is_disconnected():
                    break
                try:
                    ev = await asyncio.wait_for(q.get(), timeout=15)
                    yield f"data: {json.dumps(ev, ensure_ascii=False)}\n\n"
                except asyncio.TimeoutError:
                    yield ": ping\n\n"  # keep the connection alive
        finally:
            unsubscribe(tenant_id, q)

    return StreamingResponse(gen(), media_type="text/event-stream", headers={
        "Cache-Control": "no-cache",
        "X-Accel-Buffering": "no",
    })


# --- HELP / DIAGNOSTICS ---
@router.get("/help", response_class=HTMLResponse)
async def help_page(
    request: Request,
    user: User = Depends(get_current_user),
    tenant_id: uuid.UUID = Depends(get_current_tenant_id),
    db: AsyncSession = Depends(get_db)
):
    tenants = await get_all_tenants(db)
    return templates.TemplateResponse(request=request, name="help.html", context={
        "request": request, "user": user, "tenants": tenants, "current_tenant_id": tenant_id
    })


@router.post("/api/test-parse")
async def api_test_parse(url: str = Form(...), user: User = Depends(get_current_user)):
    import asyncio
    from app.core.tools import fetch_and_parse_url
    try:
        text = await asyncio.to_thread(fetch_and_parse_url, url.strip(), 3000)
        if text.startswith("Error fetching URL") or "Could not extract" in text:
            return {"ok": False, "detail": text}
        return {"ok": True, "length": len(text), "text": text}
    except Exception as e:
        return {"ok": False, "detail": str(e)}


@router.post("/api/test-search")
async def api_test_search(
    query: str = Form(...),
    user: User = Depends(get_current_user),
    tenant_id: uuid.UUID = Depends(get_current_tenant_id),
    db: AsyncSession = Depends(get_db)
):
    import asyncio
    from app.core.tools import web_research
    serper_key = None
    if tenant_id:
        res = await db.execute(select(BotSetting).where(BotSetting.tenant_id == tenant_id))
        s = res.scalars().first()
        if s and s.meta:
            serper_key = s.meta.get("serper_api_key") or None
    try:
        text = await asyncio.to_thread(web_research, query.strip(), 3, 2000, serper_key)
        return {"ok": True, "text": text}
    except Exception as e:
        return {"ok": False, "detail": str(e)}


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
    escalation_prompt: str = Form(""),
    fallback_text: str = Form(""),
    agent_max_iterations: str = Form("3"),
    serper_api_key: str = Form(""),
    parts_sites: str = Form(""),
    price_search_urls: str = Form(""),
    ban_message: str = Form("Вітаю, вас забанено."),
    conduct_enabled: str = Form("0"),
    conduct_warnings: str = Form("2"),
    marketing_enabled: str = Form(""),
    catalog_synonyms: str = Form(""),
    lean_controller_prompt: str = Form(""),
    lean_answer_prompt: str = Form(""),
    lean_conduct_prompt: str = Form(""),
    lean_warning_prompt: str = Form(""),
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
            from app.config import normalize_lmstudio_url
            meta_data["llm_base_url"] = normalize_lmstudio_url(llm_base_url)
            meta_data["llm_api_key"] = llm_api_key

            meta_data["agent_max_iterations"] = agent_max_iterations
            meta_data["serper_api_key"] = serper_api_key.strip()
            meta_data["parts_sites"] = parts_sites.strip()
            meta_data["price_search_urls"] = price_search_urls.strip()
            meta_data["catalog_synonyms"] = catalog_synonyms.strip()
            meta_data["ban_message"] = ban_message.strip() or "Вітаю, вас забанено."

            # Conduct + Marketing modules (toggles)
            meta_data["conduct_enabled"] = "1" if str(conduct_enabled).lower() in ("1", "true", "on", "yes") else "0"
            try:
                meta_data["conduct_warnings"] = str(max(1, min(5, int(conduct_warnings))))
            except (ValueError, TypeError):
                meta_data["conduct_warnings"] = "2"
            meta_data["marketing_enabled"] = "1" if str(marketing_enabled).lower() in ("1", "true", "on", "yes") else "0"
            meta_data["lean_controller_prompt"] = lean_controller_prompt.strip()
            meta_data["lean_answer_prompt"] = lean_answer_prompt.strip()
            meta_data["lean_conduct_prompt"] = lean_conduct_prompt.strip()
            meta_data["lean_warning_prompt"] = lean_warning_prompt.strip()

            # drop legacy fields no engine reads anymore (moved into persona / routes)
            for _k in ("price_triggers", "capability_triggers", "business_info_triggers", "brand_words", "part_words",
                       "agent_decision_rules", "answer_style", "intake_policy", "conduct_policy", "parts_instruction",
                       "tpl_evaluation_rules", "web_research_mode", "parts_sales_mode", "external_part_price_mode",
                       "fallback_sites", "tpl_escalate_instruction",
                       "enabled_tools", "router_json_mode"):
                meta_data.pop(_k, None)
            settings.meta = meta_data
            
            from sqlalchemy.orm.attributes import flag_modified
            flag_modified(settings, "meta")
            
            await db.commit()
    return RedirectResponse(url="/admin/settings", status_code=303)


# --- TENANT CONFIG EXPORT / IMPORT (all prompts & routing in one file) ---
# Editable fields exported/imported as one JSON file. Engine mechanics (action
# format, loop) are NOT here — they stay in code.
_CONFIG_COLUMNS = ["system_prompt", "business_rules", "marketing_rules",
                   "escalation_prompt", "escalation_policy", "fallback_text",
                   "llm_model", "temperature", "max_tokens",
                   "rag_top_k", "rag_score_threshold"]
_CONFIG_META_KEYS = ["agent_max_iterations",
                     "ban_message", "conduct_enabled", "conduct_warnings",
                     "marketing_enabled", "parts_sites", "price_search_urls",
                     "catalog_synonyms", "business_info",
                     "lean_controller_prompt", "lean_answer_prompt", "lean_conduct_prompt",
                     "lean_warning_prompt",
                     "llm_base_url"]  # serper_api_key intentionally omitted (secret)
_CONFIG_ROUTE_META_KEYS = [
    "tool_name", "source_description", "query_prompt",
    "result_validation_prompt", "target_url",
]


@router.get("/settings/export")
async def export_config(
    user: User = Depends(get_current_user),
    tenant_id: uuid.UUID = Depends(get_current_tenant_id),
    db: AsyncSession = Depends(get_db)
):
    import json as _json
    from fastapi.responses import StreamingResponse as _SR
    res = await db.execute(select(BotSetting).where(BotSetting.tenant_id == tenant_id))
    s = res.scalars().first()
    cfg = {"_about": "Tenant config: all editable prompts & routing. Engine "
                     "mechanics (JSON action format, loop) stay in code. Import "
                     "this file in Settings to fill everything.",
           "columns": {}, "meta": {}, "routes": []}
    if s:
        for c in _CONFIG_COLUMNS:
            cfg["columns"][c] = getattr(s, c, None)
        meta = s.meta or {}
        for k in _CONFIG_META_KEYS:
            if k in meta:
                cfg["meta"][k] = meta[k]
        route_res = await db.execute(
            select(KnowledgeType).where(KnowledgeType.tenant_id == tenant_id).order_by(KnowledgeType.priority)
        )
        cfg["routes"] = [
            {
                "code": route.code,
                "label": route.label,
                "handler": route.handler,
                "intent_patterns": route.intent_patterns or [],
                "enabled": bool(route.enabled),
                "meta": {
                    key: (route.meta or {}).get(key)
                    for key in _CONFIG_ROUTE_META_KEYS
                    if key in (route.meta or {})
                },
            }
            for route in route_res.scalars().all()
        ]
    data = _json.dumps(cfg, ensure_ascii=False, indent=2).encode("utf-8")
    return _SR(iter([data]), media_type="application/json",
               headers={"Content-Disposition": 'attachment; filename="tenant_config.json"'})


@router.post("/settings/import")
async def import_config(
    file: UploadFile = File(...),
    user: User = Depends(get_current_user),
    tenant_id: uuid.UUID = Depends(get_current_tenant_id),
    db: AsyncSession = Depends(get_db)
):
    import json as _json
    if not tenant_id:
        return RedirectResponse(url="/admin/settings", status_code=303)
    try:
        cfg = _json.loads((await file.read()).decode("utf-8"))
    except Exception:
        return RedirectResponse(url="/admin/settings?err=badfile", status_code=303)

    res = await db.execute(select(BotSetting).where(BotSetting.tenant_id == tenant_id))
    s = res.scalars().first()
    if not s:
        s = BotSetting(tenant_id=tenant_id, system_prompt="", temperature="0.7", max_tokens="1024")
        db.add(s)
    cols = cfg.get("columns", {})
    for c in _CONFIG_COLUMNS:
        if c in cols and cols[c] is not None:
            setattr(s, c, cols[c])
    meta = dict(s.meta or {})
    for k, v in (cfg.get("meta", {}) or {}).items():
        if k in _CONFIG_META_KEYS:
            meta[k] = v
    s.meta = meta
    from sqlalchemy.orm.attributes import flag_modified
    flag_modified(s, "meta")
    for route_data in cfg.get("routes", []) or []:
        code = str(route_data.get("code") or "").strip()
        if not code:
            continue
        route_res = await db.execute(select(KnowledgeType).where(
            KnowledgeType.tenant_id == tenant_id, KnowledgeType.code == code))
        route = route_res.scalars().first()
        if not route:
            route = KnowledgeType(tenant_id=tenant_id, code=code, label=code, handler="fallback")
            db.add(route)
        route.label = str(route_data.get("label") or code)
        route.intent_patterns = list(route_data.get("intent_patterns") or [])
        route.enabled = bool(route_data.get("enabled", True))
        imported_meta = dict(route_data.get("meta") or {})
        route.handler = str(route_data.get("handler") or imported_meta.get("tool_name") or "route")
        route.meta = {
            key: imported_meta[key]
            for key in _CONFIG_ROUTE_META_KEYS
            if key in imported_meta
        }
        flag_modified(route, "meta")
    await db.commit()
    return RedirectResponse(url="/admin/settings?ok=imported", status_code=303)

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
    business_info = {}
    bot_meta = {}
    if tenant_id:
        res_t = await db.execute(select(Tenant).where(Tenant.id == tenant_id))
        tenant = res_t.scalars().first()

        res_qa = await db.execute(select(QaPair).where(QaPair.tenant_id == tenant_id).order_by(QaPair.question))
        qa_pairs = res_qa.scalars().all()

        res_logic = await db.execute(select(KnowledgeType).where(KnowledgeType.tenant_id == tenant_id).order_by(KnowledgeType.label))
        logic_schemas = res_logic.scalars().all()

        res_docs = await db.execute(select(KbDocument).where(KbDocument.tenant_id == tenant_id).order_by(KbDocument.updated_at.desc()))
        documents = res_docs.scalars().all()

        res_s = await db.execute(select(BotSetting).where(BotSetting.tenant_id == tenant_id))
        bot_settings_row = res_s.scalars().first()
        if bot_settings_row and bot_settings_row.meta:
            bot_meta = bot_settings_row.meta or {}
            business_info = bot_meta.get("business_info", {}) or {}

    return templates.TemplateResponse(request=request, name="knowledge/index.html", context={
        "request": request, "user": user, "tenants": tenants,
        "current_tenant_id": tenant_id, "tenant": tenant,
        "qa_pairs": qa_pairs, "logic_schemas": logic_schemas,
        "documents": documents, "business_info": business_info,
        "bot_meta": bot_meta,
    })


@router.post("/knowledge/business_info")
async def update_business_info(
    bi_phone: str = Form(""),
    bi_address: str = Form(""),
    bi_hours: str = Form(""),
    bi_holidays: str = Form(""),
    bi_payment: str = Form(""),
    bi_delivery: str = Form(""),
    bi_warranty: str = Form(""),
    bi_extra: str = Form(""),
    user: User = Depends(get_current_user),
    tenant_id: uuid.UUID = Depends(get_current_tenant_id),
    db: AsyncSession = Depends(get_db)
):
    if tenant_id:
        res = await db.execute(select(BotSetting).where(BotSetting.tenant_id == tenant_id))
        settings = res.scalars().first()
        if settings:
            meta_data = settings.meta if settings.meta else {}
            business_info = {
                "phone": bi_phone.strip(),
                "address": bi_address.strip(),
                "hours": bi_hours.strip(),
                "holidays": bi_holidays.strip(),
                "payment": bi_payment.strip(),
                "delivery": bi_delivery.strip(),
                "warranty": bi_warranty.strip(),
                "extra": bi_extra.strip(),
            }
            meta_data["business_info"] = {k: v for k, v in business_info.items() if v}
            settings.meta = meta_data
            from sqlalchemy.orm.attributes import flag_modified
            flag_modified(settings, "meta")
            await db.commit()
    return RedirectResponse(url="/admin/knowledge", status_code=303)

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
                    price=str(price_data.get("price", "")),
                    description=str(price_data.get("description", "") or ""),
                    meta={
                        k: str(price_data.get(k) or "").strip()
                        for k in ("item_type", "brand", "availability", "characteristics", "composition")
                        if str(price_data.get(k) or "").strip()
                    },
                )
                db.add(p)
                
            # Import category knowledge records
            for faq_data in cat_data.get("faqs", []):
                q = QaPair(
                    tenant_id=tenant_id,
                    question=faq_data.get("question", ""),
                    answer=faq_data.get("answer", ""),
                    category=f"KNOWLEDGE_{cat.slug}"
                )
                db.add(q)
                
        # 2. Import global knowledge records
        for faq_data in data.get("global_faq", []):
            q = QaPair(
                tenant_id=tenant_id,
                question=faq_data.get("question", ""),
                answer=faq_data.get("answer", ""),
                category="KNOWLEDGE_Global"
            )
            db.add(q)
            
        await db.commit()
    except Exception as e:
        import logging
        logging.getLogger(__name__).error(f"Import Error: {e}")
        
    return RedirectResponse(url="/admin/knowledge/prices", status_code=303)

# --- Price table tooling: template / export / tabular import with column mapping ---

PRICE_TEMPLATE_ROWS = [
    {"category": "Категорія", "name": "Назва товару або послуги", "item_type": "товар/послуга/складна послуга", "brand": "бренд або група", "price": "ціна або умова", "availability": "наявність", "characteristics": "характеристики", "composition": "склад послуги/комплект", "description": "примітки для моделі: що входить, що уточнити, коли погодити ціну"},
    {"category": "Навушники", "name": "Ремонт навушників", "item_type": "послуга", "brand": "Bose, Marshall, JBL", "price": "після діагностики", "availability": "приймаємо в сервісі", "characteristics": "TWS, накладні, дротові", "composition": "діагностика + ремонт/заміна вузла після погодження", "description": "Якщо модель незрозуміла, не просити фото одразу; спершу спитати симптом."},
    {"category": "Смартфони", "name": "Заміна дисплея", "item_type": "складна послуга", "brand": "Apple, Xiaomi, Samsung", "price": "робота + деталь", "availability": "деталь залежить від моделі", "characteristics": "потрібна точна модель для ціни деталі", "composition": "вартість дисплея + робота майстра", "description": "Якщо точну ціну деталі не знаємо — шукати зовнішню ціну комплектуючої."},
]


def _slugify(title: str) -> str:
    import re as _re
    slug = _re.sub(r"[^\w]+", "-", (title or "").strip().lower(), flags=_re.UNICODE).strip("-")
    return slug or "import"


def _rows_to_file(rows: list, fmt: str, filename_base: str):
    """Serialize [{category,name,price,description}] to xlsx/csv/yaml StreamingResponse."""
    import io
    from fastapi.responses import StreamingResponse as _SR

    if fmt == "csv":
        import csv as _csv
        buf = io.StringIO()
        writer = _csv.writer(buf)
        writer.writerow(["category", "name", "item_type", "brand", "price", "availability", "characteristics", "composition", "description"])
        for r in rows:
            writer.writerow([r.get("category", ""), r.get("name", ""), r.get("item_type", ""), r.get("brand", ""), r.get("price", ""), r.get("availability", ""), r.get("characteristics", ""), r.get("composition", ""), r.get("description", "")])
        data = buf.getvalue().encode("utf-8-sig")
        media, ext = "text/csv", "csv"
    elif fmt == "yaml":
        cats = {}
        for r in rows:
            cats.setdefault(r["category"], []).append({
                "name": r["name"],
                "item_type": r.get("item_type", ""),
                "brand": r.get("brand", ""),
                "price": r["price"],
                "availability": r.get("availability", ""),
                "characteristics": r.get("characteristics", ""),
                "composition": r.get("composition", ""),
                "description": r.get("description", ""),
            })
        doc = {"categories": [
            {"slug": _slugify(title), "title": title, "services": services}
            for title, services in cats.items()
        ]}
        data = yaml.safe_dump(doc, allow_unicode=True, sort_keys=False).encode("utf-8")
        media, ext = "application/x-yaml", "yaml"
    else:  # xlsx
        from openpyxl import Workbook
        wb = Workbook()
        ws = wb.active
        ws.title = "Прайс"
        ws.append(["category", "name", "item_type", "brand", "price", "availability", "characteristics", "composition", "description"])
        for r in rows:
            ws.append([r.get("category", ""), r.get("name", ""), r.get("item_type", ""), r.get("brand", ""), r.get("price", ""), r.get("availability", ""), r.get("characteristics", ""), r.get("composition", ""), r.get("description", "")])
        bio = io.BytesIO()
        wb.save(bio)
        data = bio.getvalue()
        media, ext = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", "xlsx"

    return _SR(
        iter([data]), media_type=media,
        headers={"Content-Disposition": f'attachment; filename="{filename_base}.{ext}"'}
    )


@router.get("/knowledge/prices/template")
async def download_price_template(
    fmt: str = "xlsx",
    user: User = Depends(get_current_user)
):
    """Downloadable example price file so the user sees the expected format."""
    return _rows_to_file(PRICE_TEMPLATE_ROWS, fmt, "price_template")


@router.get("/knowledge/prices/export")
async def export_prices(
    fmt: str = "xlsx",
    user: User = Depends(get_current_user),
    tenant_id: uuid.UUID = Depends(get_current_tenant_id),
    db: AsyncSession = Depends(get_db)
):
    """Export the tenant's current price list (xlsx/csv/yaml)."""
    from app.models.services import ServiceCategory, ServicePrice
    rows = []
    if tenant_id:
        res = await db.execute(
            select(ServicePrice, ServiceCategory.title)
            .join(ServiceCategory, ServicePrice.category_id == ServiceCategory.id)
            .where(ServicePrice.tenant_id == tenant_id)
            .order_by(ServiceCategory.title, ServicePrice.name)
        )
        for price, cat_title in res.all():
            pm = getattr(price, "meta", None) or {}
            rows.append({
                "category": cat_title or "",
                "name": price.name,
                "item_type": pm.get("item_type", ""),
                "brand": pm.get("brand", ""),
                "price": price.price,
                "availability": pm.get("availability", ""),
                "characteristics": pm.get("characteristics", ""),
                "composition": pm.get("composition", ""),
                "description": getattr(price, "description", "") or "",
            })
    return _rows_to_file(rows, fmt, "prices_export")


def _parse_table_upload(content: bytes, filename: str):
    """Parse xlsx/csv bytes into (columns, rows-of-dicts). Values as strings."""
    import io
    import pandas as pd
    ext = filename.split(".")[-1].lower() if "." in filename else ""
    if ext in ("xlsx", "xls"):
        df = pd.read_excel(io.BytesIO(content), dtype=str)
    else:
        df = pd.read_csv(io.BytesIO(content), dtype=str, sep=None, engine="python", encoding="utf-8-sig")
    df = df.fillna("")
    df.columns = [str(c).strip() for c in df.columns]
    return list(df.columns), df.to_dict(orient="records")


@router.post("/knowledge/prices/import_table")
async def import_prices_table(
    file: UploadFile = File(...),
    mode: str = Form("preview"),
    name_col: str = Form(None),
    price_col: str = Form(None),
    category_col: str = Form(None),
    description_col: str = Form(None),
    item_type_col: str = Form(None),
    brand_col: str = Form(None),
    availability_col: str = Form(None),
    characteristics_col: str = Form(None),
    composition_col: str = Form(None),
    user: User = Depends(get_current_user),
    tenant_id: uuid.UUID = Depends(get_current_tenant_id),
    db: AsyncSession = Depends(get_db)
):
    """
    Import prices from any xlsx/csv schema.
    mode=preview: return detected columns + first rows so the UI can ask for mapping.
    mode=commit: upsert prices using the user-chosen column mapping.
    """
    if not tenant_id or not file.filename:
        return {"status": "error", "detail": "Не вибрано тенант або файл"}

    content = await file.read()
    try:
        columns, records = _parse_table_upload(content, file.filename)
    except Exception as e:
        return {"status": "error", "detail": f"Не вдалося прочитати файл: {e}"}

    if mode == "preview" or not name_col or not price_col:
        return {"status": "ok", "columns": columns, "rows": records[:5], "total": len(records)}

    from app.models.services import ServiceCategory, ServicePrice
    imported, skipped = 0, 0
    cat_cache = {}
    try:
        for rec in records:
            name = str(rec.get(name_col, "")).strip()
            price = str(rec.get(price_col, "")).strip()
            description = str(rec.get(description_col, "")).strip() if description_col else ""
            item_meta = {
                "item_type": str(rec.get(item_type_col, "")).strip() if item_type_col else "",
                "brand": str(rec.get(brand_col, "")).strip() if brand_col else "",
                "availability": str(rec.get(availability_col, "")).strip() if availability_col else "",
                "characteristics": str(rec.get(characteristics_col, "")).strip() if characteristics_col else "",
                "composition": str(rec.get(composition_col, "")).strip() if composition_col else "",
            }
            item_meta = {k: v for k, v in item_meta.items() if v}
            if not name or not price:
                skipped += 1
                continue
            cat_title = str(rec.get(category_col, "")).strip() if category_col else ""
            if not cat_title:
                cat_title = "Імпорт"

            if cat_title not in cat_cache:
                slug = _slugify(cat_title)
                res_c = await db.execute(select(ServiceCategory).where(
                    ServiceCategory.tenant_id == tenant_id, ServiceCategory.slug == slug))
                cat = res_c.scalars().first()
                if not cat:
                    cat = ServiceCategory(tenant_id=tenant_id, slug=slug, title=cat_title)
                    db.add(cat)
                    await db.flush()
                cat_cache[cat_title] = cat
            cat = cat_cache[cat_title]

            # Upsert by (category, name): update price if the row already exists.
            res_p = await db.execute(select(ServicePrice).where(
                ServicePrice.tenant_id == tenant_id,
                ServicePrice.category_id == cat.id,
                ServicePrice.name == name))
            existing = res_p.scalars().first()
            if existing:
                existing.price = price
                existing.description = description
                existing.meta = item_meta
            else:
                db.add(ServicePrice(
                    tenant_id=tenant_id,
                    category_id=cat.id,
                    name=name,
                    price=price,
                    description=description,
                    meta=item_meta,
                ))
            imported += 1
        await db.commit()
        return {"status": "ok", "imported": imported, "skipped": skipped}
    except Exception as e:
        await db.rollback()
        import logging
        logging.getLogger(__name__).error(f"Table import error: {e}")
        return {"status": "error", "detail": str(e)}


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
                cat_info["services"].append({
                    "name": s.get("name"),
                    "price": s.get("price"),
                    "description": s.get("description", ""),
                })
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
                s_description = str(s.get("description") or "")
                if not s_name: continue
                
                res_p = await db.execute(select(ServicePrice).where(ServicePrice.category_id == cat.id, ServicePrice.name == s_name))
                price_obj = res_p.scalars().first()
                if price_obj:
                    price_obj.price = s_price
                    price_obj.description = s_description
                    price_obj.meta = {
                        k: str(s.get(k) or "").strip()
                        for k in ("item_type", "brand", "availability", "characteristics", "composition")
                        if str(s.get(k) or "").strip()
                    }
                else:
                    price_obj = ServicePrice(
                        tenant_id=tenant_id,
                        category_id=cat.id,
                        name=s_name,
                        price=s_price,
                        description=s_description,
                        meta={
                            k: str(s.get(k) or "").strip()
                            for k in ("item_type", "brand", "availability", "characteristics", "composition")
                            if str(s.get(k) or "").strip()
                        },
                    )
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
        descriptions = form_data.getlist("price_description[]")
        item_types = form_data.getlist("price_item_type[]")
        brands = form_data.getlist("price_brand[]")
        availabilities = form_data.getlist("price_availability[]")
        characteristics = form_data.getlist("price_characteristics[]")
        compositions = form_data.getlist("price_composition[]")
        
        await db.execute(ServicePrice.__table__.delete().where(ServicePrice.category_id == cat.id))
        
        for idx, (name, price) in enumerate(zip(names, prices)):
            if name and price:
                item_meta = {
                    "item_type": item_types[idx].strip() if idx < len(item_types) else "",
                    "brand": brands[idx].strip() if idx < len(brands) else "",
                    "availability": availabilities[idx].strip() if idx < len(availabilities) else "",
                    "characteristics": characteristics[idx].strip() if idx < len(characteristics) else "",
                    "composition": compositions[idx].strip() if idx < len(compositions) else "",
                }
                item_meta = {k: v for k, v in item_meta.items() if v}
                db.add(ServicePrice(
                    tenant_id=tenant_id,
                    category_id=cat.id,
                    name=name,
                    price=price,
                    description=descriptions[idx] if idx < len(descriptions) else "",
                    meta=item_meta,
                ))
                
        await db.commit()
        
    return RedirectResponse(url="/admin/knowledge/prices", status_code=303)

# --- KNOWLEDGE: RECORDS ---
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
    tool_name: str = Form(...),
    target_url: str = Form(""),
    source_description: str = Form(""),
    query_prompt: str = Form(""),
    result_validation_prompt: str = Form(""),
    enabled: bool = Form(False),
    user: User = Depends(get_current_user),
    tenant_id: uuid.UUID = Depends(get_current_tenant_id),
    db: AsyncSession = Depends(get_db)
):
    if tenant_id:
        patterns = [p.strip() for p in intent_patterns.split(",")] if intent_patterns else []
        tool = tool_name.strip()
        # Only fields the lean engine actually uses are stored.
        meta_data = {
            "target_url": target_url,
            "tool_name": tool,
            "source_description": source_description.strip(),
            "query_prompt": query_prompt.strip(),
            "result_validation_prompt": result_validation_prompt.strip(),
        }
        logic = KnowledgeType(
            tenant_id=tenant_id, label=label, code=code,
            intent_patterns=patterns, handler=tool or "route",
            enabled=enabled, meta=meta_data
        )
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


@router.post("/knowledge/logic/{logic_id}/toggle")
async def logic_toggle(
    logic_id: uuid.UUID,
    user: User = Depends(get_current_user),
    tenant_id: uuid.UUID = Depends(get_current_tenant_id),
    db: AsyncSession = Depends(get_db)
):
    if tenant_id:
        res = await db.execute(select(KnowledgeType).where(
            KnowledgeType.id == logic_id,
            KnowledgeType.tenant_id == tenant_id,
        ))
        logic = res.scalars().first()
        if logic:
            logic.enabled = not bool(logic.enabled)
            await db.commit()
    return RedirectResponse(url="/admin/knowledge?tab=logic", status_code=303)

@router.post("/knowledge/logic/{logic_id}/edit")
async def logic_edit(
    logic_id: uuid.UUID,
    request: Request,
    label: str = Form(...),
    code: str = Form(...),
    intent_patterns: str = Form(...),
    tool_name: str = Form(...),
    target_url: str = Form(""),
    source_description: str = Form(""),
    query_prompt: str = Form(""),
    result_validation_prompt: str = Form(""),
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
            tool = tool_name.strip()
            logic.handler = tool or "route"
            logic.enabled = enabled
            meta_data = logic.meta if logic.meta else {}
            meta_data["target_url"] = target_url
            meta_data["tool_name"] = tool
            meta_data["source_description"] = source_description.strip()
            meta_data["query_prompt"] = query_prompt.strip()
            meta_data["result_validation_prompt"] = result_validation_prompt.strip()
            # drop legacy fields the lean engine never reads
            for _dead in ("reasoning", "next_step_prompt", "no_result_prompt", "fallback_action", "target_category"):
                meta_data.pop(_dead, None)
            logic.meta = meta_data
            
            from sqlalchemy.orm.attributes import flag_modified
            flag_modified(logic, "meta")
            
            await db.commit()
    return RedirectResponse(url="/admin/knowledge", status_code=303)

# --- TEST LLM CONNECTION ---
class LLMConnectionTest(BaseModel):
    model: str
    base_url: str = None
    api_key: str = None

@router.post("/api/test-llm-connection")
async def test_llm_connection_api(
    data: LLMConnectionTest,
    user: User = Depends(get_current_user)
):
    from app.core.llm import chat
    messages = [{"role": "user", "content": "Ping. Reply only with 'Pong'."}]
    try:
        from app.config import normalize_lmstudio_url
        base_url = normalize_lmstudio_url(data.base_url.strip()) if data.base_url and data.base_url.strip() else None
        api_key = data.api_key.strip() if data.api_key and data.api_key.strip() else None
        model = data.model.strip() if data.model and data.model.strip() else "gemma-4"
        
        response = await chat(messages, model=model, temperature=0.1, max_tokens=10, base_url=base_url, api_key=api_key, raise_error=True)
        return {"status": "success", "message": f"Успішне підключення! Відповідь моделі: {response}"}
    except Exception as e:
        return {"status": "error", "message": f"Помилка: {type(e).__name__} - {str(e)}"}

class FetchModelsRequest(BaseModel):
    base_url: str
    api_key: str = None

@router.post("/api/fetch-models")
async def fetch_models_api(
    data: FetchModelsRequest,
    user: User = Depends(get_current_user)
):
    import httpx
    try:
        from app.config import normalize_lmstudio_url
        base_url = normalize_lmstudio_url(data.base_url.strip()) if data.base_url else ""
        if not base_url:
            from app.config import settings
            base_url = normalize_lmstudio_url(settings.LMSTUDIO_URL)
            
        # Ensure it ends with /v1 if missing (standard for OpenAI compatible APIs)
        if base_url.endswith("/"):
            base_url = base_url[:-1]
            
        url = f"{base_url}/models"
        
        headers = {}
        if data.api_key and data.api_key.strip():
            headers["Authorization"] = f"Bearer {data.api_key.strip()}"
            
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(url, headers=headers)
            resp.raise_for_status()
            models_data = resp.json()
            
        return {"status": "success", "data": models_data.get("data", [])}
    except Exception as e:
        return {"status": "error", "message": str(e)}

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
    import logging
    from app.database import async_session_maker
    
    logger = logging.getLogger("TEST_CHAT")
    logger.setLevel(logging.INFO)
    
    if not tenant_id:
        logger.error("No tenant selected in test_chat_api")
        return {"response": "Помилка: не вибрано тенант", "debug_trace": []}
        
    # The sandbox runs the REAL pipeline (the same one Telegram uses) and streams
    # its trace events live. No duplicated logic — what you see here is exactly
    # what happens in production channels.
    async def event_generator():
        import asyncio
        from app.core.pipeline import process_message_pipeline

        queue: asyncio.Queue = asyncio.Queue()

        def trace(step, status, details, duration="-"):
            logger.info(f"[{step}] {status} | {details}")
            queue.put_nowait({
                "type": "trace",
                "step": step,
                "status": status,
                "details": details,
                "time": str(duration)
            })

        async def run_pipeline():
            try:
                async with async_session_maker() as db:
                    from app.config import settings as global_settings
                    res = await db.execute(select(BotSetting).where(BotSetting.tenant_id == tenant_id))
                    settings = res.scalars().first()
                    from app.config import normalize_lmstudio_url
                    raw_base = normalize_lmstudio_url(settings.meta.get("llm_base_url")) if settings and settings.meta else ""
                    base_url_info = raw_base if raw_base else f"{global_settings.LMSTUDIO_URL} (Локальна мережа/Дефолт)"
                    model_info = settings.llm_model if settings and settings.llm_model else "gemma-4"
                    trace("СИСТЕМА (КОНФІГ)", "Ініціалізація", f"Сервер LLM: {base_url_info}\nМодель LLM: {model_info}")
                    trace("RAW REQUEST", "Відправлено", f"Вхідний текст клієнта:\n'{msg.text}'\nІсторія ({len(msg.history)} повідомлень)")

                    response_text = await process_message_pipeline(
                        msg.text, msg.history, tenant_id, db, trace=trace
                    )
                    queue.put_nowait({"type": "token", "content": response_text})
            except Exception as e:
                logger.exception("Sandbox pipeline error")
                queue.put_nowait({"type": "trace", "step": "PIPELINE", "status": "Помилка", "details": str(e), "time": "-"})
                queue.put_nowait({"type": "token", "content": "Помилка обробки запиту."})
            finally:
                queue.put_nowait(None)  # sentinel: stream finished

        task = asyncio.create_task(run_pipeline())
        try:
            while True:
                event = await queue.get()
                if event is None:
                    break
                yield f"data: {json.dumps(event)}\n\n"
        finally:
            if not task.done():
                task.cancel()
    return StreamingResponse(event_generator(), media_type="text/event-stream")
