import os
import json
from dotenv import load_dotenv
from flask import Flask, request, abort
from linebot import LineBotApi, WebhookHandler
from linebot.models import (
    MessageEvent, TextMessage, TextSendMessage,
    ImageMessage, FlexSendMessage, FollowEvent
)
from linebot.exceptions import InvalidSignatureError
import firebase_admin
from firebase_admin import credentials, firestore
import logging

load_dotenv()

cred_json = os.getenv('FIREBASE_CREDENTIALS')
project_id = os.getenv('FIREBASE_PROJECT_ID')
cred_dict = json.loads(cred_json)
cred = credentials.Certificate(cred_dict)
firebase_admin.initialize_app(cred, {'projectId': project_id})
db = firestore.client()

line_token = os.getenv('LINE_TOKEN')
line_secret = os.getenv('LINE_SECRET')

if not line_token or not line_secret:
    raise ValueError("LINE_TOKEN 或 LINE_SECRET 未設置")

line_bot_api = LineBotApi(line_token)
handler = WebhookHandler(line_secret)

app = Flask(__name__)
app.logger.setLevel(logging.DEBUG)

sessions = {}

CATEGORIES = {
    "1": "電子產品",
    "2": "衣物配件",
    "3": "證件錢包",
    "4": "鑰匙",
    "5": "其他"
}


def get_main_menu():
    flex_content = {
        "type": "bubble",
        "body": {
            "type": "box",
            "layout": "vertical",
            "spacing": "md",
            "contents": [
                {
                    "type": "text",
                    "text": "🔍 失物招領",
                    "weight": "bold",
                    "size": "xl",
                    "align": "center"
                },
                {
                    "type": "text",
                    "text": "請選擇你的狀況",
                    "size": "sm",
                    "color": "#888888",
                    "align": "center"
                },
                {
                    "type": "button",
                    "style": "primary",
                    "color": "#4CAF50",
                    "action": {
                        "type": "message",
                        "label": "📦 我撿到東西了",
                        "text": "我撿到東西了"
                    }
                },
                {
                    "type": "button",
                    "style": "primary",
                    "color": "#2196F3",
                    "action": {
                        "type": "message",
                        "label": "🔎 我在找東西",
                        "text": "我在找東西"
                    }
                },
                {
                    "type": "button",
                    "style": "secondary",
                    "action": {
                        "type": "message",
                        "label": "📋 查看所有失物",
                        "text": "查看所有失物"
                    }
                }
            ]
        }
    }
    return FlexSendMessage(alt_text="失物招領選單", contents=flex_content)


def save_item(user_id, session):
    doc_ref = db.collection('items').add({
        'userId': user_id,
        'type': session.get('type'),
        'category': session.get('category'),
        'description': session.get('description'),
        'photo': session.get('photo'),
        'location': session.get('location'),
        'status': 'open',
        'createdAt': firestore.SERVER_TIMESTAMP
    })
    return doc_ref[1].id


def match_and_notify(user_id, session, item_id):
    opposite_type = 'lost' if session.get('type') == 'found' else 'found'
    category = session.get('category')

    matches = db.collection('items') \
        .where('type', '==', opposite_type) \
        .where('category', '==', category) \
        .where('status', '==', 'open') \
        .limit(3) \
        .stream()

    matched = [m for m in matches if m.id != item_id]

    if not matched:
        return

    my_type = "撿到" if session.get('type') == 'found' else "遺失"
    other_type = "遺失" if session.get('type') == 'found' else "撿到"

    for match in matched:
        match_data = match.to_dict()
        other_user_id = match_data.get('userId')

        try:
            line_bot_api.push_message(
                other_user_id,
                TextSendMessage(
                    text=f"🔔 有人登記了{my_type}的{category}，可能跟你的有關！\n\n"
                         f"描述：{session.get('description')}\n"
                         f"地點：{session.get('location')}\n\n"
                         f"請回覆「選單」查看更多"
                )
            )
        except Exception as e:
            app.logger.error(f"通知對方失敗: {e}")

        try:
            line_bot_api.push_message(
                user_id,
                TextSendMessage(
                    text=f"🔔 資料庫裡有一筆{other_type}的{category}可能符合！\n\n"
                         f"描述：{match_data.get('description')}\n"
                         f"地點：{match_data.get('location')}\n\n"
                         f"請回覆「選單」查看更多"
                )
            )
        except Exception as e:
            app.logger.error(f"通知自己失敗: {e}")


@app.route("/", methods=['POST'])
def callback():
    signature = request.headers['X-Line-Signature']
    body = request.get_data(as_text=True)
    try:
        handler.handle(body, signature)
    except InvalidSignatureError:
        abort(400)
    return 'OK'


@handler.add(FollowEvent)
def handle_follow(event):
    line_bot_api.reply_message(
        event.reply_token,
        [
            TextSendMessage(text="歡迎使用失物招領服務！👋"),
            get_main_menu()
        ]
    )


@handler.add(MessageEvent, message=ImageMessage)
def handle_image(event):
    user_id = event.source.user_id
    session = sessions.get(user_id, {})
    step = session.get("step")

    if step == "wait_photo":
        session["photo"] = event.message.id
        session["step"] = "wait_location"
        sessions[user_id] = session
        line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage(text="📍 最後，請問在哪裡撿到／遺失的？\n\n請用文字描述地點（例如：圖書館一樓、學生餐廳門口）")
        )
    else:
        line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage(text="請先從選單開始 😊")
        )


@handler.add(MessageEvent, message=TextMessage)
def handle_message(event):
    user_id = event.source.user_id
    text = event.message.text.strip()
    session = sessions.get(user_id, {})
    step = session.get("step")

    if text in ["選單", "開始", "menu", "取消"]:
        sessions.pop(user_id, None)
        line_bot_api.reply_message(event.reply_token, get_main_menu())
        return

    if text == "我撿到東西了":
        sessions[user_id] = {"type": "found", "step": "wait_category"}
        line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage(text="好的！請問撿到的是哪類物品？\n\n請回覆數字：\n1. 電子產品\n2. 衣物配件\n3. 證件錢包\n4. 鑰匙\n5. 其他")
        )

    elif text == "我在找東西":
        sessions[user_id] = {"type": "lost", "step": "wait_category"}
        line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage(text="沒關係！請問遺失的是哪類物品？\n\n請回覆數字：\n1. 電子產品\n2. 衣物配件\n3. 證件錢包\n4. 鑰匙\n5. 其他")
        )

    elif text == "查看所有失物":
        items = db.collection('items').where('status', '==', 'open').limit(5).stream()
        result = []
        for item in items:
            d = item.to_dict()
            item_type = "撿到" if d.get('type') == 'found' else "遺失"
            result.append(f"【{item_type}】{d.get('category')} - {d.get('description')} @ {d.get('location')}")

        if result:
            reply = "📋 目前的失物清單：\n\n" + "\n\n".join(result)
        else:
            reply = "目前沒有任何登記的失物 😊"

        line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage(text=reply)
        )

    elif step == "wait_category":
        if text in CATEGORIES:
            session["category"] = CATEGORIES[text]
            session["step"] = "wait_description"
            sessions[user_id] = session
            line_bot_api.reply_message(
                event.reply_token,
                TextSendMessage(text=f"分類：{CATEGORIES[text]} ✅\n\n請描述一下這個物品的外觀特徵（顏色、品牌、特殊記號等）")
            )
        else:
            line_bot_api.reply_message(
                event.reply_token,
                TextSendMessage(text="請回覆 1~5 的數字選擇分類 😊")
            )

    elif step == "wait_description":
        session["description"] = text
        session["step"] = "wait_photo"
        sessions[user_id] = session
        line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage(text="描述已記錄 ✅\n\n請上傳物品照片（若沒有照片請回覆「略過」）")
        )

    elif step == "wait_photo" and text == "略過":
        session["photo"] = None
        session["step"] = "wait_location"
        sessions[user_id] = session
        line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage(text="📍 請問在哪裡撿到／遺失的？\n\n請用文字描述地點（例如：圖書館一樓、學生餐廳門口）")
        )

    elif step == "wait_location":
        session["location"] = text
        sessions[user_id] = session

        item_id = save_item(user_id, session)
        match_and_notify(user_id, session, item_id)
        sessions.pop(user_id, None)

        item_type = "撿到" if session.get("type") == "found" else "遺失"
        summary = (
            f"✅ 登記完成！已儲存到資料庫\n\n"
            f"類型：{item_type}\n"
            f"分類：{session.get('category')}\n"
            f"描述：{session.get('description')}\n"
            f"地點：{text}\n\n"
            f"我們會幫你比對，有符合的會通知你！"
        )
        line_bot_api.reply_message(
            event.reply_token,
            [
                TextSendMessage(text=summary),
                get_main_menu()
            ]
        )

    else:
        line_bot_api.reply_message(
            event.reply_token,
            [
                TextSendMessage(text="請使用下方選單操作 👇"),
                get_main_menu()
            ]
        )


if __name__ == "__main__":
    app.run(host='0.0.0.0', port=5000)
