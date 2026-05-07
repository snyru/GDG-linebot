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

import firebase_admin
from firebase_admin import credentials, firestore
import cloudinary
import cloudinary.uploader

# ============ 1. 伺服器與第三方服務初始化 ============
app = Flask(__name__)

line_bot_api = LineBotApi(os.getenv('LINE_TOKEN'))
handler = WebhookHandler(os.getenv('LINE_SECRET'))

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
        return {}

def set_session(user_id, data):
    try:
        db.collection('sessions').document(user_id).set(data, merge=True)
    except Exception as e:
        pass

def clear_session(user_id):
    try:
        db.collection('sessions').document(user_id).delete()
    except Exception as e:
        pass

# ============ 3. Flex Message 生成 ============
def get_flex_message(filename, alt_text):
    file_path = os.path.join(os.path.dirname(__file__), filename)
    with open(file_path, 'r', encoding='utf-8') as f:
        contents = json.load(f)
    return FlexSendMessage(alt_text=alt_text, contents=contents)

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

def generate_carousel_flex(items_list, alt_text="失物列表", show_claim_button=True):
    bubbles = []
    for item in items_list:
        bubble = {
            "type": "bubble",
            "body": {
                "type": "box",
                "layout": "vertical",
                "contents": [
                    {"type": "text", "text": item.get('category', '未知分類'), "weight": "bold", "size": "xl", "color": "#1DB446"},
                    {"type": "text", "text": f"特徵：{item.get('description', '無')}", "wrap": True, "margin": "md", "size": "sm"},
                    {"type": "text", "text": f"掉落點：{item.get('location', '')} {item.get('detailed_location', '')}", "wrap": True, "size": "xs", "color": "#aaaaaa"}
                ]
            }
        }
        
        if item.get("dropoff"):
            bubble["body"]["contents"].append(
                {"type": "text", "text": f"📍 目前放置於：{item['dropoff']}", "wrap": True, "size": "sm", "color": "#FF3366", "weight": "bold", "margin": "md"}
            )
            
        if item.get("photo_url"):
            bubble["hero"] = {
                "type": "image",
                "url": item["photo_url"],
                "size": "full",
                "aspectRatio": "4:3",
                "aspectMode": "cover"
            }
        
        if show_claim_button and "doc_id" in item:
            bubble["footer"] = {
                "type": "box",
                "layout": "vertical",
                "spacing": "sm",
                "contents": [
                    {
                        "type": "button",
                        "style": "primary",
                        "color": "#FF6B6E",
                        "action": {
                            "type": "postback",
                            "label": "✋ 這是我的！",
                            "data": f"action=claim_item&item_id={item['doc_id']}",
                            "displayText": "我想領回這個物品"
                        }
                    }
                ]
            }
        bubbles.append(bubble)

    if not bubbles:
        return TextSendMessage(text="目前沒有找到符合的物品喔！")

    carousel = {
        "type": "carousel",
        "contents": bubbles
    }
    return FlexSendMessage(alt_text=alt_text, contents=carousel)

# 寫入資料庫的共用函式 (確保如果有沒填到的資料，會補上空字串而不是 None)
def save_item_to_db(user_id, session):
    final_data = {
        "userId": user_id,
        "type": session.get("type"),
        "category": session.get("category", "未知分類"),
        "description": session.get("description", "無"),
        "location": session.get("location", "未知"),
        "detailed_location": session.get("detailed_location", ""),
        "dropoff": session.get("dropoff", ""),
        "photo_url": session.get("photo_url", ""),
        "status": "open",
        "timestamp": firestore.SERVER_TIMESTAMP
    }
    try:
        db.collection('items').add(final_data)
    except Exception as e:
        print(f"寫入 items 失敗: {e}")
    return final_data

# ============ 4. 訊息與事件處理邏輯 ============
def handle_message_logic(user_id, text, reply_token):
    text = text.strip()
    session = get_session(user_id)
    step = session.get("step")

    # 1. 處理主選單與強制重置
    if text in ["選單", "開始", "取消", "menu"]:
        clear_session(user_id)
        line_bot_api.reply_message(reply_token, get_main_menu())
        return

    if text == "查看所有失物":
        try:
            docs = db.collection('items').where('type', '==', 'found').where('status', '==', 'open').limit(10).stream()
            items_list = [{"doc_id": doc.id, **doc.to_dict()} for doc in docs]
            if not items_list:
                line_bot_api.reply_message(reply_token, TextSendMessage(text="目前沒有待領取的失物喔！"))
            else:
                line_bot_api.reply_message(reply_token, generate_carousel_flex(items_list, "失物列表", show_claim_button=False))
        except Exception:
            line_bot_api.reply_message(reply_token, TextSendMessage(text="讀取失敗，請稍後再試。"))
        return

    # [防呆重點] 開始新流程時，強制清除舊的記憶，避免交錯！
    if text == "我撿到東西了":
        clear_session(user_id) # 強制清空舊記憶
        set_session(user_id, {"type": "found", "step": "wait_category"})
        line_bot_api.reply_message(reply_token, get_category_menu("我撿到的種類"))
        return
        
    elif text == "我在找東西":
        clear_session(user_id) # 強制清空舊記憶
        set_session(user_id, {"type": "lost", "step": "wait_category"})
        line_bot_api.reply_message(reply_token, get_category_menu("我在找的東西的種類"))
        return
    
    # 2. 處理步驟流程 (防呆版)
    if step == "wait_category":
        if text in CATEGORIES:
            session["category"] = text
            if session.get("type") == "found":
                session["step"] = "wait_description"
                set_session(user_id, session)
                line_bot_api.reply_message(reply_token, TextSendMessage(text=f"已選擇：{text}\n請輸入物品的詳細描述："))
            else:
                session["step"] = "wait_location_button"
                set_session(user_id, session)
                line_bot_api.reply_message(reply_token, get_flex_message('lost_place.json', '請選擇地點'))
        else:
            line_bot_api.reply_message(reply_token, TextSendMessage(text="⚠️ 請從上方的選單點擊選擇一個分類喔！"))
            
    elif step == "wait_description":
        session["description"] = text
        session["step"] = "wait_photo"
        set_session(user_id, session)
        line_bot_api.reply_message(reply_token, get_flex_message('photo.json', '請上傳照片或略過'))

    elif step == "wait_photo":
        if text == "略過":
            session["step"] = "wait_location_button"
            set_session(user_id, session)
            line_bot_api.reply_message(reply_token, get_flex_message('find_place.json', '請選擇地點'))
        else:
            line_bot_api.reply_message(reply_token, TextSendMessage(text="⚠️ 請上傳圖片，或者點擊下方按鈕選擇「略過」喔！"))
        
    elif step == "wait_location_button":
        line_bot_api.reply_message(reply_token, TextSendMessage(text="⚠️ 請點擊上方的按鈕選擇地點喔！"))
        
    elif step == "wait_detailed_location":
        session["detailed_location"] = text
        item_type = session.get("type")
        
        if item_type == "found": 
            session["step"] = "wait_dropoff_options"
            set_session(user_id, session)
            line_bot_api.reply_message(reply_token, get_flex_message('dropoff.json', '預計送去哪個地點？'))
        else:
            clear_session(user_id) # 防呆機制
            line_bot_api.reply_message(reply_token, TextSendMessage(text="流程有誤，請重新輸入「選單」開始。"))
            
    elif step == "wait_dropoff_options":
        if text == "其他":
            session["step"] = "wait_custom_dropoff"
            set_session(user_id, session)
            line_bot_api.reply_message(reply_token, TextSendMessage(text="請輸入您預計放置的詳細地點："))
        elif text in ["放置原地", "正門警衛室", "學生事務處-軍訓室", "三峽校區綜合體育館"]:
            session["dropoff"] = text
            final_data = save_item_to_db(user_id, session)
            clear_session(user_id) # 結案清空記憶
            
            messages = [
                TextSendMessage(text=f"✅ 登記成功！感謝您的熱心！\n📌 分類：{final_data['category']}\n📍 發現地：{final_data['location']} ({final_data['detailed_location']})\n🏫 放置於：{final_data['dropoff']}"),
                TextSendMessage(text="以下提供校方的失物招領通報據點資訊給您參考👇"),
                get_flex_message('contact_places.json', '聯絡據點')
            ]
            line_bot_api.reply_message(reply_token, messages)
        else:
            line_bot_api.reply_message(reply_token, TextSendMessage(text="⚠️ 請從上方選單選擇放置地點，或選擇「其他」喔！"))

    elif step == "wait_custom_dropoff":
        session["dropoff"] = text
        final_data = save_item_to_db(user_id, session)
        clear_session(user_id) # 結案清空記憶
        
        messages = [
            TextSendMessage(text=f"✅ 登記成功！感謝您的熱心！\n📌 分類：{final_data['category']}\n📍 發現地：{final_data['location']} ({final_data['detailed_location']})\n🏫 放置於：{final_data['dropoff']}"),
            TextSendMessage(text="以下提供校方的失物招領通報據點資訊給您參考👇"),
            get_flex_message('contact_places.json', '聯絡據點')
        ]
        line_bot_api.reply_message(reply_token, messages)

# 處理按鈕回傳 (防呆版)
def handle_postback_logic(user_id, data, reply_token):
    params = parse_qs(data)
    action = params.get('action', [''])[0]
    session = get_session(user_id)
    
    if action == "set_location":
        # [防呆重點] 如果使用者按了以前的舊按鈕，但現在根本不是選地點的步驟，直接擋下來！
        if not session or session.get("step") not in ["wait_location_button", "wait_photo"]:
            line_bot_api.reply_message(reply_token, TextSendMessage(text="⚠️ 操作順序有誤！請重新輸入「選單」開啟新流程喔！"))
            return
            
        loc = params.get('loc', [''])[0]
        session["location"] = loc
        if session.get("type") == "lost":
            session["detailed_location"] = "" # 直接給空字串
            final_data = save_item_to_db(user_id, session)
            clear_session(user_id) # 結案清空記憶
            
            messages = [TextSendMessage(text=f"✅ 登記成功！\n📌 分類：{final_data['category']}\n📍 地點：{final_data['location']}")]
            
            try:
                match_docs = db.collection('items').where('type', '==', 'found').where('category', '==', final_data['category']).where('status', '==', 'open').limit(5).stream()
                matches = [{"doc_id": doc.id, **doc.to_dict()} for doc in match_docs]
                
                if matches:
                    messages.append(TextSendMessage(text="💡 系統自動為您比對出以下可能的結果："))
                    messages.append(generate_carousel_flex(matches, "系統配對結果"))
                else:
                    messages.append(TextSendMessage(text="系統目前尚未配對到符合的物品。您可以直接聯繫下方學校單位詢問，或隨時回來查看喔！👇"))
                    messages.append(get_flex_message('contact_places.json', '聯絡據點'))
            except Exception:
                pass
            line_bot_api.reply_message(reply_token, messages)
        else:
            session["step"] = "wait_detailed_location"
            set_session(user_id, session)
            line_bot_api.reply_message(reply_token, TextSendMessage(text=f"已選擇：{loc}\n請輸入更詳細的位置描述（例如：二樓靠近窗戶的座位、大門口右側等）："))
        
    elif action == "claim_item":
        item_id = params.get('item_id', [''])[0]
        try:
            db.collection('items').document(item_id).update({'status': 'closed'})
            line_bot_api.reply_message(reply_token, TextSendMessage(text="🎉 太好了！已將此物品標記為「已尋回」，它不會再顯示於列表中囉。\n\n⚠️ 請依循校方或相關單位的規定前往領取/確認喔！"))
        except Exception:
            line_bot_api.reply_message(reply_token, TextSendMessage(text="Oops, 標記失敗，請稍後再試。"))

def handle_image_message_logic(user_id, message_id, reply_token):
    session = get_session(user_id)
    if session.get("step") == "wait_photo":
        line_bot_api.reply_message(reply_token, TextSendMessage(text="照片上傳中，請稍候..."))
        try:
            content = line_bot_api.get_message_content(message_id)
            image_url = cloudinary.uploader.upload(io.BytesIO(b''.join(content.iter_content()))).get("secure_url")
            session["photo_url"] = image_url
            session["step"] = "wait_location_button"
            set_session(user_id, session)
            line_bot_api.push_message(user_id, get_flex_message('find_place.json', '請選擇地點'))
        except Exception:
            line_bot_api.push_message(user_id, TextSendMessage(text="照片上傳失敗，請稍後再試。"))
    else:
        line_bot_api.reply_message(reply_token, TextSendMessage(text="目前不需要傳送照片喔！"))

# ============ 5. Flask Webhook 入口 ============
@app.route("/", methods=['GET'])
def index():
    return "Bot is running!"

@app.route("/callback", methods=['POST'])
def callback():
    try:
        handler.handle(request.get_data(as_text=True), request.headers['X-Line-Signature'])
    except InvalidSignatureError:
        abort(400)
    return 'OK'

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
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 10000)))
