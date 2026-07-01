import json
import os

DB_FILE = "users_data.json"

def load_db():
    if not os.path.exists(DB_FILE):
        return {}
    with open(DB_FILE, "r") as f:
        try:
            return json.load(f)
        except:
            return {}

def save_db(data):
    with open(DB_FILE, "w") as f:
        json.dump(data, f, indent=4)

def get_user(user_id):
    data = load_db()
    return data.get(str(user_id), {})

def update_user(user_id, **kwargs):
    data = load_db()
    user_id_str = str(user_id)
    if user_id_str not in data:
        data[user_id_str] = {
            "groups": [],
            "interval": 60,
            "auto_message": "",
            "auto_message_id": None,
            "has_media": False,
            "is_forward": False,
            "status": "stopped",
            "folder_id": None
        }
    data[user_id_str].update(kwargs)
    save_db(data)
