import uuid
import json
import os
from urllib.parse import parse_qs

from flask import Flask, request, abort
from linebot import LineBotApi, WebhookHandler
from linebot.exceptions import InvalidSignatureError
from linebot.models import (
    MessageEvent, TextMessage, PostbackEvent, TextSendMessage, FlexSendMessage
)


# # ============ 假資料庫（用 dict 模擬 Firestore）============
# class FakeDB:
#     def __init__(self):
#         self.collections = {
#             'items': {},      # id -> dict
#             'sessions': {},   # user_id -> dict
#             'users': {},
#             'matches': {}
#         }

#     def collection(self, name):
#         return FakeCollection(self, name)


# class FakeCollection:
#     def __init__(self, db, name):
#         self.db = db
#         self.name = name
#         self._filters = []
#         self._limit = None

#     def document(self, doc_id):
#         return FakeDoc(self.db, self.name, doc_id)

#     def where(self, field, op, value):
#         new = FakeCollection(self.db, self.name)
#         new._filters = self._filters + [(field, op, value)]
#         new._limit = self._limit
#         return new

#     def limit(self, n):
#         new = FakeCollection(self.db, self.name)
#         new._filters = self._filters
#         new._limit = n
#         return new

#     def stream(self):
#         results = []
#         for doc_id, data in self.db.collections[self.name].items():
#             ok = True
#             for field, op, value in self._filters:
#                 if op == '==' and data.get(field) != value:
#                     ok = False
#                     break
#             if ok:
#                 results.append(FakeSnapshot(doc_id, data))
#         if self._limit:
#             results = results[:self._limit]
#         return iter(results)

#     def add(self, data):
#         new_id = str(uuid.uuid4())[:8]
#         self.db.collections[self.name][new_id] = data
#         return (None, FakeDoc(self.db, self.name, new_id))


# class FakeDoc:
#     def __init__(self, db, collection_name, doc_id):
#         self.db = db
#         self.collection_name = collection_name
#         self.id = doc_id

#     def get(self):
#         data = self.db.collections[self.collection_name].get(self.id)
#         return FakeSnapshot(self.id, data) if data is not None else FakeSnapshot(self.id, None)

#     def set(self, data):
#         self.db.collections[self.collection_name][self.id] = data

#     def update(self, data):
#         if self.id in self.db.collections[self.collection_name]:
#             self.db.collections[self.collection_name][self.id].update(data)

#     def delete(self):
#         self.db.collections[self.collection_name].pop(self.id, None)


# class FakeSnapshot:
#     def __init__(self, doc_id, data):
#         self.id = doc_id
#         self._data = data

#     @property
#     def exists(self):
#         return self._data is not None

#     def to_dict(self):
#         return self._data


# db = FakeDB()


# # ============ 預設塞一些假資料（5 筆遺失物，方便測試）============
# def seed_data():
#     sample_items = [
#         {'userId': 'u1', 'type': 'lost', 'category': '電子產品',
#          'description': '黑色 iPhone 14，背蓋有貼貓貼紙', 'photo': None,
#          'location': '圖書館一樓', 'status': 'open'},
#         {'userId': 'u2', 'type': 'found', 'category': '鑰匙',
#          'description': '一串鑰匙，有藍色吊飾', 'photo': None,
#          'location': '學生餐廳', 'status': 'open'},
#         {'userId': 'u1', 'type': 'lost', 'category': '證件錢包',
#          'description': '咖啡色長夾，裡面有學生證', 'photo': None,
#          'location': '系館 305 教室', 'status': 'open'},
#         {'userId': 'u3', 'type': 'lost', 'category': '衣物配件',
#          'description': '黑色 Nike 帽子', 'photo': None,
#          'location': '操場', 'status': 'open'},
#         {'userId': 'u2', 'type': 'found', 'category': '其他',
#          'description': '一把藍色雨傘', 'photo': None,
#          'location': '校門口', 'status': 'open'},
#     ]
#     for item in sample_items:
#         db.collection('items').add(item)


# # ============ 假的 LINE SDK ============
# class FakeSendMessage:
#     pass


# class TextSendMessage(FakeSendMessage):
#     def __init__(self, text):
#         self.text = text

#     def render(self):
#         return f"💬 {self.text}"


# class ImageSendMessage(FakeSendMessage):
#     def __init__(self, original_content_url, preview_image_url):
#         self.url = original_content_url

#     def render(self):
#         return f"🖼️  [圖片] {self.url}"


# class FlexSendMessage(FakeSendMessage):
#     def __init__(self, alt_text, contents):
#         self.alt_text = alt_text
#         self.contents = contents

#     def render(self):
#         """把 Flex Message 渲染成文字版+按鈕清單"""
#         lines = [f"\n📦 [Flex: {self.alt_text}]"]
#         lines.append("─" * 50)

#         # 取出 header
#         bubble = self.contents
#         header = bubble.get('header', {})
#         if header:
#             for c in header.get('contents', []):
#                 if c.get('type') == 'text':
#                     lines.append(f"  {c.get('text', '')}")
#             lines.append("─" * 50)

#         # 取出 body
#         body = bubble.get('body', {})
#         buttons = []
#         for c in body.get('contents', []):
#             extract_text_and_buttons(c, lines, buttons, indent="  ")

#         # 取出 footer
#         footer = bubble.get('footer', {})
#         if footer:
#             lines.append("─" * 50)
#             for c in footer.get('contents', []):
#                 extract_text_and_buttons(c, lines, buttons, indent="  ")

#         # 列出可點擊的按鈕
#         if buttons:
#             lines.append("")
#             lines.append("👉 可點按鈕（輸入 >編號 來點）：")
#             for idx, btn in enumerate(buttons, 1):
#                 lines.append(f"   >{idx}  {btn['label']}")

#         return "\n".join(lines), buttons


# def extract_text_and_buttons(node, lines, buttons, indent=""):
#     """遞迴從 Flex 節點取出文字和按鈕"""
#     if not isinstance(node, dict):
#         return
#     t = node.get('type')
#     if t == 'text':
#         text = node.get('text', '').replace('\n', f'\n{indent}')
#         lines.append(f"{indent}{text}")
#     elif t == 'separator':
#         lines.append(f"{indent}─────")
#     elif t == 'button':
#         action = node.get('action', {})
#         label = action.get('label', '?')
#         action_type = action.get('type')
#         if action_type == 'message':
#             buttons.append({'label': label, 'type': 'message', 'text': action.get('text')})
#         elif action_type == 'postback':
#             buttons.append({'label': label, 'type': 'postback', 'data': action.get('data')})
#     elif t == 'box':
#         for c in node.get('contents', []):
#             extract_text_and_buttons(c, lines, buttons, indent=indent)


# class FakeLineBotApi:
#     """模擬 LINE Bot API，把訊息印在螢幕上"""
#     def __init__(self):
#         self.last_buttons = []  # 最近一次回的按鈕列表

#     def reply_message(self, reply_token, messages):
#         if not isinstance(messages, list):
#             messages = [messages]
#         all_buttons = []
#         for msg in messages:
#             if isinstance(msg, FlexSendMessage):
#                 rendered, buttons = msg.render()
#                 print(rendered)
#                 all_buttons.extend(buttons)
#             else:
#                 print(msg.render())
#         self.last_buttons = all_buttons


# line_bot_api = FakeLineBotApi()


# # ============ 模擬的 firestore.SERVER_TIMESTAMP ============
# class FakeFirestore:
#     SERVER_TIMESTAMP = "[SERVER_TIMESTAMP]"


# firestore = FakeFirestore()


# ============ 以下是從 app.py 抽出來的核心邏輯（去掉 LINE/Firebase 依賴）============
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
    
def get_photo_flex():
    base_dir = os.path.dirname(os.path.abspath(__file__))
    file_path = os.path.join(base_dir, 'photo.json')

    with open(file_path, 'r', encoding='utf-8') as f:
        contents = json.load(f)
    return FlexSendMessage(alt_text="請上傳照片", contents=contents)

def get_location_flex(item_type):
    # 根據是遺失還是撿到，讀取對應的檔案
    filename = 'find_place.json' if item_type == 'found' else 'lost_place.json'
    with open(filename, 'r', encoding='utf-8') as f:
        contents = json.load(f)
    return FlexSendMessage(alt_text="請選擇地點", contents=contents)
    
def get_main_menu():
    flex_content = {
        "type": "bubble",
        "body": {
            "type": "box", "layout": "vertical", "spacing": "md",
            "contents": [
                {"type": "text", "text": "🔍 失物招領", "weight": "bold", "size": "xl", "align": "center"},
                {"type": "text", "text": "請選擇你的狀況", "size": "sm", "color": "#888888", "align": "center"},
                {"type": "button", "style": "primary", "color": "#4CAF50",
                 "action": {"type": "message", "label": "📦 我撿到東西了", "text": "我撿到東西了"}},
                {"type": "button", "style": "primary", "color": "#2196F3",
                 "action": {"type": "message", "label": "🔎 我在找東西", "text": "我在找東西"}},
                {"type": "button", "style": "primary", "color": "#FF9800",
                 "action": {"type": "message", "label": "✅ 我找到了", "text": "我找到了"}},
                {"type": "button", "style": "secondary",
                 "action": {"type": "message", "label": "📋 查看所有失物", "text": "查看所有失物"}},
            ]
        }
    }
    return FlexSendMessage(alt_text="失物招領選單", contents=flex_content)


def save_item(user_id, session):
    _, doc = db.collection('items').add({
        'userId': user_id,
        'type': session.get('type'),
        'category': session.get('category'),
        'description': session.get('description'),
        'photo': session.get('photo'),
        'location': session.get('location'),
        'status': 'open',
        'createdAt': firestore.SERVER_TIMESTAMP
    })
    return doc.id


def get_open_items_for_found():
    docs = db.collection('items').where('status', '==', 'open').limit(20).stream()
    items = []
    for d in docs:
        data = d.to_dict()
        data['id'] = d.id
        items.append(data)
    return items


def build_lost_items_flex(items, selected_ids):
    if not items:
        return TextSendMessage(text="目前沒有可選擇的失物 😊")

    contents = []
    for idx, item in enumerate(items):
        is_selected = item['id'] in selected_ids
        icon = "☑️" if is_selected else "⬜"
        btn_label = "取消" if is_selected else "勾選"
        btn_style = "primary" if is_selected else "secondary"
        item_type = "撿到" if item.get('type') == 'found' else "遺失"

        row = {
            "type": "box", "layout": "horizontal", "spacing": "sm",
            "contents": [
                {"type": "text",
                 "text": f"{icon} 【{item_type}】{item.get('category', '')}\n{item.get('description', '')[:30]}",
                 "wrap": True, "size": "sm", "flex": 5},
                {"type": "button", "style": btn_style, "height": "sm", "flex": 3,
                 "action": {"type": "postback", "label": btn_label,
                            "data": f"action=toggle&id={item['id']}",
                            "displayText": f"{btn_label}：{item.get('category', '')}"}}
            ]
        }
        contents.append(row)
        if idx < len(items) - 1:
            contents.append({"type": "separator", "margin": "sm"})

    bubble = {
        "type": "bubble",
        "header": {
            "type": "box", "layout": "vertical",
            "contents": [
                {"type": "text", "text": "✅ 請勾選已找回的物品",
                 "weight": "bold", "size": "lg", "align": "center"},
                {"type": "text", "text": f"已選 {len(selected_ids)} 項（可複選）",
                 "size": "xs", "color": "#888888", "align": "center"}
            ]
        },
        "body": {"type": "box", "layout": "vertical", "spacing": "md", "contents": contents},
        "footer": {
            "type": "box", "layout": "vertical",
            "contents": [
                {"type": "button", "style": "primary", "color": "#FF9800",
                 "action": {"type": "postback", "label": "確定",
                            "data": "action=confirm", "displayText": "確定"}}
            ]
        }
    }
    return FlexSendMessage(alt_text="勾選已找回物品", contents=bubble)


def build_confirm_flex(selected_items):
    item_lines = []
    for item in selected_items:
        item_type = "撿到" if item.get('type') == 'found' else "遺失"
        item_lines.append({
            "type": "text",
            "text": f"• 【{item_type}】{item.get('category', '')} - {item.get('description', '')[:30]}",
            "wrap": True, "size": "sm", "margin": "sm"
        })

    bubble = {
        "type": "bubble",
        "body": {
            "type": "box", "layout": "vertical", "spacing": "md",
            "contents": [
                {"type": "text", "text": "⚠️ 確認你的選擇",
                 "weight": "bold", "size": "lg", "align": "center"},
                {"type": "text", "text": "以下物品將被標記為已找回：",
                 "size": "sm", "color": "#666666", "wrap": True},
                {"type": "separator", "margin": "md"},
                *item_lines,
                {"type": "separator", "margin": "md"}
            ]
        },
        "footer": {
            "type": "box", "layout": "horizontal", "spacing": "sm",
            "contents": [
                {"type": "button", "style": "secondary", "flex": 1,
                 "action": {"type": "postback", "label": "返回",
                            "data": "action=back", "displayText": "返回"}},
                {"type": "button", "style": "primary", "color": "#FF9800", "flex": 1,
                 "action": {"type": "postback", "label": "確定",
                            "data": "action=final_confirm", "displayText": "確定送出"}}
            ]
        }
    }
    return FlexSendMessage(alt_text="確認選擇", contents=bubble)


def build_readonly_list_flex(items):
    if not items:
        return TextSendMessage(text="🎉 目前沒有未處理的失物了！")

    contents = []
    for idx, item in enumerate(items):
        item_type = "撿到" if item.get('type') == 'found' else "遺失"
        contents.append({
            "type": "box", "layout": "vertical",
            "contents": [
                {"type": "text", "text": f"📦 【{item_type}】{item.get('category', '')}",
                 "weight": "bold", "size": "sm"},
                {"type": "text", "text": item.get('description', '')[:50],
                 "wrap": True, "size": "xs", "color": "#666666"}
            ]
        })
        if idx < len(items) - 1:
            contents.append({"type": "separator", "margin": "sm"})

    bubble = {
        "type": "bubble",
        "header": {
            "type": "box", "layout": "vertical",
            "contents": [
                {"type": "text", "text": "✅ 已更新",
                 "weight": "bold", "size": "lg", "align": "center"},
                {"type": "text", "text": "勾選的物品已被移除，剩餘清單如下",
                 "size": "xs", "color": "#888888", "align": "center", "wrap": True}
            ]
        },
        "body": {"type": "box", "layout": "vertical", "spacing": "md", "contents": contents}
    }
    return FlexSendMessage(alt_text="剩餘清單", contents=bubble)


# ============ 文字訊息處理 ============
def handle_message(user_id, text):
    text = text.strip()
    session = get_session(user_id)
    step = session.get("step")

    if text in ["選單", "開始", "menu", "取消"]:
        clear_session(user_id)
        line_bot_api.reply_message(None, get_main_menu())
        return

    if text == "我撿到東西了":
        set_session(user_id, {"type": "found", "step": "wait_category"})
        line_bot_api.reply_message(None, TextSendMessage(
            text="好的！請問撿到的是哪類物品？\n\n請回覆數字：\n1. 電子產品\n2. 衣物配件\n3. 證件錢包\n4. 鑰匙\n5. 其他"))
    elif text == "我在找東西":
        set_session(user_id, {"type": "lost", "step": "wait_category"})
        line_bot_api.reply_message(None, TextSendMessage(
            text="沒關係！請問遺失的是哪類物品？\n\n請回覆數字：\n1. 電子產品\n2. 衣物配件\n3. 證件錢包\n4. 鑰匙\n5. 其他"))
    elif text == "我找到了":
        items = get_open_items_for_found()
        if not items:
            clear_session(user_id)
            line_bot_api.reply_message(None, TextSendMessage(text="目前沒有可選擇的失物 😊"))
            return
        set_session(user_id, {"step": "selecting_found", "selected_items": []})
        line_bot_api.reply_message(None, build_lost_items_flex(items, []))
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
        line_bot_api.reply_message(None, TextSendMessage(text=reply))
    elif step == "wait_category":
        if text in CATEGORIES:
            session["category"] = CATEGORIES[text]
            session["step"] = "wait_description"
            set_session(user_id, session)
            line_bot_api.reply_message(None, TextSendMessage(
                text=f"分類：{CATEGORIES[text]} ✅\n\n請描述一下這個物品的外觀特徵（顏色、品牌、特殊記號等）"))
        else:
            line_bot_api.reply_message(None, TextSendMessage(text="請回覆 1~5 的數字選擇分類 😊"))
    elif step == "wait_description":
        session["description"] = text

        # 🌟 判斷是「尋找遺失物」還是「撿到東西」
        if session.get("type") == "lost":
            # === 【尋找遺失物】跳過拍照，直接彈出地點選擇按鈕 ===
            session["step"] = "wait_location_button"
            set_session(user_id, session)
            flex_msg = get_location_flex(session.get("type"))
            line_bot_api.reply_message(None, flex_msg)

        else:
            # === 【撿到東西】進入拍照步驟，彈出拍照按鈕 ===
            session["step"] = "wait_photo"
            set_session(user_id, session)
            flex_msg = get_photo_flex()
            line_bot_api.reply_message(None, flex_msg)
    elif step == "wait_photo" and text == "略過":
        session["photo"] = None
        session["step"] = "wait_location_button" # 更改 step 名稱，代表正在等待按鈕點擊
        set_session(user_id, session)
        # 改為傳送你設計好的 Flex Message 按鈕
        flex_msg = get_location_flex(session.get("type"))
        line_bot_api.reply_message(None, flex_msg)
    elif step == "wait_detailed_location":
        detailed_loc = text
        # 將大範圍與詳細描述組合起來
        final_location = f"{session.get('broad_location')} - {detailed_loc}"

        session["location"] = final_location
        set_session(user_id, session)
        item_id = save_item(user_id, session)
        clear_session(user_id)

        item_type = "撿到" if session.get("type") == "found" else "遺失"
        summary = (f"✅ 登記完成！\n\n類型：{item_type}\n分類：{session.get('category')}\n"
                   f"描述：{session.get('description')}\n地點：{final_location}")
        line_bot_api.reply_message(None, [TextSendMessage(text=summary), get_main_menu()])
    else:
        line_bot_api.reply_message(None, [TextSendMessage(text="請使用下方選單操作 👇"), get_main_menu()])
        
def handle_postback(user_id, data):
    params = parse_qs(data)
    action = params.get('action', [''])[0]
    session = get_session(user_id)

    if action == 'set_location':
        broad_location = params.get('loc', [''])[0]
        session['broad_location'] = broad_location

        if session.get('type') == 'lost':
            # === 【尋找遺失物流程】不存入資料庫，直接搜尋 ===

            # 從假資料庫尋找：狀態是open + 類型是撿到(found) + 分類跟你遺失的一樣
            matched_docs = db.collection('items') \
                .where('status', '==', 'open') \
                .where('type', '==', 'found') \
                .where('category', '==', session.get('category')) \
                .stream()

            match_results = []
            for d in matched_docs:
                data = d.to_dict()
                match_results.append(f"📦 {data.get('description')}\n📍 地點：{data.get('location')}")

            if match_results:
                match_text = f"🔍 系統為您比對到以下「目前有人撿到」的相似物品：\n\n" + "\n\n".join(match_results)
            else:
                # 拿掉登記協尋的文字
                match_text = f"目前在「{broad_location}」附近沒有人撿到類似的物品 🥺"

            clear_session(user_id) # 清除狀態
            line_bot_api.reply_message(None, [
                TextSendMessage(text=match_text),
                get_main_menu()
            ])

        else:
            # === 【撿到東西流程】繼續問詳細地點 ===
            session['step'] = 'wait_detailed_location'
            set_session(user_id, session)

            line_bot_api.reply_message(None, TextSendMessage(
                text=f"已選擇大範圍：{broad_location} ✅\n\n請輸入更詳細的地點描述（例如：靠近大樹的長椅上、警衛室旁）："))
    elif action == 'toggle':
        item_id = params.get('id', [''])[0]
        selected = session.get('selected_items', [])
        if item_id in selected:
            selected.remove(item_id)
        else:
            selected.append(item_id)
        session['selected_items'] = selected
        session['step'] = 'selecting_found'
        set_session(user_id, session)
        items = get_open_items_for_found()
        line_bot_api.reply_message(None, build_lost_items_flex(items, selected))
    elif action == 'confirm':
        selected = session.get('selected_items', [])
        if not selected:
            line_bot_api.reply_message(None, TextSendMessage(text="你還沒勾選任何物品喔 😊"))
            return
        items = get_open_items_for_found()
        selected_items = [i for i in items if i['id'] in selected]
        line_bot_api.reply_message(None, build_confirm_flex(selected_items))
    elif action == 'back':
        items = get_open_items_for_found()
        selected = session.get('selected_items', [])
        line_bot_api.reply_message(None, build_lost_items_flex(items, selected))
    elif action == 'final_confirm':
        selected = session.get('selected_items', [])
        for item_id in selected:
            db.collection('items').document(item_id).update({
                'status': 'closed',
                'closedAt': firestore.SERVER_TIMESTAMP
            })
        clear_session(user_id)
        items = get_open_items_for_found()
        line_bot_api.reply_message(None, build_readonly_list_flex(items))

# # ============ 主程式：互動式終端機 ============
# def main():
#     seed_data()
#     user_id = "test_user"

#     print("=" * 60)
#     print("🤖 LINE Bot 模擬器 啟動")
#     print("=" * 60)
#     print("操作方式：")
#     print("  - 直接輸入文字（例如：選單、我找到了）")
#     print("  - 點按鈕：輸入 >1, >2, >3 ...")
#     print("  - 查看狀態：dump")
#     print("  - 離開：q")
#     print("=" * 60)
#     print("\n💡 已預設 5 筆假物品在資料庫中，直接打「我找到了」開始測試\n")

#     while True:
#         try:
#             user_input = input("\n👤 你 > ").strip()
#         except (EOFError, KeyboardInterrupt):
#             print("\n再見！")
#             break

#         if not user_input:
#             continue
#         if user_input == 'q':
#             print("再見！")
#             break
#         if user_input == 'dump':
#             print("\n📊 === 目前資料庫狀態 ===")
#             print(f"\n[Session of {user_id}]")
#             print(f"  {get_session(user_id)}")
#             print(f"\n[Items]")
#             for item_id, item in db.collections['items'].items():
#                 print(f"  {item_id}: {item.get('status'):6} | {item.get('type'):5} | "
#                       f"{item.get('category')} - {item.get('description', '')[:30]}")
#             continue

#         # 點按鈕
#         if user_input.startswith('>'):
#             try:
#                 idx = int(user_input[1:]) - 1
#                 if 0 <= idx < len(line_bot_api.last_buttons):
#                     btn = line_bot_api.last_buttons[idx]
#                     print(f"   [點擊：{btn['label']}]")
#                     if btn['type'] == 'message':
#                         handle_message(user_id, btn['text'])
#                     elif btn['type'] == 'postback':
#                         handle_postback(user_id, btn['data'])
#                 else:
#                     print(f"   ⚠️  沒有編號 {idx+1} 的按鈕")
#             except ValueError:
#                 print("   ⚠️  格式錯誤，要打 >1 >2 這樣")
#             continue

#         # 一般訊息
#         handle_message(user_id, user_input)


# if __name__ == "__main__":
#     main()
