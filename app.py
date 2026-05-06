import uuid
import os
import json
from flask import Flask, request, abort
from urllib.parse import parse_qs

# 引入官方 SDK
from linebot import LineBotApi, WebhookHandler
from linebot.exceptions import InvalidSignatureError
from linebot.models import (
    MessageEvent, TextMessage, TextSendMessage, 
    FlexSendMessage, PostbackEvent
)

# ============ 1. 伺服器與 LINE SDK 初始化 ============
app = Flask(__name__)

# 從環境變數讀取金鑰 (請確保 Render 設定中已有這兩個變數)
line_bot_api = LineBotApi(os.getenv('LINE_CHANNEL_ACCESS_TOKEN'))
handler = WebhookHandler(os.getenv('LINE_CHANNEL_SECRET'))

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

db = FakeDB()

def seed_data():
    sample_items = [
        {'userId': 'u1', 'type': 'lost', 'category': '電子產品', 'description': '黑色 iPhone 14', 'location': '圖書館一樓', 'status': 'open'},
        {'userId': 'u2', 'type': 'found', 'category': '鑰匙', 'description': '一串鑰匙', 'location': '學生餐廳', 'status': 'open'},
    ]
    for item in sample_items:
        db.collection('items').add(item)

seed_data()

# ============ 3. 業務邏輯函式 ============
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
    flex_content = {
        "type": "bubble",
        "body": {
            "type": "box", "layout": "vertical", "spacing": "md",
            "contents": [
                {"type": "text", "text": "🔍 失物招領", "weight": "bold", "size": "xl", "align": "center"},
                {"type": "text", "text": "請選擇你的狀況", "size": "sm", "color": "#888888", "align": "center"},
                {"type": "button", "style": "primary", "color": "#4CAF50", "action": {"type": "message", "label": "📦 我撿到東西了", "text": "我撿到東西了"}},
                {"type": "button", "style": "primary", "color": "#2196F3", "action": {"type": "message", "label": "🔎 我在找東西", "text": "我在找東西"}},
                {"type": "button", "style": "primary", "color": "#FF9800", "action": {"type": "message", "label": "✅ 我找到了", "text": "我找到了"}},
                {"type": "button", "style": "secondary", "action": {"type": "message", "label": "📋 查看所有失物", "text": "查看所有失物"}},
            ]
        }
    }
    return FlexSendMessage(alt_text="失物招領選單", contents=flex_content)

# 這裡修正了原本只有一段文字的分類選單，讓它更完整
def get_category_menu():
    flex_content = {
        "type": "bubble",
        "size": "mega",
        "body": {
            "type": "box", "layout": "vertical",
            "contents": [
                {"type": "text", "text": "我撿到的種類", "weight": "bold", "size": "xl", "align": "center", "margin": "md"},
                {"type": "text", "text": "請選擇你的狀況", "size": "md", "color": "#888888", "align": "center", "margin": "md"},
                {"type": "box", "layout": "vertical", "spacing": "md", "margin": "xl",
                 "contents": [
                     {"type": "box", "layout": "horizontal", "spacing": "md", "contents": [
                         {"type": "button", "style": "secondary", "action": {"type": "message", "label": "電子產品", "text": "電子產品"}},
                         {"type": "button", "style": "secondary", "action": {"type": "message", "label": "衣服", "text": "衣服"}}
                     ]},
                     {"type": "box", "layout": "horizontal", "spacing": "md", "contents": [
                         {"type": "button", "style": "secondary", "action": {"type": "message", "label": "鞋子", "text": "鞋子"}},
                         {"type": "button", "style": "secondary", "action": {"type": "message", "label": "證件", "text": "證件"}}
                     ]},
                     {"type": "box", "layout": "horizontal", "spacing": "md", "contents": [
                         {"type": "button", "style": "secondary", "action": {"type": "message", "label": "錢包", "text": "錢包"}},
                         {"type": "button", "style": "secondary", "action": {"type": "message", "label": "雨傘", "text": "雨傘"}}
                     ]},
                     {"type": "box", "layout": "horizontal", "spacing": "md", "contents": [
                         {"type": "button", "style": "secondary", "action": {"type": "message", "label": "書籍", "text": "書籍"}},
                         {"type": "button", "style": "secondary", "action": {"type": "message", "label": "其他", "text": "其他"}}
                     ]},
                     {"type": "box", "layout": "horizontal",
                      "contents": [
                          {"type": "button", "style": "secondary", "action": {"type": "message", "label": "配飾 (耳環、項鍊、手鏈)", "text": "配飾"}}
                      ]}
                 ]}
            ]
        }
    }
    return FlexSendMessage(alt_text="請選擇撿到的種類", contents=flex_content)

# ============ 4. 訊息與事件處理邏輯 ============
def handle_message_logic(user_id, text, reply_token):
    text = text.strip()
    session = get_session(user_id)
    step = session.get("step")

    if text in ["選單", "開始", "取消", "menu"]:
        clear_session(user_id)
        line_bot_api.reply_message(reply_token, get_main_menu())
        return

    if text == "我撿到東西了":
        set_session(user_id, {"type": "found", "step": "wait_category"})
        line_bot_api.reply_message(reply_token, get_category_menu())
    elif text == "我在找東西":
        set_session(user_id, {"type": "lost", "step": "wait_category"})
        line_bot_api.reply_message(reply_token, get_category_menu())
    elif text in CATEGORIES and step == "wait_category":
        session["category"] = text
        session["step"] = "wait_description"
        set_session(user_id, session)
        line_bot_api.reply_message(reply_token, TextSendMessage(text=f"已選擇：{text}\n請輸入物品的詳細描述："))
    elif step == "wait_description":
        # 儲存剛剛輸入的物品描述
        session["description"] = text
        
        if session.get("type") == "found":
            # 撿到東西：下一步是傳送「拍照或略過」按鈕
            session["step"] = "wait_photo"
            set_session(user_id, session)
            line_bot_api.reply_message(reply_token, get_photo_flex())
        else:
            # 找東西：不需要拍照，直接跳到「選擇地點」按鈕
            session["step"] = "wait_location_button"
            set_session(user_id, session)
            line_bot_api.reply_message(reply_token, get_location_flex("lost"))

    elif step == "wait_photo" and text == "略過":
        # 如果使用者在拍照階段點擊了「略過」
        session["step"] = "wait_location_button"
        set_session(user_id, session)
        line_bot_api.reply_message(reply_token, get_location_flex("found"))
        
    elif step == "wait_location_button":
        # 避免使用者在等待按鈕時輸入文字
        line_bot_api.reply_message(reply_token, TextSendMessage(text="請點擊上方的按鈕選擇地點喔！"))

def handle_postback_logic(user_id, data, reply_token):
    params = parse_qs(data)
    action = params.get('action', [''])[0]
    # 這裡實作按鈕點擊後的邏輯
    if action == "set_location":
        loc = params.get('loc', [''])[0]
        line_bot_api.reply_message(reply_token, TextSendMessage(text=f"地點已設定在：{loc}"))

# ============ 5. Flask Webhook 入口 ============
@app.route("/", methods=['GET'])
def index():
    return "NTPU Lost and Found Bot is running!"

@app.route("/callback", methods=['POST'])
def callback():
    signature = request.headers['X-Line-Signature']
    body = request.get_data(as_text=True)

    try:
        handler.handle(body, signature)
    except InvalidSignatureError:
        abort(400)
    return 'OK'

# 官方 SDK 事件處理器
@handler.add(MessageEvent, message=TextMessage)
def handle_message(event):
    handle_message_logic(event.source.user_id, event.message.text, event.reply_token)

@handler.add(PostbackEvent)
def handle_postback(event):
    handle_postback_logic(event.source.user_id, event.postback.data, event.reply_token)

if __name__ == "__main__":
    port = int(os.environ.get('PORT', 10000))
    app.run(host='0.0.0.0', port=port)
