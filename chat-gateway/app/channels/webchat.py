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


@router.options("/webchat/{channel_id}/reset")
async def webchat_reset_preflight(channel_id: uuid.UUID, request: Request):
    return Response(status_code=204, headers=_cors_headers(request.headers.get("origin", "*")))


@router.post("/webchat/{channel_id}/reset")
async def webchat_reset(channel_id: uuid.UUID, request: Request):
    """Clear history + agent memory for a session (button 'Новий чат')."""
    from app.core.history import HistoryManager, MemoryManager
    origin = request.headers.get("origin", "")
    try:
        body = await request.json()
    except Exception:
        body = {}
    session_id = str(body.get("session_id", "")).strip()[:64]
    if session_id:
        hist_channel = f"webchat:{channel_id}"
        await HistoryManager.clear_history(hist_channel, session_id)
        await MemoryManager.save_memory(f"{hist_channel}:{session_id}", {})
    return JSONResponse({"ok": True}, headers=_cors_headers(origin))


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

        from app.core.transcript import make_live_trace, publish_live_message, log_message
        # Stream every step to the admin live feed in real time (not after the
        # turn). The collected steps are still persisted for the archive.
        publish_live_message(channel.tenant_id, session_id, "webchat", "user", text)
        collect, steps = make_live_trace(channel.tenant_id, session_id, "webchat")
        response_text = await process_message_pipeline(
            text, history, channel.tenant_id, db,
            chat_key=f"{hist_channel}:{session_id}", trace=collect,
        )
        await log_message(db, channel.tenant_id, channel.id, session_id, "user", text)
        if response_text:
            publish_live_message(channel.tenant_id, session_id, "webchat", "assistant", response_text)
            await log_message(db, channel.tenant_id, channel.id, session_id, "assistant", response_text, meta={"trace": steps})

    await HistoryManager.add_message(hist_channel, session_id, "user", text)
    if response_text:
        await HistoryManager.add_message(hist_channel, session_id, "assistant", response_text)

    return JSONResponse({"response": response_text}, headers=_cors_headers(origin))


# --- Embeddable widget script ---

WIDGET_JS = r"""
(function(){
  var script = document.currentScript;
  var CHANNEL = script.getAttribute('data-channel');
  var BASE = '{BASE_URL}';
  if (!CHANNEL) { console.error('chat-widget: data-channel missing'); return; }

  // Fresh session on every page load / reload — the id lives only in this page
  // instance, so reloading or revisiting starts a clean chat (no old history).
  var sid = 'w' + Date.now() + Math.random().toString(36).slice(2, 10);

  var FONT = '-apple-system,BlinkMacSystemFont,"SF Pro Text","Segoe UI",Roboto,Helvetica,Arial,sans-serif';
  var css = document.createElement('style');
  css.textContent =
    '.cwx-btn{position:fixed;bottom:22px;right:22px;width:60px;height:60px;border-radius:50%;background:linear-gradient(180deg,#34aadc,#007aff);color:#fff;border:none;cursor:pointer;font-size:27px;box-shadow:0 6px 20px rgba(0,90,200,.45);z-index:99998;transition:transform .15s}' +
    '.cwx-btn:hover{transform:scale(1.06)}' +
    '.cwx-box{position:fixed;bottom:96px;right:22px;width:368px;max-width:92vw;height:560px;max-height:78vh;background:#fff;border-radius:22px;display:none;flex-direction:column;overflow:hidden;z-index:99999;font-family:' + FONT + ';box-shadow:0 18px 50px rgba(0,0,0,.28);animation:cwxpop .22s ease}' +
    '@keyframes cwxpop{from{opacity:0;transform:translateY(12px) scale(.98)}to{opacity:1;transform:none}}' +
    '.cwx-head{display:flex;align-items:center;gap:10px;padding:12px 14px;background:rgba(250,250,252,.86);backdrop-filter:saturate(180%) blur(20px);border-bottom:1px solid rgba(0,0,0,.08)}' +
    '.cwx-ava{width:34px;height:34px;border-radius:50%;background:linear-gradient(180deg,#34aadc,#007aff);display:flex;align-items:center;justify-content:center;font-size:18px;flex:0 0 auto}' +
    '.cwx-htxt{display:flex;flex-direction:column;line-height:1.1;flex:1;min-width:0}' +
    '.cwx-title{font-size:15px;font-weight:600;color:#000;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}' +
    '.cwx-sub{font-size:11px;color:#8e8e93}' +
    '.cwx-head .cwx-close{background:none;border:none;color:#8e8e93;font-size:26px;line-height:1;cursor:pointer;padding:0 4px}' +
    '.cwx-msgs{flex:1;overflow-y:auto;padding:14px 12px;display:flex;flex-direction:column;gap:3px;background:#fff}' +
    '.cwx-m{position:relative;max-width:74%;padding:8px 14px;border-radius:20px;font-size:16px;line-height:1.32;white-space:pre-wrap;word-wrap:break-word;animation:cwxin .18s ease;margin-top:5px}' +
    '@keyframes cwxin{from{opacity:0;transform:translateY(6px) scale(.96)}to{opacity:1;transform:none}}' +
    '.cwx-m.u{align-self:flex-end;background:linear-gradient(180deg,#28a0ff,#007aff);color:#fff;border-bottom-right-radius:7px}' +
    '.cwx-m.a{align-self:flex-start;background:#e9e9eb;color:#000;border-bottom-left-radius:7px}' +
    '.cwx-typing{align-self:flex-start;background:#e9e9eb;border-radius:20px;border-bottom-left-radius:7px;padding:12px 14px;margin-top:5px;display:flex;gap:4px}' +
    '.cwx-typing span{width:8px;height:8px;border-radius:50%;background:#9b9ba0;display:inline-block;animation:cwxdot 1.3s infinite}' +
    '.cwx-typing span:nth-child(2){animation-delay:.18s}.cwx-typing span:nth-child(3){animation-delay:.36s}' +
    '@keyframes cwxdot{0%,60%,100%{opacity:.35;transform:translateY(0)}30%{opacity:1;transform:translateY(-3px)}}' +
    '.cwx-in{display:flex;align-items:center;gap:8px;padding:9px 10px;background:rgba(250,250,252,.92);border-top:1px solid rgba(0,0,0,.08)}' +
    '.cwx-in input{flex:1;min-width:0;border:1px solid #d6d6db;background:#fff;color:#000;padding:10px 14px;font-size:16px;outline:none;border-radius:20px;font-family:' + FONT + '}' +
    '.cwx-in input:focus{border-color:#007aff}' +
    '.cwx-in button{flex:0 0 auto;width:34px;height:34px;border:none;background:linear-gradient(180deg,#28a0ff,#007aff);color:#fff;cursor:pointer;font-size:18px;border-radius:50%;display:flex;align-items:center;justify-content:center}' +
    '.cwx-in button:disabled{background:#c7c7cc}' +
    '@media (max-width:480px){.cwx-box{top:0;left:0;right:0;bottom:0;width:100%;max-width:100%;height:100%;height:100dvh;max-height:none;border-radius:0}.cwx-btn{bottom:16px;right:16px}}';
  document.head.appendChild(css);

  var btn = document.createElement('button'); btn.className = 'cwx-btn'; btn.innerHTML = '💬';
  var box = document.createElement('div'); box.className = 'cwx-box';
  box.innerHTML =
    '<div class="cwx-head"><div class="cwx-ava">💬</div>' +
    '<div class="cwx-htxt"><span class="cwx-title">Чат</span><span class="cwx-sub">зазвичай відповідає швидко</span></div>' +
    '<button type="button" class="cwx-close" aria-label="Закрити">&times;</button></div>' +
    '<div class="cwx-msgs"></div>' +
    '<div class="cwx-in"><input type="text" placeholder="Повідомлення" maxlength="2000"><button aria-label="Надіслати">&#8593;</button></div>';
  document.body.appendChild(btn); document.body.appendChild(box);

  var msgs = box.querySelector('.cwx-msgs');
  var input = box.querySelector('input');
  var send = box.querySelector('.cwx-in button');
  var head = box.querySelector('.cwx-title');
  box.querySelector('.cwx-close').onclick = function(){ box.style.display = 'none'; };
  var greeted = false;

  function esc(s){ return s.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;'); }
  function linkify(s){
    return esc(s).replace(/(https?:\/\/[^\s<]+)/g, function(u){
      var clean = u.replace(/[.,;:)]+$/,'');
      return '<a href="'+clean+'" target="_blank" rel="noopener" style="color:inherit;text-decoration:underline">'+clean+'</a>';
    });
  }
  function add(role, text){
    var d = document.createElement('div');
    d.className = 'cwx-m ' + (role === 'user' ? 'u' : 'a');
    if (role === 'user') { d.textContent = text; } else { d.innerHTML = linkify(text); }
    msgs.appendChild(d); msgs.scrollTop = msgs.scrollHeight;
    return d;
  }

  btn.onclick = function(){
    box.style.display = (box.style.display === 'flex') ? 'none' : 'flex';
    if (box.style.display === 'flex' && !greeted) {
      greeted = true;
      fetch(BASE + '/webchat/' + CHANNEL + '/config')
        .then(function(r){ return r.json(); })
        .then(function(c){ if(c.title) head.textContent = c.title; add('assistant', c.greeting || 'Вітаю! 👋 Чим можу допомогти?'); })
        .catch(function(){ add('assistant', 'Вітаю! 👋 Чим можу допомогти?'); });
      setTimeout(function(){ input.focus(); }, 100);
    }
  };

  var busy = false;
  function submit(){
    var text = input.value.trim();
    if (!text || busy) return;
    busy = true; send.disabled = true;
    input.value = '';
    add('user', text);
    var t = document.createElement('div'); t.className = 'cwx-typing';
    t.innerHTML = '<span></span><span></span><span></span>';
    msgs.appendChild(t); msgs.scrollTop = msgs.scrollHeight;
    fetch(BASE + '/webchat/' + CHANNEL + '/chat', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({ session_id: sid, text: text })
    })
    .then(function(r){ if(!r.ok) throw new Error(r.status); return r.json(); })
    .then(function(d){ t.remove(); add('assistant', d.response); })
    .catch(function(){ t.remove(); add('assistant', 'Технічна помилка, спробуйте ще раз. 🙏'); })
    .finally(function(){ busy = false; send.disabled = false; input.focus(); });
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


# --- Inline form widget (rendered into a target element on the page) ---

INLINE_JS = r"""
(function(){
  var script = document.currentScript;
  var CHANNEL = script.getAttribute('data-channel');
  var TARGET = script.getAttribute('data-target') || 'texno-chat';
  var BASE = '{BASE_URL}';
  if (!CHANNEL) { console.error('chat-inline: data-channel missing'); return; }

  function init(){
    var root = document.getElementById(TARGET);
    if (!root) { console.error('chat-inline: target #' + TARGET + ' not found'); return; }

    // Fresh session on every page load / reload — clean chat each time.
    var sid = 'i' + Date.now() + Math.random().toString(36).slice(2, 10);

    var IFONT = '-apple-system,BlinkMacSystemFont,"SF Pro Text","Segoe UI",Roboto,Helvetica,Arial,sans-serif';
    var css = document.createElement('style');
    css.textContent =
      '.cwi{display:flex;flex-direction:column;border:1px solid rgba(0,0,0,.1);border-radius:20px;overflow:hidden;background:#fff;font-family:' + IFONT + ';height:460px;max-height:80vh;box-shadow:0 10px 34px rgba(0,0,0,.1)}' +
      '.cwi-msgs{flex:1;overflow-y:auto;padding:14px 12px;display:flex;flex-direction:column;gap:3px;background:#fff}' +
      '.cwi-m{max-width:76%;padding:8px 14px;border-radius:20px;font-size:16px;line-height:1.32;white-space:pre-wrap;word-wrap:break-word;margin-top:5px;animation:cwiin .18s ease}' +
      '@keyframes cwiin{from{opacity:0;transform:translateY(6px) scale(.96)}to{opacity:1;transform:none}}' +
      '.cwi-m.u{align-self:flex-end;background:linear-gradient(180deg,#28a0ff,#007aff);color:#fff;border-bottom-right-radius:7px}' +
      '.cwi-m.a{align-self:flex-start;background:#e9e9eb;color:#000;border-bottom-left-radius:7px}' +
      '.cwi-typing{align-self:flex-start;background:#e9e9eb;border-radius:20px;border-bottom-left-radius:7px;padding:12px 14px;margin-top:5px;display:flex;gap:4px}' +
      '.cwi-typing span{width:8px;height:8px;border-radius:50%;background:#9b9ba0;display:inline-block;animation:cwidot 1.3s infinite}' +
      '.cwi-typing span:nth-child(2){animation-delay:.18s}.cwi-typing span:nth-child(3){animation-delay:.36s}' +
      '@keyframes cwidot{0%,60%,100%{opacity:.35;transform:translateY(0)}30%{opacity:1;transform:translateY(-3px)}}' +
      '.cwi-in{display:flex;align-items:center;gap:8px;padding:10px;background:rgba(250,250,252,.95);border-top:1px solid rgba(0,0,0,.08)}' +
      '.cwi-in input{flex:1;min-width:0;border:1px solid #d6d6db;background:#fff;color:#000;padding:11px 15px;font-size:16px;outline:none;border-radius:20px;font-family:' + IFONT + '}' +
      '.cwi-in input:focus{border-color:#007aff}' +
      '.cwi-in button{flex:0 0 auto;width:36px;height:36px;border:none;background:linear-gradient(180deg,#28a0ff,#007aff);color:#fff;cursor:pointer;font-size:19px;border-radius:50%;display:flex;align-items:center;justify-content:center}' +
      '.cwi-in button:disabled{background:#c7c7cc}';
    document.head.appendChild(css);

    root.innerHTML = '<div class="cwi"><div class="cwi-msgs"></div>' +
      '<div class="cwi-in"><input type="text" placeholder="Повідомлення" maxlength="2000"><button aria-label="Надіслати">&#8593;</button></div></div>';

    var msgs = root.querySelector('.cwi-msgs');
    var input = root.querySelector('input');
    var send = root.querySelector('button');

    function esc(s){ return s.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;'); }
    function linkify(s){
      return esc(s).replace(/(https?:\/\/[^\s<]+)/g, function(u){
        var clean = u.replace(/[.,;:)]+$/,'');
        return '<a href="'+clean+'" target="_blank" rel="noopener" style="color:inherit;text-decoration:underline">'+clean+'</a>';
      });
    }
    function add(role, text){
      var d = document.createElement('div');
      d.className = 'cwi-m ' + (role === 'user' ? 'u' : 'a');
      if (role === 'user') { d.textContent = text; } else { d.innerHTML = linkify(text); }
      msgs.appendChild(d); msgs.scrollTop = msgs.scrollHeight;
    }

    fetch(BASE + '/webchat/' + CHANNEL + '/config')
      .then(function(r){ return r.json(); })
      .then(function(c){ if (c.greeting) add('assistant', c.greeting); })
      .catch(function(){});

    var busy = false;
    function submit(){
      var text = input.value.trim();
      if (!text || busy) return;
      busy = true; send.disabled = true;
      input.value = '';
      add('user', text);
      var t = document.createElement('div'); t.className = 'cwi-typing';
      t.innerHTML = '<span></span><span></span><span></span>';
      msgs.appendChild(t); msgs.scrollTop = msgs.scrollHeight;
      fetch(BASE + '/webchat/' + CHANNEL + '/chat', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({ session_id: sid, text: text })
      })
      .then(function(r){ if(!r.ok) throw new Error(r.status); return r.json(); })
      .then(function(d){ t.remove(); add('assistant', d.response); })
      .catch(function(){ t.remove(); add('assistant', 'Технічна помилка, спробуйте ще раз.'); })
      .finally(function(){ busy = false; send.disabled = false; input.focus(); });
    }
    send.onclick = submit;
    input.addEventListener('keydown', function(e){ if (e.key === 'Enter') submit(); });
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init);
  else init();
})();
"""


@router.get("/widget-inline.js")
async def widget_inline_js():
    js = INLINE_JS.replace("{BASE_URL}", settings.PUBLIC_BASE_URL.rstrip("/"))
    return Response(js, media_type="application/javascript",
                    headers={"Cache-Control": "public, max-age=3600"})
