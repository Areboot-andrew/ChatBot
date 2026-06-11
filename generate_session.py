from telethon.sync import TelegramClient
from telethon.sessions import StringSession
import asyncio

print("=== Генератор сесії для Telegram UserBot ===")
print("Отримайте API_ID та API_HASH на сайті https://my.telegram.org")
api_id = input("Введіть API_ID: ")
api_hash = input("Введіть API_HASH: ")

with TelegramClient(StringSession(), api_id, api_hash) as client:
    session_string = client.session.save()
    print("\n\n" + "="*50)
    print("ВАШ SESSION STRING (збережіть його в таємниці!):")
    print("="*50)
    print(session_string)
    print("="*50 + "\n")
    print("Скопіюйте цей текст та вставте в поле 'Session String' в Адмінці.")
