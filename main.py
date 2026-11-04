import os
import sqlite3
import re
import telebot
from telebot import types

# ================= إعدادات البوت =================
TOKEN = os.environ.get("BOT_TOKEN")
bot = telebot.TeleBot(TOKEN)

# ================= تهيئة قاعدة البيانات الديناميكية =================
def init_db():
    conn = sqlite3.connect("store.db")
    cursor = conn.cursor()
    
    # جدول المنتجات
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS products (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            price REAL NOT NULL,
            category TEXT NOT NULL,
            image_id TEXT NOT NULL,
            stock INTEGER DEFAULT 0
        )
    """)
    
    # جدول الطلبات
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS orders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            name TEXT,
            phone TEXT,
            address TEXT,
            email TEXT,
            items TEXT,
            status TEXT DEFAULT 'قيد المعالجة'
        )
    """)
    
    # جدول إعدادات المتجر (لحفظ معرف المالك ديناميكياً)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT
        )
    """)
    
    conn.commit()
    conn.close()

init_db()

# دالة لجلب معرف المالك ديناميكياً من قاعدة البيانات
def get_admin_id():
    conn = sqlite3.connect("store.db")
    cursor = conn.cursor()
    cursor.execute("SELECT value FROM settings WHERE key = 'admin_id'")
    row = cursor.fetchone()
    conn.close()
    return int(row[0]) if row else None

# دالة لتسجيل المالك لأول مرة
def set_admin_id(user_id):
    conn = sqlite3.connect("store.db")
    cursor = conn.cursor()
    cursor.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('admin_id', ?)", (str(user_id),))
    conn.commit()
    conn.close()

# القواميس المؤقتة للذاكرة
user_carts = {}
admin_states = {}
user_checkout_states = {}
user_inquiry_states = {}

# ================= القائمة الرئيسية الديناميكية =================
def main_menu(user_id):
    markup = types.InlineKeyboardMarkup(row_width=2)
    
    conn = sqlite3.connect("store.db")
    cursor = conn.cursor()
    cursor.execute("SELECT DISTINCT category FROM products")
    categories = cursor.fetchall()
    conn.close()
    
    for cat in categories:
        markup.add(types.InlineKeyboardButton(f"📁 قسم: {cat[0]}", callback_data=f"cat_{cat[0]}"))
    
    markup.add(
        types.InlineKeyboardButton("🛒 سلة المشتريات", callback_data="view_cart"),
        types.InlineKeyboardButton("📦 إتمام الشراء", callback_data="checkout")
    )
    markup.add(
        types.InlineKeyboardButton("🔍 عرض حالة الطلب", callback_data="check_my_orders")
    )
    
    # التعرف الديناميكي على المالك
    current_admin = get_admin_id()
    if current_admin and user_id == current_admin:
        markup.add(types.InlineKeyboardButton("⚙️ لوحة التحكم (المدير)", callback_data="admin_panel"))
        
    return markup

# ================= أوامر التشغيل والتعرف على المالك =================
@bot.message_handler(commands=['start'])
def send_welcome(message):
    user_id = message.from_user.id
    current_admin = get_admin_id()
    
    if not current_admin:
        set_admin_id(user_id)
        bot.send_message(message.chat.id, "🎉 مبروك! تم تسجيلك كـ **مالك ومدير هذا المتجر** بنجاح.", parse_mode="Markdown")
    
    welcome_text = (
        "أهلاً بك في متجرنا الإلكتروني! 🛍️\n\n"
        "تستطيع تصفح الأقسام، إضافة المنتجات للسلة، وإتمام طلبك بكل سهولة.\n"
        "اختر ما يناسبك من القائمة أدناه:"
    )
    bot.send_message(message.chat.id, welcome_text, reply_markup=main_menu(user_id))

# ================= عرض المنتجات وفحص المخزون =================
@bot.callback_query_handler(func=lambda call: call.data.startswith('cat_'))
def show_category_products(call):
    category = call.data.split('_', 1)[1]
    conn = sqlite3.connect("store.db")
    cursor = conn.cursor()
    cursor.execute("SELECT id, name, price, image_id, stock FROM products WHERE category = ?", (category,))
    products = cursor.fetchall()
    conn.close()
    
    if not products:
        bot.answer_callback_query(call.id, "عذراً، لا توجد منتجات في هذا القسم حالياً.", show_alert=True)
        return
    
    bot.answer_callback_query(call.id)
    for prod in products:
        p_id, name, price, image_id, stock = prod
        
        # عرض معرف المنتج (ID) ليتمكن المدير من معرفته للتعديل أو الحذف
        admin_id = get_admin_id()
        id_label = f" 🆔 [#{p_id}]" if admin_id and call.from_user.id == admin_id else ""
        
        if stock > 0:
            caption = f"📦 **{name}**{id_label}\n💰 السعر: {price} ج.س\n📦 المتبقي في المخزون: {stock}"
            markup = types.InlineKeyboardMarkup()
            markup.add(types.InlineKeyboardButton("➕ أضف للسلة", callback_data=f"add_{p_id}"))
        else:
            caption = f"📦 **{name}**{id_label}\n💰 السعر: {price} ج.س\n❌ **عذراً، نفدت الكمية!**"
            markup = None
        
        try:
            bot.send_photo(call.message.chat.id, image_id, caption=caption, parse_mode="Markdown", reply_markup=markup)
        except Exception:
            bot.send_message(call.message.chat.id, f"{caption}\n(تعذر عرض الصورة)", reply_markup=markup)

# ================= إدارة السلة =================
@bot.callback_query_handler(func=lambda call: call.data.startswith('add_'))
def add_to_cart(call):
    p_id = int(call.data.split('_')[1])
    user_id = call.from_user.id
    
    conn = sqlite3.connect("store.db")
    cursor = conn.cursor()
    cursor.execute("SELECT stock FROM products WHERE id = ?", (p_id,))
    prod = cursor.fetchone()
    conn.close()
    
    if not prod or prod[0] <= 0:
        bot.answer_callback_query(call.id, "❌ عذراً، هذا المنتج نفد من المخزون!", show_alert=True)
        return
        
    if user_id not in user_carts:
        user_carts[user_id] = []
    
    user_carts[user_id].append(p_id)
    bot.answer_callback_query(call.id, "✅ تم إضافة المنتج إلى السلة!")

@bot.callback_query_handler(func=lambda call: call.data == 'view_cart')
def view_cart(call):
    user_id = call.from_user.id
    cart = user_carts.get(user_id, [])
    
    if not cart:
        bot.answer_callback_query(call.id, "سلة المشتريات فارغة!", show_alert=True)
        return
    
    conn = sqlite3.connect("store.db")
    cursor = conn.cursor()
    
    items_summary = {}
    for p_id in cart:
        items_summary[p_id] = items_summary.get(p_id, 0) + 1
        
    text = "🛒 **محتويات سلة المشتريات:**\n\n"
    total_price = 0
    
    for p_id, count in items_summary.items():
        cursor.execute("SELECT name, price FROM products WHERE id = ?", (p_id,))
        prod = cursor.fetchone()
        if prod:
            name, price = prod
            subtotal = price * count
            total_price += subtotal
            text += f"▪️ {name} (العدد: {count}) - المجموع: {subtotal} ج.س\n"
            
    conn.close()
    text += f"\n💰 **المجموع الكلي:** {total_price} ج.س"
    
    bot.answer_callback_query(call.id)
    bot.send_message(call.message.chat.id, text, parse_mode="Markdown")

# ================= إتمام الطلب وتحديث المخزون تلقائياً =================
@bot.callback_query_handler(func=lambda call: call.data == 'checkout')
def checkout_start(call):
    user_id = call.from_user.id
    cart = user_carts.get(user_id, [])
    
    if not cart:
        bot.answer_callback_query(call.id, "سلة المشتريات فارغة تماماً!", show_alert=True)
        return
        
    bot.answer_callback_query(call.id)
    user_checkout_states[user_id] = {"step": "get_name", "items": cart}
    bot.send_message(call.message.chat.id, "📝 لإتمام الطلب، يرجى كتابة **اسمك الكامل**:")

@bot.message_handler(func=lambda m: m.from_user.id in user_checkout_states)
def process_checkout(message):
    user_id = message.from_user.id
    state = user_checkout_states[user_id]
    step = state["step"]
    
    if step == "get_name":
        state["name"] = message.text
        state["step"] = "get_phone"
        bot.send_message(message.chat.id, "📱 يرجى إدخال **رقم هاتفك**:")
        
    elif step == "get_phone":
        phone = message.text.strip()
        if not re.match(r'^\+?[0-9]{9,15}$', phone):
            bot.send_message(message.chat.id, "❌ رقم الهاتف غير صحيح. أعد إدخاله:")
            return
        state["phone"] = phone
        state["step"] = "get_address"
        bot.send_message(message.chat.id, "🏠 يرجى إدخال **عنوان الشحن بالتفصيل**:")
        
    elif step == "get_address":
        state["address"] = message.text
        state["step"] = "get_email"
        bot.send_message(message.chat.id, "✉️ يرجى إدخال **بريدك الإلكتروني**:")
        
    elif step == "get_email":
        email = message.text.strip()
        if not re.match(r'^[\w\.-]+@[\w\.-]+\.\w+$', email):
            bot.send_message(message.chat.id, "❌ البريد الإلكتروني غير صحيح. أعد إدخاله:")
            return
        
        state["email"] = email
        
        conn = sqlite3.connect("store.db")
        cursor = conn.cursor()
        items_str = ",".join(map(str, state["items"]))
        
        cursor.execute("""
            INSERT INTO orders (user_id, name, phone, address, email, items, status)
            VALUES (?, ?, ?, ?, ?, ?, 'قيد المعالجة')
        """, (user_id, state["name"], state["phone"], state["address"], email, items_str))
        
        order_id = cursor.lastrowid
        
        for p_id in state["items"]:
            cursor.execute("UPDATE products SET stock = stock - 1 WHERE id = ? AND stock > 0", (p_id,))
            
        conn.commit()
        conn.close()
        
        admin_id = get_admin_id()
        admin_msg = (
            f"📦 **طلب جديد! (رقم الطلب: #{order_id})**\n\n"
            f"👤 الاسم: {state['name']}\n"
            f"📱 الهاتف: {state['phone']}\n"
            f"🏠 العنوان: {state['address']}\n"
            f"✉️ البريد: {email}\n"
            f"🛒 المنتجات (IDs): {items_str}"
        )
        if admin_id:
            try:
                bot.send_message(admin_id, admin_msg, parse_mode="Markdown")
            except Exception:
                pass
            
        bot.send_message(message.chat.id, f"✅ تم إرسال طلبك بنجاح!\nرقم طلبك هو: **#{order_id}**\nسنقوم بالتواصل معك قريباً.")
        
        user_carts[user_id] = []
        del user_checkout_states[user_id]

# ================= استعلام الزبون عن حالة طلبه =================
@bot.callback_query_handler(func=lambda call: call.data == 'check_my_orders')
def check_order_status_start(call):
    user_id = call.from_user.id
    bot.answer_callback_query(call.id)
    user_inquiry_states[user_id] = True
    bot.send_message(call.message.chat.id, "🔍 أرسل الآن **رقم الطلب** الخاص بك (مثال: 1):")

@bot.message_handler(func=lambda m: m.from_user.id in user_inquiry_states)
def fetch_order_status_for_customer(message):
    user_id = message.from_user.id
    try:
        order_id = int(message.text.strip())
        conn = sqlite3.connect("store.db")
        cursor = conn.cursor()
        cursor.execute("SELECT id, status, name FROM orders WHERE id = ?", (order_id,))
        order = cursor.fetchone()
        conn.close()
        
        if order:
            o_id, status, name = order
            bot.send_message(message.chat.id, f"📦 **حالة الطلب #{o_id}**\n👤 اسم العميل: {name}\n📌 الحالة الحالية: *{status}*", parse_mode="Markdown")
        else:
            bot.send_message(message.chat.id, "❌ لم يتم العثور على طلب بهذا الرقم. تأكد من الرقم وأعد المحاولة:")
            return
            
    except ValueError:
        bot.send_message(message.chat.id, "❌ يرجى إدخال رقم طلب صحيح (أرقام فقط):")
        return
        
    del user_inquiry_states[user_id]

# ================= لوحة التحكم الديناميكية للمدير =================
@bot.callback_query_handler(func=lambda call: call.data == 'admin_panel' and call.from_user.id == get_admin_id())
def admin_panel(call):
    markup = types.InlineKeyboardMarkup(row_width=2)
    markup.add(
        types.InlineKeyboardButton("➕ إضافة منتج مع المخزون", callback_data="adm_add"),
        types.InlineKeyboardButton("🗑️ حذف منتج", callback_data="adm_del"),
        types.InlineKeyboardButton("✏️ تعديل منتج", callback_data="adm_edit"),
        types.InlineKeyboardButton("📦 عرض أحدث الطلبات", callback_data="adm_orders"),
        types.InlineKeyboardButton("🔄 تعديل حالة طلب", callback_data="adm_ch_status")
    )
    markup.add(types.InlineKeyboardButton("⬅️ عودة للقائمة الرئيسية", callback_data="back_home"))
    
    bot.answer_callback_query(call.id)
    bot.edit_message_text("⚙️ **لوحة تحكم المدير:**\nاختر العملية المطلوبة:", call.message.chat.id, call.message.message_id, parse_mode="Markdown", reply_markup=markup)

@bot.callback_query_handler(func=lambda call: call.data == 'back_home')
def back_home(call):
    admin_id = call.from_user.id
    for st_dict in [admin_states, user_checkout_states, user_inquiry_states]:
        if admin_id in st_dict:
            del st_dict[admin_id]
    bot.edit_message_text("أهلاً بك مجدداً:", call.message.chat.id, call.message.message_id, reply_markup=main_menu(admin_id))

# --- 1. إضافة منتج جديد ---
@bot.callback_query_handler(func=lambda call: call.data == 'adm_add' and call.from_user.id == get_admin_id())
def admin_add_start(call):
    admin_states[get_admin_id()] = {"step": "p_name"}
    bot.answer_callback_query(call.id)
    bot.send_message(call.message.chat.id, "➕ أدخل **اسم المنتج الجديد**:")

@bot.message_handler(func=lambda m: m.from_user.id == get_admin_id() and get_admin_id() in admin_states and admin_states[get_admin_id()].get("step") in ["p_name", "p_price", "p_stock", "p_category"])
def admin_add_steps(message):
    admin_id = get_admin_id()
    state = admin_states[admin_id]
    step = state["step"]
    
    if step == "p_name":
        state["name"] = message.text
        state["step"] = "p_price"
        bot.send_message(message.chat.id, "💰 أدخل **سعر المنتج** (أرقام فقط، مثلاً: 5000):")
        
    elif step == "p_price":
        try:
            state["price"] = float(message.text)
            state["step"] = "p_stock"
            bot.send_message(message.chat.id, "📦 أدخل **الكمية المتوفرة في المخزون** (أرقام صحيحة، مثلاً: 10):")
        except ValueError:
            bot.send_message(message.chat.id, "❌ السعر غير صحيح. أدخل رقماً فقط:")
            
    elif step == "p_stock":
        try:
            state["stock"] = int(message.text)
            state["step"] = "p_category"
            bot.send_message(message.chat.id, "📁 أدخل **اسم القسم** التابع له المنتج:")
        except ValueError:
            bot.send_message(message.chat.id, "❌ الكمية غير صحيحة. أدخل رقماً صحيحاً:")
            
    elif step == "p_category":
        state["category"] = message.text.strip()
        state["step"] = "p_image"
        bot.send_message(message.chat.id, "📷 الآن قم **بإرسال صورة المنتج**:")

@bot.message_handler(content_types=['photo'], func=lambda m: m.from_user.id == get_admin_id() and get_admin_id() in admin_states and admin_states[get_admin_id()].get("step") == "p_image")
def admin_save_product(message):
    admin_id = get_admin_id()
    state = admin_states[admin_id]
    
    image_id = message.photo[-1].file_id
    
    conn = sqlite3.connect("store.db")
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO products (name, price, category, image_id, stock)
        VALUES (?, ?, ?, ?, ?)
    """, (state["name"], state["price"], state["category"], image_id, state["stock"]))
    conn.commit()
    conn.close()
    
    bot.send_message(message.chat.id, f"✅ تم إضافة المنتج '{state['name']}' بنجاح في قسم '{state['category']}' (المخزون: {state['stock']})!")
    del admin_states[admin_id]

# --- 2. حذف منتج ---
@bot.callback_query_handler(func=lambda call: call.data == 'adm_del' and call.from_user.id == get_admin_id())
def admin_delete_prompt(call):
    bot.answer_callback_query(call.id)
    admin_states[get_admin_id()] = {"step": "get_product_id_for_delete"}
    bot.send_message(call.message.chat.id, "🗑️ أدخل **معرف المنتج (ID)** الذي تريد حذفه (تستطيع معرفته من تصفح الأقسام):")

@bot.message_handler(func=lambda m: m.from_user.id == get_admin_id() and get_admin_id() in admin_states and admin_states[get_admin_id()].get("step") == "get_product_id_for_delete")
def execute_delete_product(message):
    admin_id = get_admin_id()
    try:
        p_id = int(message.text.strip())
        conn = sqlite3.connect("store.db")
        cursor = conn.cursor()
        cursor.execute("SELECT name FROM products WHERE id = ?", (p_id,))
        prod = cursor.fetchone()
        
        if not prod:
            conn.close()
            bot.send_message(message.chat.id, "❌ لم يتم العثور على منتج بهذا المعرف (ID). أعد المحاولة:")
            return
            
        cursor.execute("DELETE FROM products WHERE id = ?", (p_id,))
        conn.commit()
        conn.close()
        
        bot.send_message(message.chat.id, f"✅ تم حذف المنتج '{prod[0]}' (ID: {p_id}) بنجاح!")
    except ValueError:
        bot.send_message(message.chat.id, "❌ يرجى إدخال رقم معرف صحيح (أرقام فقط):")
        return
        
    del admin_states[admin_id]

# --- 3. تعديل منتج (السعر والمخزون) ---
@bot.callback_query_handler(func=lambda call: call.data == 'adm_edit' and call.from_user.id == get_admin_id())
def admin_edit_prompt(call):
    bot.answer_callback_query(call.id)
    admin_states[get_admin_id()] = {"step": "edit_get_id"}
    bot.send_message(call.message.chat.id, "✏️ أدخل **معرف المنتج (ID)** الذي تريد تعديله:")

@bot.message_handler(func=lambda m: m.from_user.id == get_admin_id() and get_admin_id() in admin_states and admin_states[get_admin_id()].get("step") in ["edit_get_id", "edit_new_price", "edit_new_stock"])
def admin_edit_steps(message):
    admin_id = get_admin_id()
    state = admin_states[admin_id]
    step = state["step"]
    
    if step == "edit_get_id":
        try:
            p_id = int(message.text.strip())
            conn = sqlite3.connect("store.db")
            cursor = conn.cursor()
            cursor.execute("SELECT id, name, price, stock FROM products WHERE id = ?", (p_id,))
            prod = cursor.fetchone()
            conn.close()
            
            if not prod:
                bot.send_message(message.chat.id, "❌ لم يتم العثور على منتج بهذا المعرف. أعد المحاولة:")
                return
                
            state["product_id"] = p_id
            state["step"] = "edit_new_price"
            bot.send_message(message.chat.id, f"📦 المنتج: **{prod[1]}**\n💰 السعر الحالي: {prod[2]} | المخزون: {prod[3]}\n\nأدخل **السعر الجديد**:")
        except ValueError:
            bot.send_message(message.chat.id, "❌ يرجى إدخال رقم معرف صحيح (أرقام فقط):")
            
    elif step == "edit_new_price":
        try:
            state["new_price"] = float(message.text)
            state["step"] = "edit_new_stock"
            bot.send_message(message.chat.id, "📦 أدخل **الكمية الجديدة في المخزون**:")
        except ValueError:
            bot.send_message(message.chat.id, "❌ السعر غير صحيح. أدخل رقماً فقط:")
            
    elif step == "edit_new_stock":
        try:
            new_stock = int(message.text)
            p_id = state["product_id"]
            new_price = state["new_price"]
            
            conn = sqlite3.connect("store.db")
            cursor = conn.cursor()
            cursor.execute("UPDATE products SET price = ?, stock = ? WHERE id = ?", (new_price, new_stock, p_id))
            conn.commit()
            conn.close()
            
            bot.send_message(message.chat.id, f"✅ تم تحديث بيانات المنتج (ID: {p_id}) بنجاح!\n💰 السعر الجديد: {new_price} ج.س\n📦 المخزون الجديد: {new_stock}")
            del admin_states[admin_id]
        except ValueError:
            bot.send_message(message.chat.id, "❌ الكمية غير صحيحة. أدخل رقماً صحيحاً:")

# --- 4. عرض الطلبات وإدارتها ---
@bot.callback_query_handler(func=lambda call: call.data == 'adm_orders' and call.from_user.id == get_admin_id())
def admin_view_orders(call):
    conn = sqlite3.connect("store.db")
    cursor = conn.cursor()
    cursor.execute("SELECT id, name, phone, address, items, status FROM orders ORDER BY id DESC LIMIT 5")
    orders = cursor.fetchall()
    conn.close()
    
    if not orders:
        bot.answer_callback_query(call.id, "لا توجد طلبات حالياً.", show_alert=True)
        return
        
    bot.answer_callback_query(call.id)
    text = "📦 **أحدث 5 طلبات مسجلة:**\n\n"
    for ord_info in orders:
        o_id, name, phone, address, items, status = ord_info
        text += f"🔹 **طلب #{o_id}**\n👤 الاسم: {name}\n📱 الهاتف: {phone}\n🏠 العنوان: {address}\n🛒 المنتجات (IDs): {items}\n📌 الحالة: *{status}*\n-------------------\n"
        
    bot.send_message(message.chat.id, text, parse_mode="Markdown")

@bot.callback_query_handler(func=lambda call: call.data == 'adm_ch_status' and call.from_user.id == get_admin_id())
def admin_change_status_prompt(call):
    bot.answer_callback_query(call.id)
    admin_states[get_admin_id()] = {"step": "get_order_id_for_status"}
    bot.send_message(call.message.chat.id, "🔄 أدخل **رقم الطلب** الذي تريد تغيير حالته:")

@bot.message_handler(func=lambda m: m.from_user.id == get_admin_id() and get_admin_id() in admin_states and admin_states[get_admin_id()].get("step") == "get_order_id_for_status")
def get_order_for_status(message):
    admin_id = get_admin_id()
    try:
        o_id = int(message.text.strip())
        conn = sqlite3.connect("store.db")
        cursor = conn.cursor()
        cursor.execute("SELECT id FROM orders WHERE id = ?", (o_id,))
        exists = cursor.fetchone()
        conn.close()
        
        if not exists:
            bot.send_message(message.chat.id, "❌ رقم الطلب غير موجود. حاول مجددا:")
            return
            
        admin_states[admin_id] = {"step": "get_new_status", "order_id": o_id}
        bot.send_message(message.chat.id, f"📝 أدخل **الحالة الجديدة** للطلب #{o_id} (مثل: تم التوصيل، قيد الشحن):")
    except ValueError:
        bot.send_message(message.chat.id, "❌ أدخل رقماً صحيحاً:")

@bot.message_handler(func=lambda m: m.from_user.id == get_admin_id() and get_admin_id() in admin_states and admin_states[get_admin_id()].get("step") == "get_new_status")
def save_new_status(message):
    admin_id = get_admin_id()
    state = admin_states[admin_id]
    o_id = state["order_id"]
    new_status = message.text.strip()
    
    conn = sqlite3.connect("store.db")
    cursor = conn.cursor()
    cursor.execute("UPDATE orders SET status = ? WHERE id = ?", (new_status, o_id))
    conn.commit()
    conn.close()
    
    bot.send_message(message.chat.id, f"✅ تم تحديث حالة الطلب #{o_id} لتصبح: *{new_status}*", parse_mode="Markdown")
    del admin_states[admin_id]

# ================= تشغيل البوت =================
if __name__ == "__main__":
    print("Commercial Store Bot is running...")
    bot.infinity_polling()
