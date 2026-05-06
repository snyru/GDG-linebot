import os
import json
import io
from flask import Flask, request, abort
from urllib.parse import parse_qs

# LINE SDK
from linebot import LineBotApi, WebhookHandler
from linebot.exceptions import InvalidSignatureError
from linebot.models import (
    MessageEvent, TextMessage, TextSendMessage, 
    FlexSendMessage, PostbackEvent, ImageMessage
)

# 引入 Firebase
import firebase_admin
from firebase_admin import credentials, firestore

# 引入 Cloudinary
import cloudinary
import cloudinary.uploader

# ============ 1. 伺服器與第三方服務初始化 ============
app = Flask(__name__)

# LINE Bot 初始化 (配合你 Render 的環境變數名稱)
line_bot_api = LineBotApi(os.getenv('LINE_TOKEN'))
handler = WebhookHandler(os.getenv('LINE_SECRET'))

# Firebase 初始化 (配合你 Render 的環境變數名稱)
firebase_cert = os.getenv("FIREBASE_CREDENTIALS")
if firebase_cert:
    try:
        cred_dict = json.loads(firebase_cert)
        cred = credentials.Certificate(cred_dict)
        if not firebase_admin._apps:
            firebase_admin.initialize_app(cred)
        db = firestore.client()
    except Exception as e:
        print(f"Firebase 初始化失敗: {e}")
else:
    print("尚未設定 FIREBASE_CREDENTIALS 環境變數！")

# Cloudinary 初始化 (配合你 Render 的環境變數名稱)
cloudinary.config(
    cloud_name=os.getenv("CLOUDINARY_CLOUD_NAME"),
    api_key=os.getenv("CLOUDINARY_API_KEY"),
    api_secret=os.getenv("CLOUDINARY_API_SECRET"),
    secure=True
)

# ============ 2. 業務邏輯與資料庫函式 ============
CATEGORIES = {"電子產品", "衣服", "鞋子", "證件", "錢包", "雨傘", "書籍", "其他", "配飾"}

def get_session(user_id):
    try:
        doc_ref = db.collection('sessions').document(user_id)
        doc = doc_ref.get()
        return doc.to_dict() if doc.exists else {}
    except Exception as e:
        print(f"讀取 Session 失敗: {e}")
        return {}

def set_session(user_id, data):
    try:
        # 使用 merge=True 以免覆蓋掉其他欄位
        db.collection('sessions').document(user_id).set(data, merge=True)
    except Exception as e:
        print(f"寫入 Session 失敗: {e}")

def clear_session(user_id):
    try:
        db.collection('sessions').document(user_id).delete()
    except Exception as e:
        print(f"清除 Session 失敗: {e}")

# ============ 3. Flex Message 生成 ============
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

def get_category_menu(title="我撿到的種類"):
    flex_content = {
        "type": "bubble",
        "size": "mega",
        "body": {
            "type": "box", "layout": "vertical",
            "contents": [
                {"type": "text", "text": title, "weight": "bold", "size": "xl", "align": "center", "margin": "md"},
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
    return FlexSendMessage(alt_text=f"請選擇{title}", contents=flex_content)

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
        line_bot_api.reply_message(reply_token, get_category_menu("我撿到的種類"))
    elif text == "我在找東西":
        set_session(user_id, {"type": "lost", "step": "wait_description"})
        line_bot_api.reply_message(reply_token, get_category_menu("我在找的東西的種類"))
    elif text in CATEGORIES and step == "wait_category":
        session["category"] = text
        session["step"] = "wait_description"
        set_session(user_id, session)
        line_bot_api.reply_message(reply_token, TextSendMessage(text=f"已選擇：{text}\n請輸入物品的詳細描述："))
    elif step == "wait_description":
        session["description"] = text
        
        if session.get("type") == "found":
            session["step"] = "wait_photo"
            set_session(user_id, session)
            line_bot_api.reply_message(reply_token, get_photo_flex())
        else:
            session["step"] = "wait_location_button"
            set_session(user_id, session)
            line_bot_api.reply_message(reply_token, get_location_flex("lost"))

    elif step == "wait_photo" and text == "略過":
        session["step"] = "wait_location_button"
        set_session(user_id, session)
        line_bot_api.reply_message(reply_token, get_location_flex("found"))
        
    elif step == "wait_location_button":
        line_bot_api.reply_message(reply_token, TextSendMessage(text="請點擊上方的按鈕選擇地點喔！"))
        
    elif step == "wait_detailed_location":
        detailed_location = text
        main_location = session.get("location", "未知地點")
        photo_url = session.get("photo_url", "")
        
        # 準備存入 Firestore 的完整資料
        final_data = {
            "userId": user_id,
            "type": session.get("type"),
            "category": session.get("category"),
            "description": session.get("description"),
            "location": main_location,
            "detailed_location": detailed_location,
            "photo_url": photo_url,
            "status": "open",
            "timestamp": firestore.SERVER_TIMESTAMP
        }
        
        try:
            # 正式寫入 Firestore 資料庫的 items 集合
            db.collection('items').add(final_data)
        except Exception as e:
            print(f"寫入 items 失敗: {e}")
        
        clear_session(user_id)
        
        summary = (
            f"✅ 登記成功！\n"
            f"📌 分類：{final_data['category']}\n"
            f"📝 描述：{final_data['description']}\n"
            f"📍 地點：{final_data['location']} ({final_data['detailed_location']})"
        )
        if photo_url:
            summary += "\n📷 照片已成功上傳"
            
        line_bot_api.reply_message(reply_token, TextSendMessage(text=summary))

def handle_postback_logic(user_id, data, reply_token):
    params = parse_qs(data)
    action = params.get('action', [''])[0]
    session = get_session(user_id)
    
    if action == "set_location":
        loc = params.get('loc', [''])[0]
        session["location"] = loc
        session["step"] = "wait_detailed_location"
        set_session(user_id, session)
        
        reply_msg = f"已選擇：{loc}\n請輸入更詳細的位置描述（例如：二樓靠近窗戶的座位、大門口右側等）："
        line_bot_api.reply_message(reply_token, TextSendMessage(text=reply_msg))

# 處理照片上傳的邏輯 (Cloudinary)
def handle_image_message_logic(user_id, message_id, reply_token):
    session = get_session(user_id)
    step = session.get("step")
    
    if step == "wait_photo":
        # 提示正在處理中，避免使用者等太久
        line_bot_api.reply_message(reply_token, TextSendMessage(text="照片上傳中，請稍候..."))
        
        try:
            # 從 LINE 伺服器取得圖片位元組
            message_content = line_bot_api.get_message_content(message_id)
            image_io = io.BytesIO(b''.join(message_content.iter_content()))
            
            # 上傳到 Cloudinary
            upload_result = cloudinary.uploader.upload(image_io)
            image_url = upload_result.get("secure_url")
            
            # 更新 Session
            session["photo_url"] = image_url
            session["step"] = "wait_location_button"
            set_session(user_id, session)
            
            # 補發一個地點選擇的按鈕
            line_bot_api.push_message(user_id, get_location_flex("found"))
        except Exception as e:
            print(f"圖片上傳失敗: {e}")
            line_bot_api.push_message(user_id, TextSendMessage(text="照片上傳失敗，請稍後再試。"))
    else:
        line_bot_api.reply_message(reply_token, TextSendMessage(text="目前不需要傳送照片喔，請根據指示操作！"))

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

@handler.add(MessageEvent, message=ImageMessage)
def handle_image(event):
    handle_image_message_logic(event.source.user_id, event.message.id, event.reply_token)

if __name__ == "__main__":
    port = int(os.environ.get('PORT', 10000))
    app.run(host='0.0.0.0', port=port)
