import uuid
import os
import json
from flask import Flask, request, abort
from urllib.parse import parse_qs

# ============ 1. 伺服器初始化 ============
# 這裡建立名為 'app' 的變數，讓 Render 的 Gunicorn 能夠找到它
app = Flask(__name__)

# ============ 2. 假資料庫類別 (FakeDB) ============
class FakeDB:
    def __init__(self):
        self.collections = {
            'items': {},      # id -> dict
            'sessions': {},   # user_id -> dict
            'users': {},
            'matches': {}
        }
    def collection(self, name):
        return FakeCollection(self, name)

class FakeCollection:
    def __init__(self, db, name):
        self.db = db
        self.name = name
        self._filters = []
        self._limit = None
    def document(self, doc_id):
        return FakeDoc(self.db, self.name, doc_id)
    def where(self, field, op, value):
        new = FakeCollection(self.db, self.name)
        new._filters = self._filters + [(field, op, value)]
        new._limit = self._limit
        return new
    def limit(self, n):
        new = FakeCollection(self.db, self.name)
        new._filters = self._filters
        new._limit = n
        return new
    def stream(self):
        results = []
        for doc_id, data in self.db.collections[self.name].items():
            ok = True
            for field, op, value in self._filters:
                if op == '==' and data.get(field) != value:
                    ok = False
                    break
            if ok:
                results.append(FakeSnapshot(doc_id, data))
        if self._limit:
            results = results[:self._limit]
        return iter(results)
    def add(self, data):
        new_id = str(uuid.uuid4())[:8]
        self.db.collections[self.name][new_id] = data
        return (None, FakeDoc(self.db, self.name, new_id))

class FakeDoc:
    def __init__(self, db, collection_name, doc_id):
        self.db = db
        self.collection_name = collection_name
        self.id = doc_id
    def get(self):
        data = self.db.collections[self.collection_name].get(self.id)
        return FakeSnapshot(self.id, data) if data is not None else FakeSnapshot(self.id, None)
    def set(self, data):
        self.db.collections[self.collection_name][self.id] = data
    def update(self, data):
        if self.id in self.db.collections[self.collection_name]:
            self.db.collections[self.collection_name][self.id].update(data)
    def delete(self):
        self.db.collections[self.collection_name].pop(self.id, None)

class FakeSnapshot:
    def __init__(self, doc_id, data):
        self.id = doc_id
        self._data = data
    @property
    def exists(self):
        return self._data is not None
    def to_dict(self):
        return self._data

# 初始化資料庫實例
db = FakeDB()

def seed_data():
    sample_items = [
        {'userId': 'u1', 'type': 'lost', 'category': '電子產品', 'description': '黑色 iPhone 14', 'location': '圖書館一樓', 'status': 'open'},
        {'userId': 'u2', 'type': 'found', 'category': '鑰匙', 'description': '一串鑰匙', 'location': '學生餐廳', 'status': 'open'},
    ]
    for item in sample_items:
        db.collection('items').add(item)

seed_data()

# ============ 3. LINE 訊息類別與實體 ============
class TextSendMessage:
    def __init__(self, text):
        self.text = text

class FlexSendMessage:
    def __init__(self, alt_text, contents):
        self.alt_text = alt_text
        self.contents = contents

class LineBotApi:
    """這裡您之後可以替換為真正的 linebot.models"""
    def reply_message(self, reply_token, messages):
        # 在 Render 日誌中印出回覆內容以便除錯
        print(f">>> Reply to {reply_token}: Sending messages...")

line_bot_api = LineBotApi()

# ============ 4. 業務邏輯函式 ============
CATEGORIES = {"電子產品", "衣服", "鞋子", "證件", "錢包", "雨傘", "書籍", "其他", "配飾"}

def get_session(user_id):
    doc = db.collection('sessions').document(user_id).get()
    return doc.to_dict() if doc.exists else {}

def set_session(user_id, data):
    db.collection('sessions').document(user_id).set(data)

def clear_session(user_id):
    db.collection('sessions').document(user_id).delete()

def get_location_flex(item_type):
    filename = 'find_place.json' if item_type == 'found' else 'lost_place.json'
    file_path = os.path.join(os.path.dirname(__file__), filename)
    with open(file_path, 'r', encoding='utf-8') as f:
        contents = json.load(f)
    return FlexSendMessage(alt_text="請選擇地點", contents=contents)

def get_photo_flex():
    file_path = os.path.join(os.path.dirname(__file__), 'photo.json')
    with open(file_path, 'r', encoding='utf-8') as f:
        contents = json.load(f)
    return FlexSendMessage(alt_text="請上傳照片或略過", contents=contents)

def get_main_menu():
    # ... (保留您原本的 get_main_menu 內容)
    flex_content = {
        "type": "bubble",
        "body": {
            "type": "box", "layout": "vertical", "spacing": "md",
            "contents": [
                {"type": "text", "text": "🔍 失物招領", "weight": "bold", "size": "xl", "align": "center"},
                {"type": "button", "style": "primary", "color": "#4CAF50", "action": {"type": "message", "label": "📦 我撿到東西了", "text": "我撿到東西了"}},
                {"type": "button", "style": "primary", "color": "#2196F3", "action": {"type": "message", "label": "🔎 我在找東西", "text": "我在找東西"}},
            ]
        }
    }
    return FlexSendMessage(alt_text="失物招領選單", contents=flex_content)

def get_category_menu():
    # ... (保留您原本的 get_category_menu 內容)
    return FlexSendMessage(alt_text="請選擇種類", contents={"type": "bubble", "body": {"type": "box", "layout": "vertical", "contents": [{"type": "text", "text": "請選擇分類"}]}})

# ============ 5. 訊息處理邏輯 ============
def handle_message(user_id, text, reply_token):
    text = text.strip()
    session = get_session(user_id)
    step = session.get("step")

    if text in ["選單", "開始", "取消"]:
        clear_session(user_id)
        line_bot_api.reply_message(reply_token, get_main_menu())
        return

    if text == "我撿到東西了":
        set_session(user_id, {"type": "found", "step": "wait_category"})
        line_bot_api.reply_message(reply_token, get_category_menu())
    # ... 這裡繼續放入您原本 handle_message 的其他 elif 邏輯 ...

def handle_postback(user_id, data, reply_token):
    params = parse_qs(data)
    action = params.get('action', [''])[0]
    session = get_session(user_id)
    # ... 這裡放入您原本 handle_postback 的邏輯 ...

# ============ 6. Flask Webhook 入口 ============
@app.route("/", methods=['GET'])
def index():
    return "NTPU Lost and Found Bot is running!"

@app.route("/callback", methods=['POST'])
def callback():
    body = request.get_data(as_text=True)
    payload = json.loads(body)

    for event in payload.get('events', []):
        reply_token = event.get('replyToken')
        user_id = event['source']['userId']

        if event['type'] == 'message' and event['message']['type'] == 'text':
            handle_message(user_id, event['message']['text'], reply_token)
        elif event['type'] == 'postback':
            handle_postback(user_id, event['postback']['data'], reply_token)

    return 'OK'

if __name__ == "__main__":
    # 這裡本地測試用，Render 會透過 Gunicorn 執行
    port = int(os.environ.get('PORT', 10000))
    app.run(host='0.0.0.0', port=port)
