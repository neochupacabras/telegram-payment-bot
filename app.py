# --- IMPORTS ---
import os
import logging
import requests
import json
import uuid
import base64
import io
import threading
import sys
import asyncio

from flask import Flask, request
from dotenv import load_dotenv
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes

# --- CONFIGURAÇÃO DE LOGGING ---
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    stream=sys.stdout,
)
logger = logging.getLogger(__name__)

# --- CARREGAMENTO DE VARIÁVEIS DE AMBIENTE ---
load_dotenv()

# Validação imediata
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
MERCADO_PAGO_ACCESS_TOKEN = os.getenv("MERCADO_PAGO_ACCESS_TOKEN")
GROUP_CHAT_ID_STR = os.getenv("GROUP_CHAT_ID")
PAYMENT_AMOUNT_STR = os.getenv("PAYMENT_AMOUNT")
WEBHOOK_BASE_URL = os.getenv("WEBHOOK_BASE_URL")

missing_vars = [k for k, v in locals().items() if k.isupper() and v is None]
if missing_vars:
    logger.critical(f"ERRO CRÍTICO: Variáveis de ambiente faltando: {', '.join(missing_vars)}")
    sys.exit(1)

try:
    GROUP_CHAT_ID = int(GROUP_CHAT_ID_STR)
    PAYMENT_AMOUNT = float(PAYMENT_AMOUNT_STR)
except (ValueError, TypeError):
    logger.critical("ERRO CRÍTICO: GROUP_CHAT_ID ou PAYMENT_AMOUNT não são números válidos.")
    sys.exit(1)

NOTIFICATION_URL = f"{WEBHOOK_BASE_URL}/webhook/mercadopago"
global_bot_app = None

# --- INICIALIZAÇÃO DO FLASK ---
# O Gunicorn vai procurar por esta variável `app`
app = Flask(__name__)

# --- FUNÇÕES DO BOT TELEGRAM ---
# (As funções start, button_handler, create_pix_payment, grant_access continuam exatamente as mesmas)
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    welcome_message = (
        f"Olá, {user.first_name}!\n\n"
        "Bem-vindo(a) ao bot de acesso ao nosso grupo exclusivo.\n\n"
        "O grupo contém [descreva os benefícios do seu grupo aqui].\n\n"
        f"O valor do acesso único é de R$ {PAYMENT_AMOUNT:.2f}.\n\n"
        "Para entrar, clique no botão abaixo e realize o pagamento via PIX."
    )
    keyboard = [[InlineKeyboardButton("✅ Quero Entrar (Pagar com PIX)", callback_data='generate_payment')]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text(welcome_message, reply_markup=reply_markup)

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    chat_id = query.message.chat_id
    user_id = query.from_user.id
    user_name = query.from_user.first_name

    if query.data == 'generate_payment':
        await query.edit_message_text(text="Gerando sua cobrança PIX, aguarde um instante...")
        payment_data = create_pix_payment(user_id, user_name)
        if payment_data:
            qr_code_base64 = payment_data['qr_code_base64']
            pix_copy_paste = payment_data['pix_copy_paste']
            qr_code_image = base64.b64decode(qr_code_base64)
            image_stream = io.BytesIO(qr_code_image)
            image_stream.name = 'pix_qr_code.png'
            await context.bot.send_photo(chat_id=chat_id, photo=image_stream, caption="Use o QR Code acima ou o código abaixo para pagar.")
            await context.bot.send_message(chat_id=chat_id, text=f"PIX Copia e Cola:\n\n`{pix_copy_paste}`", parse_mode='MarkdownV2')
            await context.bot.send_message(chat_id=chat_id, text="Assim que o pagamento for confirmado, você receberá o link de acesso automaticamente!")
        else:
            await query.edit_message_text(text="Desculpe, ocorreu um erro ao gerar sua cobrança. Tente novamente mais tarde.")

def create_pix_payment(user_id: int, user_name: str) -> dict:
    url = "https://api.mercadopago.com/v1/payments"
    idempotency_key = str(uuid.uuid4())
    headers = {
        "Authorization": f"Bearer {MERCADO_PAGO_ACCESS_TOKEN}",
        "Content-Type": "application/json",
        "X-Idempotency-Key": idempotency_key
    }
    payload = {
        "transaction_amount": PAYMENT_AMOUNT,
        "description": f"Acesso ao grupo exclusivo para {user_name}",
        "payment_method_id": "pix",
        "payer": {"email": f"user_{user_id}@telegram.bot", "first_name": user_name},
        "notification_url": NOTIFICATION_URL,
        "external_reference": str(user_id)
    }
    try:
        response = requests.post(url, headers=headers, data=json.dumps(payload))
        response.raise_for_status()
        data = response.json()
        return {
            'qr_code_base64': data['point_of_interaction']['transaction_data']['qr_code_base64'],
            'pix_copy_paste': data['point_of_interaction']['transaction_data']['qr_code']
        }
    except requests.exceptions.RequestException as e:
        logger.error(f"Erro ao criar pagamento no Mercado Pago: {e}", exc_info=True)
        return None

async def grant_access(user_id: int):
    try:
        link = await global_bot_app.bot.create_chat_invite_link(chat_id=GROUP_CHAT_ID, member_limit=1)
        success_message = (
            "🎉 Pagamento confirmado com sucesso!\n\n"
            "Seja bem-vindo(a) ao nosso grupo! Aqui está seu link de acesso exclusivo:\n\n"
            f"{link.invite_link}\n\n"
            "⚠️ **Atenção:** Este link é de uso único e não pode ser compartilhado."
        )
        await global_bot_app.bot.send_message(chat_id=user_id, text=success_message)
        logger.info(f"Acesso concedido para o usuário {user_id}")
    except Exception as e:
        logger.error(f"Erro ao conceder acesso para o usuário {user_id}: {e}", exc_info=True)
        await global_bot_app.bot.send_message(chat_id=user_id, text="Houve um problema ao gerar seu link de acesso. Por favor, entre em contato com o suporte.")

# --- ROTAS DO FLASK ---
@app.route("/")
def health_check():
    return "Bot is alive!", 200

@app.route("/webhook/mercadopago", methods=['POST'])
def mercadopago_webhook():
    data = request.get_json(silent=True)
    if not data:
        logger.warning("Webhook do MP recebido sem corpo JSON.")
        return "Bad Request", 400

    logger.info(f"Webhook do MP recebido: {data}")

    action = data.get("action")
    if action == "payment.updated":
        payment_id = data.get("data", {}).get("id")
        if payment_id:
            payment_details_url = f"https://api.mercadopago.com/v1/payments/{payment_id}"
            headers = {"Authorization": f"Bearer {MERCADO_PAGO_ACCESS_TOKEN}"}
            response = requests.get(payment_details_url, headers=headers)

            if response.status_code == 200:
                payment_info = response.json()
                if payment_info.get("status") == "approved" and payment_info.get("external_reference"):
                    user_id = int(payment_info["external_reference"])
                    logger.info(f"Pagamento aprovado para o usuário {user_id}")
                    asyncio.run_coroutine_threadsafe(grant_access(user_id), global_bot_app.loop)
            else:
                logger.error(f"Falha ao buscar detalhes do pagamento {payment_id}. Status: {response.status_code}")

    return "OK", 200

# --- LÓGICA DE INICIALIZAÇÃO DO BOT (THREAD SEPARADA) ---
def run_bot_polling():
    global global_bot_app
    logger.info("Iniciando a aplicação do bot...")

    try:
        # AQUI O CÓDIGO TENTA INICIAR E PODE CRASHAR
        application = Application.builder().token(TELEGRAM_BOT_TOKEN).build()
        global_bot_app = application
        global_bot_app.loop = asyncio.get_event_loop()

        application.add_handler(CommandHandler("start", start))
        application.add_handler(CallbackQueryHandler(button_handler))

        logger.info("Polling do bot iniciado com sucesso!")
        application.run_polling()

    except Exception as e:
        # SE CAIR AQUI, O ERRO SERÁ CLARAMENTE LOGADO
        logger.critical(f"ERRO FATAL NA THREAD DO BOT: {e}", exc_info=True)
        # Tenta enviar um aviso para o administrador (opcional)
        # asyncio.run(send_admin_alert(f"O bot falhou ao iniciar polling: {e}"))
        pass # A thread morre, mas o servidor Flask continua vivo
