import os
import json
from dotenv import load_dotenv
from flask import Flask, request, abort
from linebot import LineBotApi, WebhookHandler
from linebot.models import (
    MessageEvent, TextMessage, TextSendMessage,
    ImageMessage, FlexSendMessage, FollowEvent,
    ImageSendMessage
)
from linebot.exceptions import InvalidSignatureError
import firebase_admin
from firebase_admin import credentials, firestore
import cloudinary
import cloudinary.uploader
import logging

load_dotenv()

cred_json = os.getenv('FIREBASE_CREDENTIALS')
project_id = os.getenv('FIREBASE_PROJECT_ID')
cred_dict = json.loads(cred_json)
cred = credentials.Certificate(cred_dict)
firebase_admin.initialize_app(cred, {'projectId': project_id})
db = firestore.client()

cloudinary.config(
    cloud_name=os.getenv('CLOUDINARY_CLOUD_NAME'),
    api_key=os.getenv('CLOUDINARY_API_KEY'),
    api_secret=os.getenv('CLOUDINARY_API_SECRET')
)

line_token = os.getenv('LINE_TOKEN')
line_secret = os.getenv('LINE_SECRET')

if not line_token or not line_secret:
    raise ValueError("LINE_TOKEN 或 LINE_SECRET 未設置")

line_bot_api = LineBotApi(line_token)
handler = WebhookHandler(line_secret)

app = Flask(__name__)
app.logger.setLevel(logging.DEBUG)

CATEGORIES = {
    "1": "電子產品",
    "2": "衣物配件",
    "3": "證件錢包",
    "4": "鑰匙",
    "5": "其他"
}


def get_session(user_id):
    doc = db.collection('sessions').document(user_id).get()
    return doc.to_dict() if doc.exists else {}


def set_session(user_id, data):
    db.collection('sessions').document(user_id).set(data)


def clear_session(user_id):
    db.collection('sessions').document(user_id).delete()


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
                    "style": "primary",
                    "color": "#FF9800",
                    "action": {
                        "type": "message",
                        "label": "✅ 我找到了",
                        "text": "我找到了"
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


def create_or_update_user(user_id, display_name):
    user_ref = db.collection('users').document(user_id)
    user_doc = user_ref.get()
    if not user_doc.exists:
        user_ref.set({
            'lineUserId': user_id,
            'displayName': display_name,
            'notifyEnabled': True,
            'createdAt': firestore.SERVER_TIMESTAMP
        })
    else:
        user_ref.update({'displayName': display_name})


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

        found_id = item_id if session.get('type') == 'found' else match.id
        lost_id = match.id if session.get('type') == 'found' else item_id

        existing = db.collection('matches') \
            .where('foundItemId', '==', found_id) \
            .where('lostItemId', '==', lost_id) \
            .limit(1) \
            .stream()

        if list(existing):
            continue

        db.collection('matches').add({
            'foundItemId': found_id,
            'lostItemId': lost_id,
            'foundUserId': user_id if session.get('type') == 'found' else other_user_id,
            'lostUserId': other_user_id if session.get('type') == 'found' else user_id,
            'status': 'notified',
            'createdAt': firestore.SERVER_TIMESTAMP
        })

        # 通知對方
        try:
            messages = [
                TextSendMessage(
                    text=f"🔔 有人登記了{my_type}的{category}，可能跟你的有關！\n\n"
                         f"描述：{session.get('description')}\n"
                         f"地點：{session.get('location')}\n\n"
                         f"請回覆「選單」查看更多"
                )
            ]
            if session.get('photo'):
                messages.append(ImageSendMessage(
                    original_content_url=session.get('photo'),
                    preview_image_url=session.get('photo')
                ))
            line_bot_api.push_message(other_user_id, messages)
        except Exception as e:
            app.logger.error(f"通知對方失敗: {e}")

        # 通知自己
        try:
            messages = [
                TextSendMessage(
                    text=f"🔔 資料庫裡有一筆{other_type}的{category}可能符合！\n\n"
                         f"描述：{match_data.get('description')}\n"
                         f"地點：{match_data.get('location')}\n\n"
                         f"請回覆「選單」查看更多"
                )
            ]
            if match_data.get('photo'):
                messages.append(ImageSendMessage(
                    original_content_url=match_data.get('photo'),
                    preview_image_url=match_data.get('photo')
                ))
            line_bot_api.push_message(user_id, messages)
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
    user_id = event.source.user_id
    profile = line_bot_api.get_profile(user_id)
    create_or_update_user(user_id, profile.display_name)
    line_bot_api.reply_message(
        event.reply_token,
        [
            TextSendMessage(text=f"歡迎 {profile.display_name}！👋\n使用失物招領服務"),
            get_main_menu()
        ]
    )


@handler.add(MessageEvent, message=ImageMessage)
def handle_image(event):
    user_id = event.source.user_id
    session = get_session(user_id)
    step = session.get("step")

    if step == "wait_photo":
        message_content = line_bot_api.get_message_content(event.message.id)
        image_data = b"".join(chunk for chunk in message_content.iter_content())

        upload_result = cloudinary.uploader.upload(
            image_data,
            folder="linebot-lost-found"
        )
        image_url = upload_result.get('secure_url')

        session["photo"] = image_url
        session["step"] = "wait_location"
        set_session(user_id, session)
        line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage(text="照片已上傳 ✅\n\n📍 最後，請問在哪裡撿到／遺失的？\n\n請用文字描述地點（例如：圖書館一樓、學生餐廳門口）")
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
    session = get_session(user_id)
    step = session.get("step")

    if text in ["選單", "開始", "menu", "取消"]:
        clear_session(user_id)
        line_bot_api.reply_message(event.reply_token, get_main_menu())
        return

    if text == "我撿到東西了":
        set_session(user_id, {"type": "found", "step": "wait_category"})
        line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage(text="好的！請問撿到的是哪類物品？\n\n請回覆數字：\n1. 電子產品\n2. 衣物配件\n3. 證件錢包\n4. 鑰匙\n5. 其他")
        )

    elif text == "我在找東西":
        set_session(user_id, {"type": "lost", "step": "wait_category"})
        line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage(text="沒關係！請問遺失的是哪類物品？\n\n請回覆數字：\n1. 電子產品\n2. 衣物配件\n3. 證件錢包\n4. 鑰匙\n5. 其他")
        )
    
    elif text == "我找到了":
        items = db.collection('items').where('status', '==', 'open').limit(5).stream()
        result = []

        for item in items:
            d = item.to_dict()
            item_type = "撿到" if d.get('type') == 'found' else "遺失"
            result.append(
                f"【{item_type}】{d.get('category')} - {d.get('description')} @ {d.get('location')}\n"
                f"回覆：選擇 {item.id}"
        )

    if result:
        reply = "✅ 請選擇你已找回的物品：\n\n" + "\n\n".join(result)
    else:
        reply = "目前沒有可選擇的失物 😊"

    line_bot_api.reply_message(
        event.reply_token,
        TextSendMessage(text=reply)
    )
    elif text.startswith("選擇 "):
        item_id = text.replace("選擇 ", "").strip()

        session["confirm_delete_id"] = item_id
        set_session(user_id, session)

        line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage(text=f"你確定要將這筆物品標記為已找回嗎？\n\n請回覆：確認刪除")
    )

    elif text == "確認刪除":
        item_id = session.get("confirm_delete_id")

        if not item_id:
            line_bot_api.reply_message(
                event.reply_token,
                TextSendMessage(text="找不到要處理的物品，請重新選擇。")
            )
            return

        db.collection('items').document(item_id).update({
            'status': 'closed'
        })

        clear_session(user_id)

        line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage(text="✅ 已確認找回，這筆物品已從清單中移除。")
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
            set_session(user_id, session)
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
        set_session(user_id, session)
        line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage(text="描述已記錄 ✅\n\n請上傳物品照片（若沒有照片請回覆「略過」）")
        )

    elif step == "wait_photo" and text == "略過":
        session["photo"] = None
        session["step"] = "wait_location"
        set_session(user_id, session)
        line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage(text="📍 請問在哪裡撿到／遺失的？\n\n請用文字描述地點（例如：圖書館一樓、學生餐廳門口）")
        )

    elif step == "wait_location":
        session["location"] = text
        set_session(user_id, session)

        item_id = save_item(user_id, session)
        match_and_notify(user_id, session, item_id)
        clear_session(user_id)

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
