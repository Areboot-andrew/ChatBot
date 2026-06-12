from fastapi import APIRouter, Request, Depends, HTTPException, BackgroundTasks
from sqlalchemy.ext.asyncio import AsyncSession
from aiogram import Bot, Dispatcher, types
from aiogram.enums import ParseMode
from aiogram.client.default import DefaultBotProperties
from app.database import get_db
from app.models.channel import Channel
from app.core.pipeline import process_message_pipeline
import uuid
import logging

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/webhook/telegram", tags=["telegram"])

async def handle_telegram_update(channel_id: uuid.UUID, update_data: dict):
    """
    Background task to process telegram updates to immediately return 200 OK to Telegram.
    """
    from app.database import async_session_maker
    async with async_session_maker() as db:
        channel = await db.get(Channel, channel_id)
        if not channel or channel.type != 'telegram' or not channel.enabled:
            return
            
        token = channel.credentials.get("token")
        if not token:
            return
            
        bot = Bot(token=token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
        update = types.Update(**update_data)
        
        try:
            if update.message and update.message.text:
                text = update.message.text
                chat_id = update.message.chat.id
                tenant_id = channel.tenant_id

                # Fetch history from Redis
                from app.core.history import HistoryManager
                history = await HistoryManager.get_history("telegram", str(chat_id))

                # Keep "typing..." alive while the agent works (the status expires
                # after ~5s, agent loops can take much longer).
                import asyncio
                async def _keep_typing():
                    try:
                        while True:
                            await bot.send_chat_action(chat_id=chat_id, action="typing")
                            await asyncio.sleep(4)
                    except asyncio.CancelledError:
                        pass
                typing_task = asyncio.create_task(_keep_typing())

                # Process through core pipeline (chat_key enables agent memory)
                try:
                    response_text = await process_message_pipeline(
                        text, history, tenant_id, db,
                        chat_key=f"telegram:{channel_id}:{chat_id}"
                    )
                finally:
                    typing_task.cancel()
                
                # Update history
                await HistoryManager.add_message("telegram", str(chat_id), "user", text)
                await HistoryManager.add_message("telegram", str(chat_id), "assistant", response_text)
                
                await bot.send_message(chat_id=chat_id, text=response_text)
                
        except Exception as e:
            logger.error(f"Error processing telegram update: {e}")
        finally:
            await bot.session.close()

@router.post("/{channel_id}")
async def telegram_webhook(
    channel_id: uuid.UUID,
    request: Request,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db)
):
    """
    Webhook entry point for Telegram.
    """
    channel = await db.get(Channel, channel_id)
    if not channel or channel.type != 'telegram' or not channel.enabled:
        raise HTTPException(status_code=404, detail="Channel not found")
        
    update_data = await request.json()
    
    # Process the update in the background so Telegram gets 200 OK fast
    background_tasks.add_task(handle_telegram_update, channel_id, update_data)
    
    return {"status": "ok"}
