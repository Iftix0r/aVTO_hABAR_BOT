import asyncio
from pyrogram import Client, idle
bot = Client("bot_session", api_id=123, api_hash="123", bot_token="123:abc")
async def main_loop():
    print("main loop ran")
if __name__ == "__main__":
    bot.run(main_loop())
