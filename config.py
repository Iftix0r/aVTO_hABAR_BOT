import os
from dotenv import load_dotenv

load_dotenv()

API_ID = int(os.environ.get("API_ID", "123456"))
API_HASH = os.environ.get("API_HASH", "test_hash")
BOT_TOKEN = os.environ.get("BOT_TOKEN", "test_token")
