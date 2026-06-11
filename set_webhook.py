import httpx
import sys

def set_webhook(token: str, webhook_url: str):
    """
    Sets the Telegram webhook for the given bot token.
    """
    api_url = f"https://api.telegram.org/bot{token}/setWebhook"
    response = httpx.post(api_url, json={"url": webhook_url})
    print(response.json())

if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("Usage: python set_webhook.py <bot_token> <ngrok_url>")
        print("Example: python set_webhook.py 123:ABC https://xyz.ngrok.io/webhook/telegram/<channel_uuid>")
        sys.exit(1)
        
    set_webhook(sys.argv[1], sys.argv[2])
