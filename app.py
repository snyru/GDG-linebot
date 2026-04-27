import os
from dotenv import load_dotenv
from flask import Flask, request, abort
from linebot import LineBotApi, WebhookHandler
from linebot.models import (
    MessageEvent, TextMessage, TextSendMessage,
    ImageMessage, FlexSendMessage, FollowEvent
)
from linebot.exceptions import InvalidSignatureError
import logging

load_dotenv()

line_token = os.getenv('LINE_TOKEN')
line_secret = os.getenv('LINE_SECRET')

if not line_token or not line_secret:
    raise ValueError("LINE_TOKEN 或 LINE_SECRET 未設置")

line_bot_api = LineBotApi(line_token)
handler = WebhookHandler(line_secret)

app = Flask(__name__)
app.logger.setLevel(logging.DEBUG)

# 暫存每個使用者的對話狀態
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

    # 隨時可以回主選單
    if text in ["選單", "開始", "menu", "取消"]:
        sessions.pop(user_id, None)
        line_bot_api.reply_message(event.reply_token, get_main_menu())
        return

    # 主選單選項
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
        line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage(text="（這裡之後會顯示失物清單）")
        )

    # 步驟一：選分類
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

    # 步驟二：填描述
    elif step == "wait_description":
        session["description"] = text
        session["step"] = "wait_photo"
        sessions[user_id] = session
        line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage(text="描述已記錄 ✅\n\n請上傳物品照片（若沒有照片請回覆「略過」）")
        )

    # 步驟二點五：略過照片
    elif step == "wait_photo" and text == "略過":
        session["photo"] = None
        session["step"] = "wait_location"
        sessions[user_id] = session
        line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage(text="📍 請問在哪裡撿到／遺失的？\n\n請用文字描述地點（例如：圖書館一樓、學生餐廳門口）")
        )

    # 步驟三：填地點
    elif step == "wait_location":
        session["location"] = text
        session["step"] = "done"
        sessions[user_id] = session

        item_type = "撿到" if session.get("type") == "found" else "遺失"
        summary = (
            f"✅ 登記完成！\n\n"
            f"類型：{item_type}\n"
            f"分類：{session.get('category')}\n"
            f"描述：{session.get('description')}\n"
            f"地點：{text}\n\n"
            f"我們會幫你比對，有符合的會通知你！"
        )
        sessions.pop(user_id, None)
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
