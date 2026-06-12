"""
Webchat channel: embeddable chat widget for tenant sites (spec §8.3).

Each site = a `webchat` channel row with allowed origins. The site embeds:
    <script src="https://<PUBLIC_BASE_URL>/widget.js" data-channel="<channel_id>" defer></script>

The widget talks REST:
    GET  /webchat/{channel_id}/config   -> greeting, title
    POST /webchat/{channel_id}/chat     -> {session_id, text} -> {response}

Origin is checked against the channel's allowed_origins (comma-separated,
empty = allow any origin).
"""
import logging
import uuid

from fastapi import APIRouter, Request, HTTPException
from fastapi.responses import JSONResponse, Response

from app.config import settings
from app.models.channel import Channel

logger = logging.getLogger(__name__)

router = APIRouter(tags=["webchat"])


def _allowed_origin(origin: str, channel: Channel) -> bool:
    allowed = (channel.credentials or {}).get("allowed_origins", "").strip()
    if not allowed or allowed == "*":
        return True
    if not origin:
        return False
    origin_host = origin.replace("https://", "").replace("http://", "").split("/")[0].lower()
    for entry in allowed.split(","):
        host = entry.strip().replace("https://", "").replace("http://", "").split("/")[0].lower()
        if host and (origin_host == host or origin_host.endswith("." + host)):
            return True
    return False


def _cors_headers(origin: str) -> dict:
    return {
        "Access-Control-Allow-Origin": origin or "*",
        "Access-Control-Allow-Methods": "POST, GET, OPTIONS",
        "Access-Control-Allow-Headers": "Content-Type",
    }


async def _get_webchat_channel(channel_id: uuid.UUID, db) -> Channel:
    channel = await db.get(Channel, channel_id)
    if not channel or channel.type != "webchat" or not channel.enabled:
        raise HTTPException(status_code=404, detail="Channel not found")
    return channel


@router.options("/webchat/{channel_id}/chat")
async def webchat_preflight(channel_id: uuid.UUID, request: Request):
    return Response(status_code=204, headers=_cors_headers(request.headers.get("origin", "*")))


@router.get("/webchat/{channel_id}/config")
async def webchat_config(channel_id: uuid.UUID, request: Request):
    from app.database import async_session_maker
    origin = request.headers.get("origin", "")
    async with async_session_maker() as db:
        channel = await _get_webchat_channel(channel_id, db)
        if not _allowed_origin(origin, channel):
            raise HTTPException(status_code=403, detail="Origin not allowed")
        return JSONResponse(
            {"greeting": channel.greeting or "Вітаю! Чим можу допомогти?", "title": channel.name or "Чат"},
            headers=_cors_headers(origin),
        )


@router.post("/webchat/{channel_id}/chat")
async def webchat_message(channel_id: uuid.UUID, request: Request):
    from app.database import async_session_maker
    from app.core.history import HistoryManager
    from app.core.pipeline import process_message_pipeline

    origin = request.headers.get("origin", "")
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON")

    text = str(body.get("text", "")).strip()
    session_id = str(body.get("session_id", "")).strip()[:64]
    if not text or not session_id:
        raise HTTPException(status_code=400, detail="text and session_id required")
    text = text[:2000]

    async with async_session_maker() as db:
        channel = await _get_webchat_channel(channel_id, db)
        if not _allowed_origin(origin, channel):
            raise HTTPException(status_code=403, detail="Origin not allowed")

        hist_channel = f"webchat:{channel_id}"
        history = await HistoryManager.get_history(hist_channel, session_id)
        history = history + [{"role": "user", "content": text}]

        response_text = await process_message_pipeline(
            text, history, channel.tenant_id, db,
            chat_key=f"{hist_channel}:{session_id}",
        )

    await HistoryManager.add_message(hist_channel, session_id, "user", text)
    await HistoryManager.add_message(hist_channel, session_id, "assistant", response_text)

    return JSONResponse({"response": response_text}, headers=_cors_headers(origin))


# --- Embeddable widget script ---

WIDGET_JS = r"""
(function(){
  var script = document.currentScript;
  var CHANNEL = script.getAttribute('data-channel');
  var BASE = '{BASE_URL}';
  if (!CHANNEL) { console.error('chat-widget: data-channel missing'); return; }

  var sid = localStorage.getItem('cw_sid_' + CHANNEL);
  if (!sid) { sid = 'w' + Date.now() + Math.random().toString(36).slice(2, 10); localStorage.setItem('cw_sid_' + CHANNEL, sid); }

  var css = document.createElement('style');
  css.textContent =
    '.cwx-btn{position:fixed;bottom:20px;right:20px;width:56px;height:56px;border-radius:50%;background:#2563eb;color:#fff;border:none;cursor:pointer;font-size:24px;box-shadow:0 4px 14px rgba(0,0,0,.35);z-index:99998}' +
    '.cwx-box{position:fixed;bottom:90px;right:20px;width:340px;max-width:92vw;height:480px;max-height:75vh;background:#111827;border:1px solid #374151;border-radius:14px;display:none;flex-direction:column;overflow:hidden;z-index:99999;font-family:system-ui,sans-serif;box-shadow:0 10px 40px rgba(0,0,0,.5)}' +
    '.cwx-head{background:#1f2937;color:#f9fafb;padding:12px 16px;font-size:14px;font-weight:600}' +
    '.cwx-msgs{flex:1;overflow-y:auto;padding:12px;display:flex;flex-direction:column;gap:8px}' +
    '.cwx-m{max-width:85%;padding:8px 12px;border-radius:12px;font-size:13px;line-height:1.45;white-space:pre-wrap;word-break:break-word}' +
    '.cwx-m.u{align-self:flex-end;background:#2563eb;color:#fff;border-bottom-right-radius:4px}' +
    '.cwx-m.a{align-self:flex-start;background:#1f2937;color:#e5e7eb;border-bottom-left-radius:4px}' +
    '.cwx-in{display:flex;border-top:1px solid #374151;background:#111827}' +
    '.cwx-in input{flex:1;border:none;background:transparent;color:#e5e7eb;padding:12px;font-size:13px;outline:none}' +
    '.cwx-in button{border:none;background:transparent;color:#3b82f6;padding:0 14px;cursor:pointer;font-size:18px}' +
    '.cwx-typing{align-self:flex-start;color:#9ca3af;font-size:12px;padding:4px 12px}';
  document.head.appendChild(css);

  var btn = document.createElement('button'); btn.className = 'cwx-btn'; btn.innerHTML = '💬';
  var box = document.createElement('div'); box.className = 'cwx-box';
  box.innerHTML = '<div class="cwx-head">Чат</div><div class="cwx-msgs"></div>' +
    '<div class="cwx-in"><input type="text" placeholder="Ваше запитання..." maxlength="2000"><button>➤</button></div>';
  document.body.appendChild(btn); document.body.appendChild(box);

  var msgs = box.querySelector('.cwx-msgs');
  var input = box.querySelector('input');
  var send = box.querySelector('.cwx-in button');
  var head = box.querySelector('.cwx-head');
  var greeted = false;

  function add(role, text){
    var d = document.createElement('div');
    d.className = 'cwx-m ' + (role === 'user' ? 'u' : 'a');
    d.textContent = text;
    msgs.appendChild(d); msgs.scrollTop = msgs.scrollHeight;
    return d;
  }

  btn.onclick = function(){
    box.style.display = (box.style.display === 'flex') ? 'none' : 'flex';
    if (box.style.display === 'flex' && !greeted) {
      greeted = true;
      fetch(BASE + '/webchat/' + CHANNEL + '/config')
        .then(function(r){ return r.json(); })
        .then(function(c){ head.textContent = c.title || 'Чат'; add('assistant', c.greeting); })
        .catch(function(){ add('assistant', 'Вітаю! Чим можу допомогти?'); });
      input.focus();
    }
  };

  var busy = false;
  function submit(){
    var text = input.value.trim();
    if (!text || busy) return;
    busy = true;
    input.value = '';
    add('user', text);
    var t = document.createElement('div'); t.className = 'cwx-typing'; t.textContent = 'друкує…';
    msgs.appendChild(t); msgs.scrollTop = msgs.scrollHeight;
    fetch(BASE + '/webchat/' + CHANNEL + '/chat', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({ session_id: sid, text: text })
    })
    .then(function(r){ if(!r.ok) throw new Error(r.status); return r.json(); })
    .then(function(d){ t.remove(); add('assistant', d.response); })
    .catch(function(){ t.remove(); add('assistant', 'Технічна помилка, спробуйте ще раз.'); })
    .finally(function(){ busy = false; });
  }
  send.onclick = submit;
  input.addEventListener('keydown', function(e){ if (e.key === 'Enter') submit(); });
})();
"""


@router.get("/widget.js")
async def widget_js():
    js = WIDGET_JS.replace("{BASE_URL}", settings.PUBLIC_BASE_URL.rstrip("/"))
    return Response(js, media_type="application/javascript",
                    headers={"Cache-Control": "public, max-age=3600"})
