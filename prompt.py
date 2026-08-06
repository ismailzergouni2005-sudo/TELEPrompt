import os
import io
import math
import logging
import threading
import asyncio
import numpy as np
from http.server import HTTPServer, BaseHTTPRequestHandler
from google import generativeai as genai
from PIL import Image, ImageEnhance
import cv2
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

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
ADMIN_ID = os.getenv("ADMIN_ID")
CHANNEL_ID = os.getenv("CHANNEL_ID")  # معرف القناة لإرسال الإشعارات

WELCOME_IMAGE_URL = "https://ibb.co/hJ49q7y9" 
WELCOME_STICKER_ID = "CAACAgIAAxkBAAEtNrJqciCsb_KyhKNta-pPJzCKUefSigACVAADQbVWDGq3-McIjQH6PQQ"

genai.configure(api_key=GEMINI_API_KEY)
model = genai.GenerativeModel("gemini-3.6-flash")

GENERATION_CONFIG = {
    "max_output_tokens": 4096,
    "temperature": 0.7,
    "top_p": 0.9,
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

async def notify_channel(user, action: str, context: ContextTypes.DEFAULT_TYPE):
    """دالة إرسال الإشعارات لقناة التليجرام بشكل آمن لمنع أخطاء Markdown"""
    target_id = CHANNEL_ID or ADMIN_ID
    if not target_id:
        return

    def clean_md(text):
        if not text:
            return ""
        for char in ['_', '*', '`', '[']:
            text = str(text).replace(char, f"\\{char}")
        return text

    username = f"@{clean_md(user.username)}" if user.username else "لا يوجد"
    first_name = clean_md(user.first_name) or "غير معروف"
    last_name = clean_md(user.last_name) or ""
    full_name = f"{first_name} {last_name}".strip()
    
    channel_message = (
        "🔔 **إشعار جديد في البوت:**\n"
        "━━━━━━━━━━━━━━━━━━━\n"
        f"📌 **الحدث:** {action}\n"
        f"👤 **الاسم:** {full_name}\n"
        f"🏷️ **اليوزر:** {username}\n"
        f"🆔 **المعرف:** `{user.id}`\n"
        f"🔗 **الرابط:** [{first_name}](tg://user?id={user.id})\n"
    )

    try:
        chat_id_val = int(target_id) if target_id.startswith("-") or target_id.isdigit() else target_id
        await context.bot.send_message(
            chat_id=chat_id_val,
            text=channel_message,
            parse_mode="Markdown"
        )
    except Exception as e:
        logging.warning(f"تعذر إرسال الإشعار للقناة: {e}")

def local_upscale_image(photo_bytes: bytearray) -> io.BytesIO:
    """تحسين الجودة والحدة ومضاعفة الأبعاد محلياً"""
    image_np = np.frombuffer(photo_bytes, np.uint8)
    img = cv2.imdecode(image_np, cv2.IMREAD_COLOR)

    height, width = img.shape[:2]
    scaled_img = cv2.resize(img, (width * 2, height * 2), interpolation=cv2.INTER_LANCZOS4)

    denoised = cv2.fastNlMeansDenoisingColored(scaled_img, None, 3, 3, 7, 21)

    pil_img = Image.fromarray(cv2.cvtColor(denoised, cv2.COLOR_BGR2RGB))
    pil_img = ImageEnhance.Sharpness(pil_img).enhance(1.8)
    pil_img = ImageEnhance.Contrast(pil_img).enhance(1.1)

    output_stream = io.BytesIO()
    pil_img.save(output_stream, format="JPEG", quality=98)
    output_stream.seek(0)
    return output_stream

def get_welcome_text(user, ui_lang="ar"):
    user_mention = f"[{user.first_name}](tg://user?id={user.id})"
    
    if ui_lang == "ar":
        return (
            f"✨ أهلاً بك يا {user_mention} في **بوت استخراج البرومبت وتحسين الصور**! ✨\n"
            "━━━━━━━━━━━━━━━━━━━\n\n"
            "🛠️ **كيف يعمل هذا البوت؟**\n"
            "1️⃣ **أرسل أي صورة:** سيتعرف البوت عليها تلقائياً.\n"
            "2️⃣ **اختر الخدمة:** استخراج البرومبت بالذكاء الاصطناعي أو تحسين الجودة والحدة مجاناً.\n"
            "3️⃣ **استلم النتيجة:** احصل على البرومبت أو الصورة المحسنة فوراً!\n\n"
            "👇 **ابدأ الآن بإرسال صورتك!**"
        )
    else:
        return (
            f"✨ Welcome {user_mention} to **AI Prompt Extractor & HD Upscaler Bot**! ✨\n"
            "━━━━━━━━━━━━━━━━━━━\n\n"
            "🛠️ **How does this bot work?**\n"
            "1️⃣ **Send an Image:** The bot will analyze it automatically.\n"
            "2️⃣ **Choose Action:** Extract AI prompt or Upscale image quality to HD.\n"
            "3️⃣ **Get Results:** Copy your prompt or download your high-res image!\n\n"
            "👇 **Start now by sending your image!**"
        )

TEXTS = {
    "ar": {
        "choose_main_mode": "🎯 **اختر الخدمة المطلوبة للصورة:**",
        "btn_extract_prompt": "📝 استخراج البرومبت (Prompt)",
        "btn_upscale_image": "🚀 تحسين الجودة والحدة (Free HD Upscale)",
        "choose_prompt_lang": "🌐 **الخطوة 1/3:** اختر لغة البرومبت المطلوب:",
        "choose_detail": "⚙️ **الخطوة 2/3:** اختر مستوى تفصيل البرومبت:",
        "choose_ratio": "📐 **الخطوة 3/3:** اختر مقاس/نسبة أبعاد الصورة:",
        "choose_standard_ratio": "📐 اختر النسبة القياسية المطلوبة:",
        "btn_short": "⚡ قصير وموجز",
        "btn_medium": "⚖️ متوسط",
        "btn_detailed": "🔍 تفصيلي وفائق الدقة (شامل جداً)",
        "btn_ratio_same": "🖼️ نفس مقاس الصورة المرسلة",
        "btn_ratio_standard": "📏 مقاس عام (اختيار نسبة قياسية)",
        "btn_back": "🔙 رجوع",
        "btn_back_detail": "🔙 رجوع لمستوى التفصيل",
        "btn_cancel": "❌ إلغاء",
        "cancelled": "🚫 تم إلغاء العملية الحالية. أرسل صورة جديدة في أي وقت.",
        "session_expired": "⚠️ انتهت الجلسة. يرجى إعادة إرسال الصورة من جديد.",
        "analyzing": "⏳ جاري تحليل عناصر الصورة واستخراج البرومبت...",
        "enhancing": "⚡ جاري معالجة وتكبير أبعاد الصورة وإبراز حدتها محلياً...",
        "success_title": "✅ **تم استخراج البرومبت بنجاح!**\n*(اضغط على النص أدناه لنسخه فوراً)*\n\n",
        "success_enhance": "✨ **تم رفع دقة الصورة وتحسين وضوح التفاصيل بنجاح!**",
        "btn_retry": "🔄 استخراج بمستوى/لغة أخرى",
        "btn_new_photo": "📸 أرسل صورة جديدة",
        "error_generation": "❌ حدث خطأ أثناء المعالجة: ",
        "ready_for_new": "📸 مرحباً بك مجدداً! يمكنك إرسال صورة جديدة الآن.",
    },
    "en": {
        "choose_main_mode": "🎯 **Choose the service for your image:**",
        "btn_extract_prompt": "📝 Extract Prompt",
        "btn_upscale_image": "🚀 Ultra HD Upscale (Free)",
        "choose_prompt_lang": "🌐 **Step 1/3:** Choose prompt language:",
        "choose_detail": "⚙️ **Step 2/3:** Choose detail level:",
        "choose_ratio": "📐 **Step 3/3:** Choose aspect ratio:",
        "choose_standard_ratio": "📐 Choose standard aspect ratio:",
        "btn_short": "⚡ Short & Concise",
        "btn_medium": "⚖️ Medium",
        "btn_detailed": "🔍 Detailed & Ultra-Precise",
        "btn_ratio_same": "🖼️ Same as sent image",
        "btn_ratio_standard": "📏 Standard ratio",
        "btn_back": "🔙 Back",
        "btn_back_detail": "🔙 Back to detail level",
        "btn_cancel": "❌ Cancel",
        "cancelled": "🚫 Operation cancelled. Send a new image anytime.",
        "session_expired": "⚠️ Session expired. Please resend the image.",
        "analyzing": "⏳ Analyzing image and extracting prompt...",
        "enhancing": "⚡ Processing image sharpness and resolution...",
        "success_title": "✅ **Prompt extracted successfully!**\n*(Tap below to copy)*\n\n",
        "success_enhance": "✨ **Image scaled up & details enhanced successfully!**",
        "btn_retry": "🔄 Extract with other options",
        "btn_new_photo": "📸 Send a new image",
        "error_generation": "❌ An error occurred: ",
        "ready_for_new": "📸 Ready for a new photo! Send your image.",
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
        return f"{width // divisor}:{height // divisor}"
    except Exception as e:
        logging.warning(f"تعذر حساب نسبة أبعاد الصورة: {e}")
        return "1:1"

async def send_welcome_payload(chat_id, user, context):
    ui_lang = context.user_data.get("ui_lang", "ar")
    welcome_msg = get_welcome_text(user, ui_lang)
    
    try:
        await context.bot.send_photo(
            chat_id=chat_id,
            photo=WELCOME_IMAGE_URL,
            caption=welcome_msg,
            parse_mode="Markdown"
        )
    except Exception as e:
        await context.bot.send_message(
            chat_id=chat_id, text=welcome_msg, parse_mode="Markdown"
        )
        logging.warning(f"تعذر إرسال صورة الترحيب: {e}")

    try:
        if WELCOME_STICKER_ID:
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

async def show_main_mode_menu(context, send_func):
    keyboard = [
        [InlineKeyboardButton(t(context, "btn_extract_prompt"), callback_data="mode_prompt")],
        [InlineKeyboardButton(t(context, "btn_upscale_image"), callback_data="mode_upscale")],
        [InlineKeyboardButton(t(context, "btn_cancel"), callback_data="cancel")],
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await send_func(t(context, "choose_main_mode"), reply_markup=reply_markup, parse_mode="Markdown")

async def show_prompt_language_menu(context, query):
    keyboard = [
        [
            InlineKeyboardButton("🇩🇿 العربية", callback_data="lang_ar"),
            InlineKeyboardButton("🇬🇧 English", callback_data="lang_en"),
        ],
        [
            InlineKeyboardButton(t(context, "btn_back"), callback_data="back_to_main_mode"),
            InlineKeyboardButton(t(context, "btn_cancel"), callback_data="cancel")
        ],
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await query.edit_message_text(t(context, "choose_prompt_lang"), reply_markup=reply_markup, parse_mode="Markdown")

async def show_detail_menu(context, query):
    keyboard = [
        [
            InlineKeyboardButton(t(context, "btn_short"), callback_data="detail_short"),
            InlineKeyboardButton(t(context, "btn_medium"), callback_data="detail_medium"),
        ],
        [InlineKeyboardButton(t(context, "btn_detailed"), callback_data="detail_detailed")],
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
    rows.append([
        InlineKeyboardButton(t(context, "btn_back"), callback_data="ratio_back"),
        InlineKeyboardButton(t(context, "btn_cancel"), callback_data="cancel"),
    ])
    reply_markup = InlineKeyboardMarkup(rows)
    await query.edit_message_text(t(context, "choose_standard_ratio"), reply_markup=reply_markup, parse_mode="Markdown")

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await notify_channel(update.effective_user, "قام بتشغيل البوت (/start)", context)

    if "ui_lang" not in context.user_data:
        await show_ui_language_menu(update.message.reply_text)
        return
    await send_welcome_payload(update.effective_chat.id, update.effective_user, context)

async def language_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await show_ui_language_menu(update.message.reply_text)

async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    photo_file = await update.message.photo[-1].get_file()
    photo_bytes = await photo_file.download_as_bytearray()
    context.user_data["photo_bytes"] = photo_bytes
    context.user_data["photo_message_id"] = update.message.message_id

    await notify_channel(update.effective_user, "قام بإرسال صورة جديدة 📸", context)

    try:
        await context.bot.set_message_reaction(
            chat_id=update.effective_chat.id,
            message_id=update.message.message_id,
            reaction=[ReactionTypeEmoji(emoji="❤")],
        )
    except Exception:
        pass

    if "ui_lang" not in context.user_data:
        await show_ui_language_menu(update.message.reply_text)
        return

    await show_main_mode_menu(context, update.message.reply_text)

async def process_upscale(query, context: ContextTypes.DEFAULT_TYPE, chat_id, user):
    photo_bytes = context.user_data.get("photo_bytes")
    photo_message_id = context.user_data.get("photo_message_id")

    if not photo_bytes:
        await query.edit_message_text(t(context, "session_expired"))
        return

    await query.edit_message_text(t(context, "enhancing"))

    try:
        loop = asyncio.get_running_loop()
        enhanced_stream = await loop.run_in_executor(None, local_upscale_image, photo_bytes)
        
        post_action_keyboard = [
            [InlineKeyboardButton(t(context, "btn_new_photo"), callback_data="new_photo_request")],
        ]
        reply_markup = InlineKeyboardMarkup(post_action_keyboard)

        await context.bot.send_document(
            chat_id=chat_id,
            document=enhanced_stream,
            filename="enhanced_hd_image.jpg",
            caption=t(context, "success_enhance"),
            parse_mode="Markdown",
            reply_markup=reply_markup,
            reply_to_message_id=photo_message_id,
        )
        await query.delete_message()
        
        await notify_channel(user, "قام بزيادة دقة صورة (HD Upscale) 🚀", context)

    except Exception as e:
        logging.error(f"خطأ أثناء تحسين الصورة: {e}")
        await query.message.reply_text(t(context, "error_generation") + str(e))

async def generate_and_send_prompt(query, context: ContextTypes.DEFAULT_TYPE, chat_id, user):
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
        ("ar", "short"): "اكتب برومبت قصير وموجز لوصف هذه الصورة لاستخدامه في الذكاء الاصطناعي.",
        ("ar", "medium"): "اكتب برومبت متوسط الطول وشامل لوصف هذه الصورة لاستخدامه في توليد الصور.",
        ("ar", "detailed"): "قم بتحليل هذه الصورة بأقصى درجة ممكنة من الدقة والعمق واكتب برومبت تفصيلي جداً شامل الإضاءة والزوايا والتأثيرات.",
        ("en", "short"): "Write a short and concise image generation prompt describing this image.",
        ("en", "medium"): "Write a detailed image generation prompt describing this image.",
        ("en", "detailed"): "Write an ultra-detailed, highly comprehensive image generation prompt covering subject, lighting, style, background, and camera angle.",
    }

    instruction = system_instructions.get((selected_lang, selected_length), "")

    if selected_ratio:
        instruction += f"\nInclude aspect ratio: --ar {selected_ratio}"

    image = Image.open(io.BytesIO(photo_bytes))
    
    max_retries = 3
    response = None
    last_error = None
    
    for attempt in range(max_retries):
        try:
            response = model.generate_content([instruction, image], generation_config=GENERATION_CONFIG)
            if response and response.text:
                break
        except Exception as e:
            last_error = e
            logging.warning(f"محاولة فاشلة ({attempt + 1}/{max_retries}): {e}")
            await asyncio.sleep(1)

    if not response or not response.text:
        logging.error(f"فشل التوليد نهائياً: {last_error}")
        await query.message.reply_text(t(context, "error_generation") + str(last_error))
        return

    try:
        generated_prompt = response.text.strip()

        post_action_keyboard = [
            [InlineKeyboardButton(t(context, "btn_retry"), callback_data="back_to_lang")],
            [InlineKeyboardButton(t(context, "btn_new_photo"), callback_data="new_photo_request")],
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

        await notify_channel(user, "قام باستخراج برومبت من صورة 📝", context)

    except Exception as e:
        logging.error(f"خطأ إرسال الرسالة: {e}")
        await query.message.reply_text(t(context, "error_generation") + str(e))

async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data

    if data.startswith("uilang_"):
        ui_lang = data.split("_")[1]
        context.user_data["ui_lang"] = ui_lang

        if context.user_data.get("photo_bytes"):
            await show_main_mode_menu(context, query.edit_message_text)
        else:
            await query.delete_message()
            await send_welcome_payload(update.effective_chat.id, update.effective_user, context)
        return

    if data == "mode_prompt":
        await show_prompt_language_menu(context, query)
        return

    if data == "mode_upscale":
        await process_upscale(query, context, update.effective_chat.id, update.effective_user)
        return

    if data == "back_to_main_mode":
        await show_main_mode_menu(context, query.edit_message_text)
        return

    if data == "new_photo_request":
        context.user_data.pop("photo_bytes", None)
        context.user_data.pop("selected_lang", None)
        context.user_data.pop("selected_length", None)
        context.user_data.pop("selected_ratio", None)
        context.user_data.pop("photo_message_id", None)
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text=t(context, "ready_for_new")
        )
        return

    if data == "cancel":
        context.user_data.pop("photo_bytes", None)
        await query.edit_message_text(t(context, "cancelled"))
        return

    if data == "back_to_lang":
        await show_prompt_language_menu(context, query)
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
        await generate_and_send_prompt(query, context, update.effective_chat.id, update.effective_user)
        return

    if data.startswith("ratio_std_"):
        context.user_data["selected_ratio"] = data.replace("ratio_std_", "", 1)
        await generate_and_send_prompt(query, context, update.effective_chat.id, update.effective_user)
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

    print("🤖 البوت يعمل بنجاح...")
    app.run_polling()

if __name__ == "__main__":
    main()
