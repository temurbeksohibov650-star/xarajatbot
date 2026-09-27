import telebot
import sqlite3
import re
import time
import threading
from datetime import datetime, timedelta
from telebot import types

TOKEN = "8847982865:AAHjUTmrPIXXjZ5VMG-3Dqwjc3znsZdv9AM"

bot = telebot.TeleBot(TOKEN)

user_states = {}

def init_db():
    conn = sqlite3.connect('xarajatlar.db')
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS transactions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            type TEXT,
            amount REAL,
            category TEXT,
            date TEXT
        )
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS debts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            type TEXT,
            person TEXT,
            amount REAL,
            due_date TEXT,
            due_date_iso TEXT,
            notified_3days INTEGER DEFAULT 0,
            notified_1day INTEGER DEFAULT 0,
            status TEXT DEFAULT 'ochiq',
            created_at TEXT
        )
    ''')
    
    # Eski bazaga yetishmayotgan kolonkalarni xavfsiz qo'shish
    try:
        cursor.execute("ALTER TABLE debts ADD COLUMN due_date_iso TEXT")
    except sqlite3.OperationalError:
        pass
    try:
        cursor.execute("ALTER TABLE debts ADD COLUMN notified_3days INTEGER DEFAULT 0")
    except sqlite3.OperationalError:
        pass
    try:
        cursor.execute("ALTER TABLE debts ADD COLUMN notified_1day INTEGER DEFAULT 0")
    except sqlite3.OperationalError:
        pass

    conn.commit()
    conn.close()

init_db()

def main_keyboard():
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True)
    btn_stats = types.KeyboardButton("📊 Hisobot")
    btn_debts = types.KeyboardButton("🤝 Qarzlar")
    btn_manage = types.KeyboardButton("⚙️ Amallarni boshqarish (10 ta)")
    btn_help = types.KeyboardButton("ℹ️ Yordam")
    
    markup.row(btn_stats, btn_debts)
    markup.row(btn_manage, btn_help)
    return markup

@bot.message_handler(commands=['start'])
def send_welcome(message):
    bot.reply_to(
        message, 
        "Assalomu alaykum! Xarajatbotga xush kelibsiz.\n\n"
        "📖 <b>Foydalanish bo'yicha yo'riqnoma:</b>\n"
        "• <b>Xarajat:</b> <code>15000 tushlik</code>\n"
        "• <b>Daromad:</b> <code>+5000000 oylik</code>\n"
        "• <b>Qarz olish:</b> <code>15 oktyabrda Alidan 10000 qarz oldim</code>\n"
        "• <b>Qarz berish:</b> <code>Valiga 50000 qarz berdim 2026-10-25</code>\n\n"
        "Sana bilan kiritilgan qarzlar bo'yicha bot 3 kun va 1 kun avval eslatma yuboradi!",
        parse_mode='HTML',
        reply_markup=main_keyboard()
    )

def parse_date_from_text(text):
    now = datetime.now()
    months = {
        'yanvar': 1, 'fevral': 2, 'mart': 3, 'aprel': 4,
        'may': 5, 'iyun': 6, 'iyul': 7, 'avgust': 8,
        'sentyabr': 9, 'oktyabr': 10, 'noyabr': 11, 'dekabr': 12
    }
    
    match_iso = re.search(r'\b(\d{4})[-. /](\d{1,2})[-. /](\d{1,2})\b', text)
    if match_iso:
        y, m, d = int(match_iso.group(1)), int(match_iso.group(2)), int(match_iso.group(3))
        try:
            return datetime(y, m, d).strftime("%Y-%m-%d")
        except ValueError:
            pass

    match_str = re.search(r'\b(\d{1,2})[- ]?([a-z' ']+)\b', text.lower())
    if match_str:
        day = int(match_str.group(1))
        month_name = match_str.group(2)
        for m_name, m_num in months.items():
            if m_name in month_name:
                year = now.year
                if m_num < now.month or (m_num == now.month and day < now.day):
                    year += 1
                try:
                    return datetime(year, m_num, day).strftime("%Y-%m-%d")
                except ValueError:
                    pass
    return None

def parse_debt_text(text):
    lower_text = text.lower()
    debt_type = None

    if "qarz oldim" in lower_text or "qarz olgandim" in lower_text or "qarz olindi" in lower_text:
        debt_type = "qarz_oldim"
    elif "qarz berdim" in lower_text or "qarz bergandim" in lower_text or "qarz berildi" in lower_text:
        debt_type = "qarz_berdim"

    if not debt_type:
        return None

    numbers = re.findall(r'\d+', text)
    if not numbers:
        return None
    amount = float(numbers[0])

    iso_date = parse_date_from_text(text)

    stop_words = [
        "qarz", "oldim", "berdim", "olgandim", "bergandim", "olindi", "berildi",
        "so'm", "som", "da", "ga", "dan", "bugun", "ertaga", numbers[0]
    ]

    words = text.split()
    person_words = []
    date_words = []

    for word in words:
        clean_word = re.sub(r'[^\w\s-]', '', word)
        if clean_word.lower() in stop_words:
            continue
        if any(char.isdigit() for char in word) or any(m in word.lower() for m in ['yanvar','fevral','mart','aprel','may','iyun','iyul','avgust','sentyabr','oktyabr','noyabr','dekabr']):
            date_words.append(word)
        else:
            # Ism oxiridagi -dan, -ga, -da qo'shimchalarini tozalash
            clean_name = re.sub(r'(dan|ga|da)$', '', clean_word, flags=re.IGNORECASE)
            if clean_name:
                person_words.append(clean_name)

    person = " ".join(person_words) if person_words else "Noma'lum"
    due_date_str = " ".join(date_words) if date_words else ("Keltirilmagan" if not iso_date else iso_date)

    return {
        'type': debt_type,
        'amount': amount,
        'person': person.capitalize(),
        'due_date': due_date_str,
        'due_date_iso': iso_date
    }

def show_debts(message):
    user_id = message.from_user.id
    conn = sqlite3.connect('xarajatlar.db')
    cursor = conn.cursor()

    cursor.execute("SELECT id, type, person, amount, due_date FROM debts WHERE user_id = ? AND status = 'ochiq'", (user_id,))
    rows = cursor.fetchall()
    conn.close()

    if not rows:
        bot.reply_to(message, "🎉 Sizda hozircha hech qanday faol qarzlar yo'q!", reply_markup=main_keyboard())
        return

    text = "🤝 <b>Sizning faol qarzlaringiz:</b>\n\n"
    markup = types.InlineKeyboardMarkup()

    for row in rows:
        debt_id, debt_type, person, amount, due_date = row
        # Ismni qo'shimchalardan tozalash
        clean_person = re.sub(r'(dan|ga|da)$', '', person, flags=re.IGNORECASE).capitalize()

        if debt_type == "qarz_berdim":
            text += f"🔴 <b>{clean_person}</b> sizdan {amount:,.0f} so'm qarz olgan.\n"
            text += f"⌛️ Qaytarish muddati: {due_date}\n\n"
            btn_text = f"✅ {clean_person} qarzini qaytardi ({amount:,.0f} so'm)"
        else:
            text += f"🟢 Siz <b>{clean_person}</b>dan {amount:,.0f} so'm qarz olgansiz.\n"
            text += f"⌛️ Qaytarish muddati: {due_date}\n\n"
            btn_text = f"✅ {clean_person}ga qarzni qaytardim ({amount:,.0f} so'm)"

        markup.add(types.InlineKeyboardButton(text=btn_text, callback_data=f"closedebt_{debt_id}"))

    bot.reply_to(message, text, parse_mode='HTML', reply_markup=markup)

@bot.callback_query_handler(func=lambda call: call.data.startswith('closedebt_'))
def handle_close_debt(call):
    debt_id = call.data.split('_')[1]
    conn = sqlite3.connect('xarajatlar.db')
    cursor = conn.cursor()

    cursor.execute("UPDATE debts SET status = 'yopildi' WHERE id = ?", (debt_id,))
    conn.commit()
    conn.close()

    bot.answer_callback_query(call.id, text="Qarz yopildi va ro'yxatdan o'chirildi!")
    bot.edit_message_text(
        chat_id=call.message.chat.id,
        message_id=call.message.message_id,
        text="✅ <b>Qarz muvaffaqiyatli yopildi!</b>",
        parse_mode='HTML'
    )

def send_stats_period_options(message):
    markup = types.InlineKeyboardMarkup()
    btn_today = types.InlineKeyboardButton("📅 Bugun", callback_data="stats_today")
    btn_week = types.InlineKeyboardButton("📆 Shu hafta", callback_data="stats_week")
    btn_month = types.InlineKeyboardButton("🗓 Shu oy", callback_data="stats_month")
    btn_all = types.InlineKeyboardButton("📂 Barchasi", callback_data="stats_all")
    
    markup.row(btn_today, btn_week)
    markup.row(btn_month, btn_all)

    bot.reply_to(message, "📊 Qaysi davr uchun hisobotni ko'rmoqchisiz?", reply_markup=markup)

@bot.callback_query_handler(func=lambda call: call.data.startswith('stats_'))
def handle_stats_callback(call):
    period = call.data.split('_')[1]
    user_id = call.from_user.id
    now = datetime.now()

    conn = sqlite3.connect('xarajatlar.db')
    cursor = conn.cursor()

    if period == "today":
        title = "📅 Bugungi hisobot"
        start_date = now.strftime("%Y-%m-%d 00:00:00")
        cursor.execute('SELECT type, amount, category FROM transactions WHERE user_id = ? AND date >= ?', (user_id, start_date))
    elif period == "week":
        title = "📆 Oxirgi 7 kunlik hisobot"
        start_date = (now - timedelta(days=7)).strftime("%Y-%m-%d 00:00:00")
        cursor.execute('SELECT type, amount, category FROM transactions WHERE user_id = ? AND date >= ?', (user_id, start_date))
    elif period == "month":
        title = "🗓 Shu oylik hisobot"
        start_date = now.strftime("%Y-%m-01 00:00:00")
        cursor.execute('SELECT type, amount, category FROM transactions WHERE user_id = ? AND date >= ?', (user_id, start_date))
    else:
        title = "📂 Barcha vaqtlardagi hisobot"
        cursor.execute('SELECT type, amount, category FROM transactions WHERE user_id = ?', (user_id,))

    rows = cursor.fetchall()
    conn.close()

    bot.answer_callback_query(call.id)

    if not rows:
        bot.edit_message_text(
            chat_id=call.message.chat.id,
            message_id=call.message.message_id,
            text=f"{title}:\n\n⚠️ Ushbu davr bo'yicha hech qanday ma'lumot topilmadi."
        )
        return

    total_income = 0.0
    total_expense = 0.0
    details = ""

    for row in rows:
        trans_type, amount, category = row
        if trans_type == 'Daromad':
            total_income += amount
            details += f"🟢 +{amount:,.0f} so'm ({category})\n"
        else:
            total_expense += amount
            details += f"🔴 -{amount:,.0f} so'm ({category})\n"

    balance = total_income - total_expense

    report = f"<b>{title}:</b>\n\n"
    report += details
    report += f"\n-------------------\n"
    report += f"🟢 <b>Jami Daromad:</b> {total_income:,.0f} so'm\n"
    report += f"🔴 <b>Jami Xarajat:</b> {total_expense:,.0f} so'm\n"
    report += f"💵 <b>Sof Balans:</b> {balance:,.0f} so'm"

    bot.edit_message_text(
        chat_id=call.message.chat.id,
        message_id=call.message.message_id,
        text=report,
        parse_mode='HTML'
    )

def send_manage_options(message):
    user_id = message.from_user.id
    conn = sqlite3.connect('xarajatlar.db')
    cursor = conn.cursor()

    cursor.execute('SELECT id, type, amount, category FROM transactions WHERE user_id = ? ORDER BY id DESC LIMIT 10', (user_id,))
    rows = cursor.fetchall()
    conn.close()

    if not rows:
        bot.reply_to(message, "⚠️ Boshqarish uchun hech qanday ma'lumot topilmadi.", reply_markup=main_keyboard())
        return

    markup = types.InlineKeyboardMarkup()
    for row in rows:
        row_id, trans_type, amount, category = row
        icon = "🟢" if trans_type == "Daromad" else "🔴"
        btn_text = f"{icon} {category} - {amount:,.0f} so'm"
        callback_data = f"select_{row_id}"
        markup.add(types.InlineKeyboardButton(text=btn_text, callback_data=callback_data))

    bot.reply_to(message, "Oxirgi 10 ta amal. Qaysi birini o'chirmoqchisiz yoki tahrirlamoqchisiz?", reply_markup=markup)

@bot.callback_query_handler(func=lambda call: call.data.startswith('select_'))
def handle_select_callback(call):
    transaction_id = call.data.split('_')[1]
    
    markup = types.InlineKeyboardMarkup()
    btn_edit = types.InlineKeyboardButton("✏️ Tahrirlash", callback_data=f"edit_{transaction_id}")
    btn_delete = types.InlineKeyboardButton("🗑 O'chirish", callback_data=f"del_{transaction_id}")
    markup.row(btn_edit, btn_delete)

    bot.edit_message_text(
        chat_id=call.message.chat.id,
        message_id=call.message.message_id,
        text="Ushbu amal ustida nima qilmoqchisiz?",
        reply_markup=markup
    )

@bot.callback_query_handler(func=lambda call: call.data.startswith('del_'))
def handle_delete_callback(call):
    transaction_id = call.data.split('_')[1]
    
    conn = sqlite3.connect('xarajatlar.db')
    cursor = conn.cursor()
    cursor.execute('SELECT amount, category FROM transactions WHERE id = ?', (transaction_id,))
    row = cursor.fetchone()

    if row:
        amount, category = row
        cursor.execute('DELETE FROM transactions WHERE id = ?', (transaction_id,))
        conn.commit()
        conn.close()
        
        bot.answer_callback_query(call.id, text="O'chirildi!")
        bot.edit_message_text(
            chat_id=call.message.chat.id,
            message_id=call.message.message_id,
            text=f"✅ Muvaffaqiyatli o'chirildi:\n<b>{category}</b> - {amount:,.0f} so'm",
            parse_mode='HTML'
        )
    else:
        conn.close()
        bot.answer_callback_query(call.id, text="Bu ma'lumot topilmadi!")

@bot.callback_query_handler(func=lambda call: call.data.startswith('edit_'))
def handle_edit_callback(call):
    transaction_id = call.data.split('_')[1]
    user_id = call.from_user.id
    
    user_states[user_id] = {'editing_id': transaction_id}
    
    bot.answer_callback_query(call.id)
    bot.send_message(
        call.message.chat.id, 
        "✏️ Yangi summa va kategoriyani kiriting:\n\n<i>Xarajat bo'lsa: 15000 tushlik\nDaromad bo'lsa: +5000000 oylik</i>", 
        parse_mode='HTML'
    )

@bot.message_handler(func=lambda message: True)
def handle_message(message):
    user_id = message.from_user.id
    text = message.text.strip()

    if user_id in user_states and 'editing_id' in user_states[user_id]:
        editing_id = user_states[user_id]['editing_id']
        try:
            trans_type = "Xarajat"
            if text.startswith("+"):
                trans_type = "Daromad"
                text = text[1:].strip()

            parts = text.split()
            amount = float(parts[0])
            category = " ".join(parts[1:]) if len(parts) > 1 else ("Daromad" if trans_type == "Daromad" else "Umumiy")

            conn = sqlite3.connect('xarajatlar.db')
            cursor = conn.cursor()
            cursor.execute('''
                UPDATE transactions 
                SET type = ?, amount = ?, category = ? 
                WHERE id = ?
            ''', (trans_type, amount, category, editing_id))
            conn.commit()
            conn.close()

            del user_states[user_id]
            bot.reply_to(message, f"✅ Muvaffaqiyatli yangilandi:\nTur: {trans_type}\nSumma: {amount:,.0f} so'm\nKategoriya: {category}", reply_markup=main_keyboard())
            return
        except ValueError:
            bot.reply_to(message, "⚠️ Iltimos, to'g'ri formatda kiriting!\nMasalan: 15000 tushlik yoki +5000000 oylik")
            return

    if text == "📊 Hisobot" or text == "/stats":
        send_stats_period_options(message)
    elif text == "🤝 Qarzlar":
        show_debts(message)
    elif text == "⚙️ Amallarni boshqarish (10 ta)" or text == "❌ Xarajatni o'chirish":
        send_manage_options(message)
    elif text == "ℹ️ Yordam":
        bot.reply_to(
            message,
            "📖 <b>Botdan foydalanish usullari:</b>\n\n"
            "🔴 <b>Xarajat:</b> <code>15000 tushlik</code>\n"
            "🟢 <b>Daromad:</b> <code>+5000000 oylik</code>\n"
            "🤝 <b>Qarz olish:</b> <code>15 oktyabrda Alidan 10000 qarz oldim</code>\n"
            "🤝 <b>Qarz berish:</b> <code>Valiga 50000 qarz berdim 2026-10-25</code>",
            parse_mode='HTML',
            reply_markup=main_keyboard()
        )
    else:
        debt_data = parse_debt_text(text)
        if debt_data:
            created_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            conn = sqlite3.connect('xarajatlar.db')
            cursor = conn.cursor()
            cursor.execute('''
                INSERT INTO debts (user_id, type, person, amount, due_date, due_date_iso, status, created_at)
                VALUES (?, ?, ?, ?, ?, ?, 'ochiq', ?)
            ''', (user_id, debt_data['type'], debt_data['person'], debt_data['amount'], debt_data['due_date'], debt_data['due_date_iso'], created_at))
            conn.commit()
            conn.close()

            reminder_info = "\n🔔 <i>(Sana belgilandi: Qaytarish kuniga 3 va 1 kun qolganda eslatma yuboriladi)</i>" if debt_data['due_date_iso'] else ""

            clean_person = re.sub(r'(dan|ga|da)$', '', debt_data['person'], flags=re.IGNORECASE).capitalize()

            if debt_data['type'] == "qarz_berdim":
                msg = f"🤝 <b>Qarz berilgani saqlandi:</b>\n👤 Shaxs: {clean_person}\n💰 Summa: {debt_data['amount']:,.0f} so'm\n⌛️ Muddat: {debt_data['due_date']}{reminder_info}"
            else:
                msg = f"🤝 <b>Qarz olingani saqlandi:</b>\n👤 Kimdan: {clean_person}\n💰 Summa: {debt_data['amount']:,.0f} so'm\n⌛️ Muddat: {debt_data['due_date']}{reminder_info}"

            bot.reply_to(message, msg, parse_mode='HTML', reply_markup=main_keyboard())
            return

        try:
            trans_type = "Xarajat"
            if text.startswith("+"):
                trans_type = "Daromad"
                text = text[1:].strip()

            parts = text.split()
            amount = float(parts[0])
            category = " ".join(parts[1:]) if len(parts) > 1 else ("Daromad" if trans_type == "Daromad" else "Umumiy")
            date_now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

            conn = sqlite3.connect('xarajatlar.db')
            cursor = conn.cursor()
            cursor.execute('''
                INSERT INTO transactions (user_id, type, amount, category, date)
                VALUES (?, ?, ?, ?, ?)
            ''', (user_id, trans_type, amount, category, date_now))
            conn.commit()
            conn.close()

            icon = "🟢" if trans_type == "Daromad" else "🔴"
            bot.reply_to(message, f"{icon} Saqlandi ({trans_type}):\nSumma: {amount:,.0f} so'm\nKategoriya: {category}", reply_markup=main_keyboard())

        except ValueError:
            bot.reply_to(message, "⚠️ Iltimos, ma'lumotni to'g'ri formatda kiriting!\n\nMasalan:\n• 15000 tushlik\n• +5000000 oylik\n• Alidan 10000 qarz oldim 15-oktyabrda", reply_markup=main_keyboard())
        except Exception as e:
            bot.reply_to(message, f"Xatolik yuz berdi: {e}", reply_markup=main_keyboard())

def check_debt_reminders():
    while True:
        try:
            today = datetime.now().date()
            conn = sqlite3.connect('xarajatlar.db')
            cursor = conn.cursor()
            cursor.execute("SELECT id, user_id, type, person, amount, due_date, due_date_iso, notified_3days, notified_1day FROM debts WHERE status = 'ochiq' AND due_date_iso IS NOT NULL")
            rows = cursor.fetchall()

            for row in rows:
                debt_id, user_id, debt_type, person, amount, due_date, due_date_iso, notif_3, notif_1 = row
                clean_person = re.sub(r'(dan|ga|da)$', '', person, flags=re.IGNORECASE).capitalize()

                try:
                    due_date_obj = datetime.strptime(due_date_iso, "%Y-%m-%d").date()
                    days_left = (due_date_obj - today).days

                    if days_left == 3 and notif_3 == 0:
                        if debt_type == "qarz_oldim":
                            msg = f"⏰ <b>QARZ ESLATMASI (3 kun qoldi)!</b>\n\nSiz <b>{clean_person}</b>dan olgan <b>{amount:,.0f} so'm</b> qarzni qaytarish vaqtingizga 3 kun qoldi.\n⌛️ Qaytarish kuni: {due_date}"
                        else:
                            msg = f"⏰ <b>QARZ ESLATMASI (3 kun qoldi)!</b>\n\nSiz <b>{clean_person}</b>ga bergan <b>{amount:,.0f} so'm</b> qarzni qaytarib olish vaqtingizga 3 kun qoldi.\n⌛️ Qaytarish kuni: {due_date}"
                        
                        bot.send_message(user_id, msg, parse_mode='HTML')
                        cursor.execute("UPDATE debts SET notified_3days = 1 WHERE id = ?", (debt_id,))
                        conn.commit()

                    elif days_left == 1 and notif_1 == 0:
                        if debt_type == "qarz_oldim":
                            msg = f"🔴 <b>SHOSHILING! QARZ ESLATMASI (1 kun qoldi)!</b>\n\nSiz <b>{clean_person}</b>dan olgan <b>{amount:,.0f} so'm</b> qarzni ertaga qaytarishingiz kerak!\n⌛️ Qaytarish kuni: {due_date}"
                        else:
                            msg = f"🔴 <b>QARZ ESLATMASI (1 kun qoldi)!</b>\n\nSiz <b>{clean_person}</b>ga bergan <b>{amount:,.0f} so'm</b> qarzni ertaga qaytarib olishingiz kerak!\n⌛️ Qaytarish kuni: {due_date}"
                        
                        bot.send_message(user_id, msg, parse_mode='HTML')
                        cursor.execute("UPDATE debts SET notified_1day = 1 WHERE id = ?", (debt_id,))
                        conn.commit()

                except Exception as ex:
                    print(f"Sana hisoblashda xatolik: {ex}")

            conn.close()
        except Exception as e:
            print(f"Eslatma tekshirishda xatolik: {e}")

        time.sleep(3600)

reminder_thread = threading.Thread(target=check_debt_reminders, daemon=True)
reminder_thread.start()

print("Bot va avtomatik eslatmalar tizimi ishga tushdi...")
bot.infinity_polling(timeout=60, long_polling_timeout=60)