import os
import io
import math
import logging
import threading
from datetime import datetime
from http.server import HTTPServer, BaseHTTPRequestHandler
from google import generativeai as genai
from PIL import Image
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReactionTypeEmoji
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
)
from telegram.request import HTTPXRequest

# إعداد التسجيل (Logging)
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)

# قراءة المفاتيح آمنة عبر متغيرات البيئة
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
ADMIN_ID = os.getenv("ADMIN_ID")

# روابط الصورة والملصق الترحيبي (يمكنك تعديل الروابط أو المعرفات حسب رغبتك)
WELCOME_IMAGE_URL = "https://ibb.co/hJ49q7y9"  # ضع رابط صورة الترحيب الخاصة بك
WELCOME_STICKER_ID = "AAMCAgADGQEAAS02smpyIKxv8rKEo21r6k8nMIpR59KKAAJUAANBtVYMarf4xwiNAfoBAAdtAAM9BA"          # ضع Sticker File ID الخاص بك هنا

# إعداد نموذج Gemini
genai.configure(api_key=GEMINI_API_KEY)
model = genai.GenerativeModel("gemini-3.6-flash")

GENERATION_CONFIG = {
    "max_output_tokens": 4096,
    "temperature": 0.9,
    "top_p": 0.95,
}

STANDARD_RATIOS = [
    ("1:1", "1:1  (مربع / Square)"),
    ("16:9", "16:9  (عريض / Widescreen)"),
    ("9:16", "9:16  (عمودي / Portrait)"),
    ("4:3", "4:3  (كلاسيكي / Classic)"),
    ("3:4", "3:4  (عمودي كلاسيكي)"),
    ("21:9", "21:9  (سينمائي / Cinematic)"),
]

def run_dummy_server():
    port = int(os.getenv("PORT", 10000))
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"Bot is running")
        def log_message(self, format, *args):
            pass
    server = HTTPServer(("0.0.0.0", port), Handler)
    server.serve_forever()

# دالة توليد نص الترحيب الشامل الذي يوضح عمل البوت ويضمن اسم المستخدم باللون الأزرق
def get_welcome_text(user, ui_lang="ar"):
    # رابط tg://user يجعل اسم المستخدم يتنسق باللون الأزرق التفاعلي
    user_mention = f"[{user.first_name}](tg://user?id={user.id})"
    
    if ui_lang == "ar":
        return (
            f"✨ أهلاً بك يا {user_mention} في **بوت استخراج البرومبت الاحترافي**! ✨\n"
            "━━━━━━━━━━━━━━━━━━━\n\n"
            "🛠️ **كيف يعمل هذا البوت؟**\n"
            "1️⃣ **أرسل أي صورة:** سيتعرف البوت عليها ويقوم بتحليلها باستخدام الذكاء الاصطناعي.\n"
            "2️⃣ **حدد الخيارات:** اختر لغة البرومبت (عربي/إنجليزي)، ومستوى التفصيل، ونسبة أبعاد الصورة.\n"
            "3️⃣ **انسخ البرومبت:** يُنشئ البوت وصفاً دقيقاً للغاية جاهزاً للنسخ بضغطة واحدة لاستخدامه في مولدات الصور (مثل Midjourney و Stable Diffusion).\n\n"
            "👇 **ابدأ الآن بإرسال صورتك!**"
        )
    else:
        return (
            f"✨ Welcome {user_mention} to the **Professional Prompt Extractor Bot**! ✨\n"
            "━━━━━━━━━━━━━━━━━━━\n\n"
            "🛠️ **How does this bot work?**\n"
            "1️⃣ **Send an Image:** The bot will analyze every detail using AI.\n"
            "2️⃣ **Select Options:** Choose prompt language, detail level, and aspect ratio.\n"
            "3️⃣ **Copy Prompt:** You get a hyper-precise prompt ready to copy in one tap for AI generators!\n\n"
            "👇 **Start now by sending your image!**"
        )

TEXTS = {
    "ar": {
        "choose_prompt_lang": "🌐 **الخطوة 1/3:** اختر لغة البرومبت المطلوب:",
        "choose_detail": "⚙️ **الخطوة 2/3:** اختر مستوى تفصيل البرومبت:",
        "choose_ratio": "📐 **الخطوة 3/3:** اختر مقاس/نسبة أبعاد الصورة:",
        "choose_standard_ratio": "📐 اختر النسبة القياسية المطلوبة:",
        "btn_short": "⚡ قصير وموجز",
        "btn_medium": "⚖️ متوسط",
        "btn_detailed": "🔍 تفصيلي وفائق الدقة (شامل جداً)",
        "btn_ratio_same": "🖼️ نفس مقاس الصورة المرسلة",
        "btn_ratio_standard": "📏 مقاس عام (اختيار نسبة قياسية)",
        "btn_back": "🔙 رجوع للغة",
        "btn_back_detail": "🔙 رجوع لمستوى التفصيل",
        "btn_cancel": "❌ إلغاء",
        "cancelled": "🚫 تم إلغاء العملية. يمكنك إرسال صورة جديدة في أي وقت.",
        "session_expired": "⚠️ انتهت الجلسة. يرجى إعادة إرسال الصورة من جديد.",
        "analyzing": "⏳ جاري تحليل عناصر الصورة واستخراج برومبت طويل ومدقق...",
        "success_title": "✅ **تم استخراج البرومبت بنجاح!**\n*(اضغط على النص أدناه لنسخه فوراً)*\n\n",
        "btn_retry": "🔄 استخراج بمستوى/لغة أخرى",
        "btn_new_photo": "📸 أرسل صورة جديدة",
        "error_generation": "❌ حدث خطأ أثناء تحليل الصورة: ",
    },
    "en": {
        "choose_prompt_lang": "🌐 **Step 1/3:** Choose the language of the prompt:",
        "choose_detail": "⚙️ **Step 2/3:** Choose the detail level of the prompt:",
        "choose_ratio": "📐 **Step 3/3:** Choose the image size / aspect ratio:",
        "choose_standard_ratio": "📐 Choose the standard aspect ratio:",
        "btn_short": "⚡ Short & Concise",
        "btn_medium": "⚖️ Medium",
        "btn_detailed": "🔍 Detailed & Ultra-Precise (Comprehensive)",
        "btn_ratio_same": "🖼️ Same as sent image",
        "btn_ratio_standard": "📏 Standard ratio (choose one)",
        "btn_back": "🔙 Back to language",
        "btn_back_detail": "🔙 Back to detail level",
        "btn_cancel": "❌ Cancel",
        "cancelled": "🚫 Operation cancelled. You can send a new image anytime.",
        "session_expired": "⚠️ Session expired. Please resend the image.",
        "analyzing": "⏳ Analyzing image elements and extracting a long, precise prompt...",
        "success_title": "✅ **Prompt extracted successfully!**\n*(Tap the text below to copy it instantly)*\n\n",
        "btn_retry": "🔄 Extract with another level/language",
        "btn_new_photo": "📸 Send a new image",
        "error_generation": "❌ An error occurred while analyzing the image: ",
    },
}

def t(context: ContextTypes.DEFAULT_TYPE, key: str) -> str:
    ui_lang = context.user_data.get("ui_lang", "ar")
    return TEXTS[ui_lang][key]

def compute_image_ratio(photo_bytes) -> str:
    try:
        image = Image.open(io.BytesIO(photo_bytes))
        width, height = image.size
        divisor = math.gcd(width, height) or 1
        ratio_w, ratio_h = width // divisor, height // divisor
        return f"{ratio_w}:{ratio_h} ({width}x{height}px)"
    except Exception as e:
        logging.warning(f"تعذر حساب نسبة أبعاد الصورة: {e}")
        return "غير محدد / Unspecified"

# ==========================================================
# الواجهات والرسائل
# ==========================================================
async def send_welcome_payload(chat_id, user, context):
    """إرسال صورة الترحيب + الرسالة + الملصق"""
    ui_lang = context.user_data.get("ui_lang", "ar")
    welcome_msg = get_welcome_text(user, ui_lang)
    
    # 1. إرسال صورة الترحيب مع نص الشرح والمنشن الأزرق
    try:
        await context.bot.send_photo(
            chat_id=chat_id,
            photo=WELCOME_IMAGE_URL,
            caption=welcome_msg,
            parse_mode="Markdown"
        )
    except Exception as e:
        # في حال عدم وجود صورة يرسل النص كرسالة عادية
        await context.bot.send_message(
            chat_id=chat_id,
            text=welcome_msg,
            parse_mode="Markdown"
        )
        logging.warning(f"تعذر إرسال صورة الترحيب: {e}")

    # 2. إرسال الملصق بعد الرسالة فوراً
    try:
        if WELCOME_STICKER_ID and not WELCOME_STICKER_ID.startswith("CAACAgIAAxkBAAE..."):
            await context.bot.send_sticker(chat_id=chat_id, sticker=WELCOME_STICKER_ID)
    except Exception as e:
        logging.warning(f"تعذر إرسال الملصق: {e}")

async def show_ui_language_menu(send_func):
    keyboard = [
        [
            InlineKeyboardButton("🇩🇿 العربية", callback_data="uilang_ar"),
            InlineKeyboardButton("🇬🇧 English", callback_data="uilang_en"),
        ],
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await send_func(
        "🌐 مرحباً! الرجاء اختيار لغة واجهة البوت:\n🌐 Welcome! Please choose the bot's interface language:",
        reply_markup=reply_markup,
    )

async def show_prompt_language_menu(context, send_func):
    keyboard = [
        [
            InlineKeyboardButton("🇩🇿 العربية", callback_data="lang_ar"),
            InlineKeyboardButton("🇬🇧 English", callback_data="lang_en"),
        ],
        [
            InlineKeyboardButton(t(context, "btn_cancel"), callback_data="cancel"),
        ],
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await send_func(t(context, "choose_prompt_lang"), reply_markup=reply_markup, parse_mode="Markdown")

async def show_detail_menu(context, query):
    keyboard = [
        [
            InlineKeyboardButton(t(context, "btn_short"), callback_data="detail_short"),
            InlineKeyboardButton(t(context, "btn_medium"), callback_data="detail_medium"),
        ],
        [
            InlineKeyboardButton(t(context, "btn_detailed"), callback_data="detail_detailed"),
        ],
        [
            InlineKeyboardButton(t(context, "btn_back"), callback_data="back_to_lang"),
            InlineKeyboardButton(t(context, "btn_cancel"), callback_data="cancel"),
        ],
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await query.edit_message_text(t(context, "choose_detail"), reply_markup=reply_markup, parse_mode="Markdown")

async def show_ratio_menu(context, query):
    keyboard = [
        [InlineKeyboardButton(t(context, "btn_ratio_same"), callback_data="ratio_same")],
        [InlineKeyboardButton(t(context, "btn_ratio_standard"), callback_data="ratio_menu")],
        [
            InlineKeyboardButton(t(context, "btn_back_detail"), callback_data="back_to_detail"),
            InlineKeyboardButton(t(context, "btn_cancel"), callback_data="cancel"),
        ],
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await query.edit_message_text(t(context, "choose_ratio"), reply_markup=reply_markup, parse_mode="Markdown")

async def show_standard_ratio_menu(context, query):
    rows = []
    row = []
    for value, label in STANDARD_RATIOS:
        row.append(InlineKeyboardButton(label, callback_data=f"ratio_std_{value}"))
        if len(row) == 2:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    rows.append(
        [
            InlineKeyboardButton(t(context, "btn_back"), callback_data="ratio_back"),
            InlineKeyboardButton(t(context, "btn_cancel"), callback_data="cancel"),
        ]
    )
    reply_markup = InlineKeyboardMarkup(rows)
    await query.edit_message_text(
        t(context, "choose_standard_ratio"), reply_markup=reply_markup, parse_mode="Markdown"
    )

# ==========================================================
# الأوامر والأحداث
# ==========================================================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if "ui_lang" not in context.user_data:
        await show_ui_language_menu(update.message.reply_text)
        return
    await send_welcome_payload(update.effective_chat.id, update.effective_user, context)

async def language_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await show_ui_language_menu(update.message.reply_text)

async def notify_admin_new_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not ADMIN_ID:
        return

    user = update.effective_user
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    full_name = user.full_name or "غير متوفر"
    username = f"@{user.username}" if user.username else "لا يوجد يوزر"
    user_id = user.id
    tg_lang = user.language_code or "غير معروف"

    admin_message = (
        "📩 **مستخدم أرسل صورة**\n"
        "━━━━━━━━━━━━━━\n"
        f"👤 الاسم: {full_name}\n"
        f"🔗 اليوزر: {username}\n"
        f"🆔 المعرف: `{user_id}`\n"
        f"🌐 لغة تيليجرام: {tg_lang}\n"
        f"🕒 الوقت: {now}"
    )

    try:
        await context.bot.send_message(
            chat_id=ADMIN_ID, text=admin_message, parse_mode="Markdown"
        )
    except Exception as e:
        logging.warning(f"تعذر إرسال إشعار للأدمن: {e}")

async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    photo_file = await update.message.photo[-1].get_file()
    photo_bytes = await photo_file.download_as_bytearray()
    context.user_data["photo_bytes"] = photo_bytes
    context.user_data["photo_message_id"] = update.message.message_id

    try:
        await context.bot.set_message_reaction(
            chat_id=update.effective_chat.id,
            message_id=update.message.message_id,
            reaction=[ReactionTypeEmoji(emoji="❤")],
        )
    except Exception as e:
        logging.warning(f"تعذر إضافة التفاعل على الرسالة: {e}")

    await notify_admin_new_photo(update, context)

    if "ui_lang" not in context.user_data:
        await show_ui_language_menu(update.message.reply_text)
        return

    await show_prompt_language_menu(context, update.message.reply_text)

async def generate_and_send_prompt(query, context: ContextTypes.DEFAULT_TYPE, chat_id):
    selected_lang = context.user_data.get("selected_lang", "en")
    selected_length = context.user_data.get("selected_length", "medium")
    selected_ratio = context.user_data.get("selected_ratio")
    photo_bytes = context.user_data.get("photo_bytes")
    photo_message_id = context.user_data.get("photo_message_id")

    if not photo_bytes:
        await query.edit_message_text(t(context, "session_expired"))
        return

    await query.edit_message_text(t(context, "analyzing"))

    system_instructions = {
        ("ar", "short"): "اكتب برومبت قصير وموجز باللغة العربية...",
        ("ar", "medium"): "اكتب برومبت متوسط الطول باللغة العربية...",
        ("ar", "detailed"): "قم بتحليل هذه الصورة بأقصى درجة ممكنة من الدقة والعمق...",
        ("en", "short"): "Write a concise image generation prompt in English...",
        ("en", "medium"): "Write a medium-length image generation prompt in English...",
        ("en", "detailed"): "Analyze this image with maximum possible depth and precision...",
    }

    instruction = system_instructions.get((selected_lang, selected_length), "")

    if selected_ratio:
        if selected_lang == "ar":
            instruction += f"\n\nمهم جداً: يجب أن يذكر البرومبت نسبة الأبعاد: \"{selected_ratio}\"."
        else:
            instruction += f"\n\nVery important: prompt MUST state aspect ratio: \"{selected_ratio}\"."

    try:
        image = Image.open(io.BytesIO(photo_bytes))
        response = model.generate_content(
            [instruction, image], generation_config=GENERATION_CONFIG
        )
        generated_prompt = response.text.strip()

        post_action_keyboard = [
            [InlineKeyboardButton(t(context, "btn_retry"), callback_data="back_to_lang")],
            [InlineKeyboardButton(t(context, "btn_new_photo"), callback_data="cancel")],
        ]
        reply_markup = InlineKeyboardMarkup(post_action_keyboard)
        result_message = t(context, "success_title") + f"```\n{generated_prompt}\n```"

        await context.bot.send_message(
            chat_id=chat_id,
            text=result_message,
            parse_mode="Markdown",
            reply_markup=reply_markup,
            reply_to_message_id=photo_message_id,
        )
        await query.delete_message()

    except Exception as e:
        logging.error(f"خطأ أثناء التوليد: {e}")
        await query.message.reply_text(t(context, "error_generation") + str(e))

async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data

    if data.startswith("uilang_"):
        ui_lang = data.split("_")[1]
        context.user_data["ui_lang"] = ui_lang

        if context.user_data.get("photo_bytes"):
            await show_prompt_language_menu(context, query.edit_message_text)
        else:
            await query.delete_message()
            await send_welcome_payload(update.effective_chat.id, update.effective_user, context)
        return

    if data == "cancel":
        context.user_data.pop("photo_bytes", None)
        context.user_data.pop("selected_lang", None)
        context.user_data.pop("selected_length", None)
        context.user_data.pop("selected_ratio", None)
        context.user_data.pop("photo_message_id", None)
        await query.edit_message_text(t(context, "cancelled"))
        return

    if data == "back_to_lang":
        await show_prompt_language_menu(context, query.edit_message_text)
        return

    if data == "back_to_detail":
        await show_detail_menu(context, query)
        return

    if data == "ratio_back":
        await show_ratio_menu(context, query)
        return

    if data.startswith("lang_"):
        context.user_data["selected_lang"] = data.split("_")[1]
        await show_detail_menu(context, query)
        return

    if data.startswith("detail_"):
        context.user_data["selected_length"] = data.split("_")[1]
        if not context.user_data.get("photo_bytes"):
            await query.edit_message_text(t(context, "session_expired"))
            return
        await show_ratio_menu(context, query)
        return

    if data == "ratio_menu":
        await show_standard_ratio_menu(context, query)
        return

    if data == "ratio_same":
        photo_bytes = context.user_data.get("photo_bytes")
        if not photo_bytes:
            await query.edit_message_text(t(context, "session_expired"))
            return
        context.user_data["selected_ratio"] = compute_image_ratio(photo_bytes)
        await generate_and_send_prompt(query, context, update.effective_chat.id)
        return

    if data.startswith("ratio_std_"):
        context.user_data["selected_ratio"] = data.replace("ratio_std_", "", 1)
        await generate_and_send_prompt(query, context, update.effective_chat.id)
        return

def main():
    threading.Thread(target=run_dummy_server, daemon=True).start()

    request = HTTPXRequest(
        connect_timeout=30.0,
        read_timeout=30.0,
        write_timeout=30.0,
        pool_timeout=30.0,
    )

    app = Application.builder().token(TELEGRAM_TOKEN).request(request).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("language", language_command))
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    app.add_handler(CallbackQueryHandler(button_callback))

    print("🤖 البوت يعمل الآن بنجاح...")
    app.run_polling()

if __name__ == "__main__":
    main()
