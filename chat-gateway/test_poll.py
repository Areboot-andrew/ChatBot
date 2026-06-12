import asyncio
from openai import AsyncOpenAI

async def main():
    client = AsyncOpenAI(base_url='https://text.pollinations.ai/openai', api_key='not-needed')
    try:
        res = await client.chat.completions.create(model='openai', messages=[{'role': 'user', 'content': 'hello'}])
        print(res.choices[0].message.content)
    except Exception as e:
        print("Error:", e)

if __name__ == "__main__":
    asyncio.run(main())
