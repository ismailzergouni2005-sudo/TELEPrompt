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

# قراءة المفاتيح آمنة عبر متغيرات البيئة فقط
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
ADMIN_ID = os.getenv("ADMIN_ID")  # معرف تيليجرام الخاص بالأدمن لاستقبال إشعارات المستخدمين

# إعداد نموذج Gemini
genai.configure(api_key=GEMINI_API_KEY)
model = genai.GenerativeModel("gemini-3.6-flash")

# إعداد توليد أطول وأكثر تفصيلاً (رفع الحد الأقصى للمخرجات حتى لا يُقتصّ البرومبت التفصيلي)
GENERATION_CONFIG = {
    "max_output_tokens": 4096,
    "temperature": 0.9,
    "top_p": 0.95,
}

# نسب الأبعاد القياسية المتاحة في قائمة "المقاس العام"
STANDARD_RATIOS = [
    ("1:1", "1:1  (مربع / Square)"),
    ("16:9", "16:9  (عريض / Widescreen)"),
    ("9:16", "9:16  (عمودي / Portrait)"),
    ("4:3", "4:3  (كلاسيكي / Classic)"),
    ("3:4", "3:4  (عمودي كلاسيكي)"),
    ("21:9", "21:9  (سينمائي / Cinematic)"),
]


# ==========================================================
# خادم HTTP وهمي لإرضاء فحص المنفذ في Render (Web Service)
# ==========================================================
def run_dummy_server():
    port = int(os.getenv("PORT", 10000))

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"Bot is running")

        def log_message(self, format, *args):
            pass  # لإسكات سجلات الخادم الوهمي

    server = HTTPServer(("0.0.0.0", port), Handler)
    server.serve_forever()


# ==========================================================
# نصوص واجهة البوت بلغتين (عربي / إنجليزي)
# ==========================================================
TEXTS = {
    "ar": {
        "welcome": (
            "✨ **بوت استخراج البرومبت الاحترافي** ✨\n"
            "━━━━━━━━━━━━━━━━━━━\n\n"
            "🖼️ أرسل أي صورة تريدها، وسأقوم بتحليل كل تفاصيلها بدقة عالية.\n\n"
            "🎯 اختر لغة البرومبت ومستوى التفصيل ونسبة الأبعاد التي تناسبك.\n"
            "📋 وستحصل على برومبت احترافي جاهز للنسخ بضغطة واحدة!\n\n"
            "👇 ابدأ الآن بإرسال صورتك"
        ),
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
        "welcome": (
            "✨ **Professional Prompt Extractor Bot** ✨\n"
            "━━━━━━━━━━━━━━━━━━━\n\n"
            "🖼️ Send me any image, and I'll analyze every detail with high precision.\n\n"
            "🎯 Choose your preferred prompt language, detail level, and aspect ratio.\n"
            "📋 You'll get a professional, ready-to-copy prompt in one tap!\n\n"
            "👇 Start now by sending your image"
        ),
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
    """إرجاع النص المناسب حسب لغة واجهة المستخدم المخزنة."""
    ui_lang = context.user_data.get("ui_lang", "ar")
    return TEXTS[ui_lang][key]


# ==========================================================
# أدوات مساعدة لنسبة الأبعاد
# ==========================================================
def compute_image_ratio(photo_bytes) -> str:
    """حساب نسبة أبعاد الصورة الأصلية المرسلة (مثال: 4:3)."""
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
# قوائم لوحات المفاتيح (Keyboards)
# ==========================================================
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
# أوامر البوت
# ==========================================================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if "ui_lang" not in context.user_data:
        await show_ui_language_menu(update.message.reply_text)
        return
    await update.message.reply_text(t(context, "welcome"), parse_mode="Markdown")


async def language_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """السماح للمستخدم بتغيير لغة الواجهة في أي وقت عبر /language"""
    await show_ui_language_menu(update.message.reply_text)


async def notify_admin_new_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """إرسال معلومات المستخدم للأدمن عند كل صورة يرسلها."""
    if not ADMIN_ID:
        return  # لم يتم تحديد ADMIN_ID في متغيرات البيئة

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


# استقبال الصورة
async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    photo_file = await update.message.photo[-1].get_file()
    photo_bytes = await photo_file.download_as_bytearray()
    context.user_data["photo_bytes"] = photo_bytes
    # نخزن رقم رسالة الصورة حتى نستطيع الرد عليها لاحقاً بالبرومبت الناتج
    context.user_data["photo_message_id"] = update.message.message_id

    # تفاعل بقلب على رسالة الصورة مباشرة عند استلامها
    try:
        await context.bot.set_message_reaction(
            chat_id=update.effective_chat.id,
            message_id=update.message.message_id,
            reaction=[ReactionTypeEmoji(emoji="❤")],
        )
    except Exception as e:
        logging.warning(f"تعذر إضافة التفاعل على الرسالة: {e}")

    # إشعار الأدمن بمعلومات المستخدم عند كل صورة يرسلها
    await notify_admin_new_photo(update, context)

    if "ui_lang" not in context.user_data:
        # لم يتم اختيار لغة الواجهة بعد، نطلبها أولاً ثم نكمل تلقائياً
        await show_ui_language_menu(update.message.reply_text)
        return

    await show_prompt_language_menu(context, update.message.reply_text)


# ==========================================================
# توليد البرومبت وإرساله
# ==========================================================
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

    # توجيهات صارمة ومفصلة لضمان أقصى درجة من الدقة والطول دون مقدمات
    system_instructions = {
        ("ar", "short"): (
            "اكتب برومبت قصير وموجز باللغة العربية (3-4 جمل غنية بالتفاصيل) يصف الفكرة الرئيسية "
            "والموضوع الأساسي والأسلوب العام لهذه الصورة بدقة. "
            "أرجع نص البرومبت فقط بدون أي مقدمات."
        ),
        ("ar", "medium"): (
            "اكتب برومبت متوسط الطول باللغة العربية (فقرة واحدة مركّزة من 6 إلى 10 جمل) يصف بدقة "
            "موضوع الصورة، ملامح الشخصيات/الكائنات، الأسلوب الفني، الإضاءة، الألوان، والتكوين البصري. "
            "أرجع نص البرومبت فقط بدون أي مقدمات."
        ),
        ("ar", "detailed"): (
            "قم بتحليل هذه الصورة بأقصى درجة ممكنة من الدقة والعمق، واكتب برومبت شديد الطول والتفصيل والشمول "
            "باللغة العربية (لا يقل عن 250-400 كلمة) لإعادة إنتاجها بدقة متناهية عبر أدوات الذكاء الاصطناعي "
            "التوليدية. يجب أن يغطي البرومبت جميع النقاط التالية بعمق وإسهاب حقيقي وليس بإيجاز:\n"
            "1. Subject Details: وصف دقيق جداً للشخصيات/الكائنات الرئيسية (الملامح، تعابير الوجه، وضعية الجسد، الحركة، اتجاه النظر، العمر التقريبي، البشرة، الشعر).\n"
            "2. Wardrobe & Textures: وصف الملابس والإكسسوارات وخاماتها ودرجة تفاصيلها (تجاعيد، بلل، غبار، ثلج، خدوش، لمعان القماش).\n"
            "3. Art Style/Rendering Engine: النمط الفني أو محرك العرض (مثل Unreal Engine 5, Octane Render, تصوير فوتوغرافي واقعي 8K، رسم رقمي، أنمي).\n"
            "4. Lighting & Atmosphere: نوع الإضاءة ومصدرها، اتجاهها، حدتها، الظلال، التباين، الجو العام والمزاج البصري.\n"
            "5. Camera & Composition: زاوية الكاميرا، نوع اللقطة، العدسة المستخدمة، عمق المجال، تكوين الكادر وقواعد التأطير.\n"
            "6. Colors & Palette: لوحة الألوان السائدة، التباين اللوني، درجات الحرارة اللونية (دافئة/باردة).\n"
            "7. Environment & Weather: تفاصيل الخلفية والبيئة المحيطة، حالة الطقس، الجسيمات العالقة في الهواء (ثلج، ضباب، غبار).\n"
            "8. Mood & Atmosphere: الشعور العام والانطباع الفني الذي تنقله الصورة.\n"
            "9. Technical Tags: كلمات مفتاحية تقنية ختامية مثل الدقة (8K)، مستوى الواقعية، ونسبة الأبعاد المطلوبة.\n"
            "لا تختصر أو تلخص أي نقطة، بل فصّل كل عنصر بجملة أو جملتين كاملتين على الأقل. "
            "أرجع نص البرومبت فقط الصافي المخصص للنسخ المباشر، متصلاً وسلساً كفقرة أو فقرتين طويلتين، "
            "بدون أرقام أو عناوين فرعية أو شروحات جانبية."
        ),
        ("en", "short"): (
            "Write a concise image generation prompt in English (3-4 detail-rich sentences) covering the core subject, "
            "idea, and overall style of this photo precisely. "
            "Output ONLY the raw prompt text."
        ),
        ("en", "medium"): (
            "Write a medium-length image generation prompt in English (a single focused paragraph of 6-10 sentences) "
            "precisely describing the subject, features, style, lighting, colors, and composition. "
            "Output ONLY the raw prompt text."
        ),
        ("en", "detailed"): (
            "Analyze this image with maximum possible depth and precision, and write an extremely long, hyper-detailed, "
            "comprehensive image generation prompt in English (at least 250-400 words) suitable for exact recreation via "
            "generative AI tools. The prompt must densely and thoroughly cover ALL of the following, with real depth rather than brevity:\n"
            "1. Subject Details: precise facial features/expressions, body posture, gesture, gaze direction, approximate age, "
            "skin, hair, and exact pose of every main subject.\n"
            "2. Wardrobe & Textures: clothing, accessories, and material textures in detail (wrinkles, moisture, dust, snow, wear, fabric sheen).\n"
            "3. Art Style/Rendering Engine: the exact artistic style or render engine (e.g. Unreal Engine 5, Octane Render, photorealistic 8K photography, digital painting, anime).\n"
            "4. Lighting & Atmosphere: light source type, direction, intensity, shadow behavior, contrast, cinematic mood.\n"
            "5. Camera & Composition: camera angle, shot type, lens/focal length, depth of field, framing and compositional rules.\n"
            "6. Colors & Palette: dominant color palette, color contrast, warm/cool color temperature balance.\n"
            "7. Environment & Weather: background and environment details, weather conditions, airborne particles (snow, fog, dust).\n"
            "8. Mood & Atmosphere: the overall emotional tone and artistic impression conveyed.\n"
            "9. Technical Tags: closing technical keywords such as resolution (8K), realism level, and the requested aspect ratio.\n"
            "Do not summarize or shorten any point — elaborate each element in at least one or two full sentences. "
            "Output ONLY the pure raw prompt text suitable for direct copy-pasting, flowing naturally as one or two long paragraphs. "
            "Do NOT include numbering, markdown headings, or explanations."
        ),
    }

    instruction = system_instructions.get((selected_lang, selected_length))

    # إضافة توجيه نسبة الأبعاد المختارة إلى تعليمات النموذج
    if selected_ratio:
        if selected_lang == "ar":
            instruction += (
                f"\n\nمهم جداً: يجب أن يذكر البرومبت بوضوح ضمن الوسوم التقنية الختامية نسبة الأبعاد التالية: "
                f"\"{selected_ratio}\" (aspect ratio)."
            )
        else:
            instruction += (
                f"\n\nVery important: the closing technical tags of the prompt MUST explicitly state the following "
                f"aspect ratio: \"{selected_ratio}\"."
            )

    try:
        image = Image.open(io.BytesIO(photo_bytes))
        response = model.generate_content(
            [instruction, image], generation_config=GENERATION_CONFIG
        )
        generated_prompt = response.text.strip()

        post_action_keyboard = [
            [
                InlineKeyboardButton(t(context, "btn_retry"), callback_data="back_to_lang"),
            ],
            [
                InlineKeyboardButton(t(context, "btn_new_photo"), callback_data="cancel"),
            ],
        ]
        reply_markup = InlineKeyboardMarkup(post_action_keyboard)

        # نستخدم ``` لجعل النص داخل صندوق كود قابل للنسخ بضغطة واحدة في تيليجرام
        result_message = t(context, "success_title") + f"```\n{generated_prompt}\n```"

        # إرسال البرومبت كرد مباشر على رسالة الصورة الأصلية
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


# ==========================================================
# معالجة تفاعلات الأزرار
# ==========================================================
async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data

    # اختيار لغة واجهة البوت
    if data.startswith("uilang_"):
        ui_lang = data.split("_")[1]
        context.user_data["ui_lang"] = ui_lang

        # إن كانت هناك صورة بانتظار المعالجة، انتقل مباشرة لقائمة لغة البرومبت
        if context.user_data.get("photo_bytes"):
            await show_prompt_language_menu(context, query.edit_message_text)
        else:
            await query.edit_message_text(t(context, "welcome"), parse_mode="Markdown")
        return

    # زر الإلغاء
    if data == "cancel":
        context.user_data.pop("photo_bytes", None)
        context.user_data.pop("selected_lang", None)
        context.user_data.pop("selected_length", None)
        context.user_data.pop("selected_ratio", None)
        context.user_data.pop("photo_message_id", None)
        await query.edit_message_text(t(context, "cancelled"))
        return

    # زر الرجوع لاختيار لغة البرومبت
    if data == "back_to_lang":
        await show_prompt_language_menu(context, query.edit_message_text)
        return

    # زر الرجوع لاختيار مستوى التفصيل
    if data == "back_to_detail":
        await show_detail_menu(context, query)
        return

    # زر الرجوع من قائمة النسب القياسية إلى قائمة اختيار المقاس
    if data == "ratio_back":
        await show_ratio_menu(context, query)
        return

    # اختيار لغة البرومبت
    if data.startswith("lang_"):
        selected_lang = data.split("_")[1]
        context.user_data["selected_lang"] = selected_lang
        await show_detail_menu(context, query)
        return

    # اختيار مستوى التفصيل -> ننتقل لاختيار مقاس/نسبة الصورة
    if data.startswith("detail_"):
        selected_length = data.split("_")[1]
        context.user_data["selected_length"] = selected_length

        if not context.user_data.get("photo_bytes"):
            await query.edit_message_text(t(context, "session_expired"))
            return

        await show_ratio_menu(context, query)
        return

    # فتح قائمة النسب القياسية (المقاس العام)
    if data == "ratio_menu":
        await show_standard_ratio_menu(context, query)
        return

    # اختيار "نفس مقاس الصورة المرسلة"
    if data == "ratio_same":
        photo_bytes = context.user_data.get("photo_bytes")
        if not photo_bytes:
            await query.edit_message_text(t(context, "session_expired"))
            return
        context.user_data["selected_ratio"] = compute_image_ratio(photo_bytes)
        await generate_and_send_prompt(query, context, update.effective_chat.id)
        return

    # اختيار نسبة قياسية من قائمة "المقاس العام"
    if data.startswith("ratio_std_"):
        selected_ratio = data.replace("ratio_std_", "", 1)
        context.user_data["selected_ratio"] = selected_ratio
        await generate_and_send_prompt(query, context, update.effective_chat.id)
        return


# تشغيل البوت
def main():
    # تشغيل خادم وهمي في thread منفصل لإرضاء فحص المنفذ في Render (Web Service)
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
