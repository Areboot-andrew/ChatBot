from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import logging

from app.config import settings
from app.database import engine
from app.core.history import redis_client
from app.admin.routes import router as admin_router
from app.admin.seed import seed_admin
from fastapi.staticfiles import StaticFiles

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(title="Chat Gateway", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], # Should be restricted in production
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.on_event("startup")
async def startup_event():
    logger.info("Starting up Chat Gateway...")
    from app.database import init_qdrant_collections
    await init_qdrant_collections()
    await seed_admin()

@app.on_event("shutdown")
async def shutdown_event():
    logger.info("Shutting down Chat Gateway...")
    await engine.dispose()
    await redis_client.close()

@app.get("/health")
async def health_check():
    return {"status": "ok", "environment": settings.ENVIRONMENT}

from fastapi.responses import RedirectResponse

@app.get("/", include_in_schema=False)
async def root_redirect():
    return RedirectResponse(url="/admin/dashboard")

@app.get("/admin", include_in_schema=False)
async def admin_redirect():
    return RedirectResponse(url="/admin/dashboard")

class ChatRequest(BaseModel):
    chat_id: str
    text: str

@app.post("/api/v1/chat")
async def test_chat(request: ChatRequest):
    """
    Test endpoint. In a real scenario, this enqueues an arq task.
    For M1, we can call llm.chat directly to test the LLM connection.
    """
    from app.core.llm import chat
    
    response = await chat([{"role": "user", "content": request.text}])
    return {
        "status": "success",
        "chat_id": request.chat_id,
        "response": response
    }

from app.channels.telegram import router as telegram_router

app.include_router(admin_router)
app.include_router(telegram_router)

# Mount static files
import os
os.makedirs("app/admin/static", exist_ok=True)
app.mount("/static", StaticFiles(directory="app/admin/static"), name="static")
