import os
import io
import logging
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
from google import generativeai as genai
from PIL import Image
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
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

# إعداد نموذج Gemini
genai.configure(api_key=GEMINI_API_KEY)
model = genai.GenerativeModel("gemini-3.6-flash")


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
            "👋 **أهلاً بك في بوت استخراج البرومبت الاحترافي!**\n\n"
            "أرسل لي أي صورة الآن، وسأقوم بتحليلها واستخراج برومبت دقيق جداً ومفصل يمكنك نسخه بضغطة واحدة."
        ),
        "choose_prompt_lang": "🌐 **الخطوة 1/2:** اختر لغة البرومبت المطلوب:",
        "choose_detail": "⚙️ **الخطوة 2/2:** اختر مستوى تفصيل البرومبت:",
        "btn_short": "⚡ قصير وموجز",
        "btn_medium": "⚖️ متوسط",
        "btn_detailed": "🔍 تفصيلي وفائق الدقة (شامل جداً)",
        "btn_back": "🔙 رجوع للغة",
        "btn_cancel": "❌ إلغاء",
        "cancelled": "🚫 تم إلغاء العملية. يمكنك إرسال صورة جديدة في أي وقت.",
        "session_expired": "⚠️ انتهت الجلسة. يرجى إعادة إرسال الصورة من جديد.",
        "analyzing": "⏳ جاري تحليل عناصر الصورة واستخراج البرومبت بدقة فائقة...",
        "success_title": "✅ **تم استخراج البرومبت بنجاح!**\n*(اضغط على النص أدناه لنسخه فوراً)*\n\n",
        "btn_retry": "🔄 استخراج بمستوى/لغة أخرى",
        "btn_new_photo": "📸 أرسل صورة جديدة",
        "error_generation": "❌ حدث خطأ أثناء تحليل الصورة: ",
    },
    "en": {
        "welcome": (
            "👋 **Welcome to the Professional Prompt Extractor Bot!**\n\n"
            "Send me any image now, and I'll analyze it to extract a highly accurate, detailed prompt you can copy with one tap."
        ),
        "choose_prompt_lang": "🌐 **Step 1/2:** Choose the language of the prompt:",
        "choose_detail": "⚙️ **Step 2/2:** Choose the detail level of the prompt:",
        "btn_short": "⚡ Short & Concise",
        "btn_medium": "⚖️ Medium",
        "btn_detailed": "🔍 Detailed & Ultra-Precise (Comprehensive)",
        "btn_back": "🔙 Back to language",
        "btn_cancel": "❌ Cancel",
        "cancelled": "🚫 Operation cancelled. You can send a new image anytime.",
        "session_expired": "⚠️ Session expired. Please resend the image.",
        "analyzing": "⏳ Analyzing image elements and extracting an ultra-precise prompt...",
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


# استقبال الصورة
async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    photo_file = await update.message.photo[-1].get_file()
    photo_bytes = await photo_file.download_as_bytearray()
    context.user_data["photo_bytes"] = photo_bytes

    if "ui_lang" not in context.user_data:
        # لم يتم اختيار لغة الواجهة بعد، نطلبها أولاً ثم نكمل تلقائياً
        await show_ui_language_menu(update.message.reply_text)
        return

    await show_prompt_language_menu(context, update.message.reply_text)


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
        await query.edit_message_text(t(context, "cancelled"))
        return

    # زر الرجوع لاختيار لغة البرومبت
    if data == "back_to_lang":
        await show_prompt_language_menu(context, query.edit_message_text)
        return

    # اختيار لغة البرومبت
    if data.startswith("lang_"):
        selected_lang = data.split("_")[1]
        context.user_data["selected_lang"] = selected_lang
        await show_detail_menu(context, query)
        return

    # اختيار مستوى التفصيل وتوليد البرومبت
    if data.startswith("detail_"):
        selected_length = data.split("_")[1]
        selected_lang = context.user_data.get("selected_lang", "en")
        photo_bytes = context.user_data.get("photo_bytes")

        if not photo_bytes:
            await query.edit_message_text(t(context, "session_expired"))
            return

        await query.edit_message_text(t(context, "analyzing"))

        # توجيهات صارمة ومفصلة لضمان أقصى درجة من الدقة دون مقدمات
        system_instructions = {
            ("ar", "short"): (
                "اكتب برومبت قصير وموجز باللغة العربية (2-3 جمل) يصف الفكرة الرئيسية والموضوع الأساسي لهذه الصورة. "
                "أرجع نص البرومبت فقط بدون أي مقدمات."
            ),
            ("ar", "medium"): (
                "اكتب برومبت متوسط التفاصيل باللغة العربية يصف موضوع الصورة، الأسلوب الفني، الألوان، والإضاءة بإيجاز واضح. "
                "أرجع نص البرومبت فقط بدون أي مقدمات."
            ),
            ("ar", "detailed"): (
                "قم بتحليل هذه الصورة بأقصى درجة ممكنة من الدقة والعمق، واكتب برومبت شديد التفصيل والشمول باللغة العربية "
                "لإعادة إنتاجها بدقة متناهية عبر أدوات الذكاء الاصطناعي التوليدية. يجب أن يغطي البرومبت جميع النقاط التالية بشكل مكثف:\n"
                "1. Subject Details: وصف دقيق جداً للشخصيات/الكائنات الرئيسية (الملامح، تعابير الوجه، وضعية الجسد، الحركة، اتجاه النظر).\n"
                "2. Wardrobe & Textures: وصف الملابس والإكسسوارات وخاماتها ودرجة تفاصيلها (تجاعيد، بلل، غبار، ثلج، خدوش).\n"
                "3. Art Style/Rendering Engine: النمط الفني أو محرك العرض (مثل Unreal Engine 5, Octane Render, تصوير فوتوغرافي واقعي 8K).\n"
                "4. Lighting & Atmosphere: نوع الإضاءة ومصدرها، اتجاهها، حدتها، الظلال، التباين، الجو العام والمزاج البصري.\n"
                "5. Camera & Composition: زاوية الكاميرا، نوع اللقطة، العدسة المستخدمة، عمق المجال، تكوين الكادر وقواعد التأطير.\n"
                "6. Colors & Palette: لوحة الألوان السائدة، التباين اللوني، درجات الحرارة اللونية (دافئة/باردة).\n"
                "7. Environment & Weather: تفاصيل الخلفية والبيئة المحيطة، حالة الطقس، الجسيمات العالقة في الهواء (ثلج، ضباب، غبار).\n"
                "8. Mood & Atmosphere: الشعور العام والانطباع الفني الذي تنقله الصورة.\n"
                "9. Technical Tags: إضافة كلمات مفتاحية تقنية ختامية مثل الدقة (8K)، نسبة الأبعاد، ومستوى الواقعية.\n"
                "أرجع نص البرومبت فقط الصافي المخصص للنسخ المباشر، متصلاً وسلساً، بدون أرقام أو عناوين فرعية أو شروحات جانبية."
            ),
            ("en", "short"): (
                "Write a concise image generation prompt in English (2-3 sentences) covering the core subject and idea of this photo. "
                "Output ONLY the raw prompt text."
            ),
            ("en", "medium"): (
                "Write a medium-detailed image generation prompt in English describing the subject, style, lighting, composition, and colors clearly. "
                "Output ONLY the raw prompt text."
            ),
            ("en", "detailed"): (
                "Analyze this image with maximum possible depth and precision, and write an extremely detailed, hyper-comprehensive "
                "image generation prompt in English suitable for exact recreation via generative AI tools. The prompt must densely cover ALL of the following:\n"
                "1. Subject Details: precise facial features/expressions, body posture, gesture, gaze direction, exact pose of every main subject.\n"
                "2. Wardrobe & Textures: clothing, accessories, and material textures in detail (wrinkles, moisture, dust, snow, wear).\n"
                "3. Art Style/Rendering Engine: the exact artistic style or render engine (e.g. Unreal Engine 5, Octane Render, photorealistic 8K photography).\n"
                "4. Lighting & Atmosphere: light source type, direction, intensity, shadow behavior, contrast, cinematic mood.\n"
                "5. Camera & Composition: camera angle, shot type, lens/focal length, depth of field, framing and compositional rules.\n"
                "6. Colors & Palette: dominant color palette, color contrast, warm/cool color temperature balance.\n"
                "7. Environment & Weather: background and environment details, weather conditions, airborne particles (snow, fog, dust).\n"
                "8. Mood & Atmosphere: the overall emotional tone and artistic impression conveyed.\n"
                "9. Technical Tags: closing technical keywords such as resolution (8K), aspect ratio, and realism level.\n"
                "Output ONLY the pure raw prompt text suitable for direct copy-pasting, flowing naturally as one prompt. "
                "Do NOT include numbering, markdown headings, or explanations."
            ),
        }

        instruction = system_instructions.get((selected_lang, selected_length))

        try:
            image = Image.open(io.BytesIO(photo_bytes))
            response = model.generate_content([instruction, image])
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

            result_message = t(context, "success_title") + f"```\n{generated_prompt}\n```"

            await query.message.reply_text(result_message, parse_mode="Markdown", reply_markup=reply_markup)
            await query.delete_message()

        except Exception as e:
            logging.error(f"خطأ أثناء التوليد: {e}")
            await query.message.reply_text(t(context, "error_generation") + str(e))


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
