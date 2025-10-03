# --- START OF FILE app.py ---

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

# --- CARREGAMENTO E VALIDAÇÃO DE VARIÁVEIS ---
load_dotenv()
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
MERCADO_PAGO_ACCESS_TOKEN = os.getenv("MERCADO_PAGO_ACCESS_TOKEN")
GROUP_CHAT_ID_STR = os.getenv("GROUP_CHAT_ID")
PAYMENT_AMOUNT_STR = os.getenv("PAYMENT_AMOUNT")
WEBHOOK_BASE_URL = os.getenv("WEBHOOK_BASE_URL")

# Validação imediata
required_vars = {
    "TELEGRAM_BOT_TOKEN": TELEGRAM_BOT_TOKEN,
    "MERCADO_PAGO_ACCESS_TOKEN": MERCADO_PAGO_ACCESS_TOKEN,
    "GROUP_CHAT_ID": GROUP_CHAT_ID_STR,
    "PAYMENT_AMOUNT": PAYMENT_AMOUNT_STR,
    "WEBHOOK_BASE_URL": WEBHOOK_BASE_URL
}
if any(v is None for v in required_vars.values()):
    missing = [k for k, v in required_vars.items() if v is None]
    logger.critical(f"ERRO CRÍTICO: Variáveis de ambiente faltando: {', '.join(missing)}")
    sys.exit(1)

try:
    GROUP_CHAT_ID = int(GROUP_CHAT_ID_STR)
    PAYMENT_AMOUNT = float(PAYMENT_AMOUNT_STR)
except (ValueError, TypeError):
    logger.critical("ERRO CRÍTICO: GROUP_CHAT_ID ou PAYMENT_AMOUNT não são números válidos.")
    sys.exit(1)

NOTIFICATION_URL = f"{WEBHOOK_BASE_URL}/webhook/mercadopago"
global_bot_app = None
processed_payments = set()

# --- INICIALIZAÇÃO DO FLASK ---
app = Flask(__name__)

# --- FUNÇÕES DO BOT ---
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
    headers = {
        "Authorization": f"Bearer {MERCADO_PAGO_ACCESS_TOKEN}",
        "Content-Type": "application/json",
        "X-Idempotency-Key": str(uuid.uuid4())
    }
    payload = {
        "transaction_amount": PAYMENT_AMOUNT,
        "description": f"Acesso ao grupo exclusivo para {user_name}",
        "payment_method_id": "pix",
        "payer": { "email": f"user_{user_id}@telegram.bot", "first_name": user_name },
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

async def send_access_link(user_id: int):
    if not global_bot_app:
        logger.error("A aplicação do bot não está inicializada. Não é possível enviar o link.")
        return
    try:
        logger.info(f"Gerando link de convite para o usuário {user_id}...")
        invite_link = await global_bot_app.bot.create_chat_invite_link(chat_id=GROUP_CHAT_ID, member_limit=1)
        success_message = (
            "🎉 Pagamento confirmado com sucesso!\n\n"
            "Seja bem-vindo(a) ao nosso grupo! Aqui está seu link de acesso exclusivo:\n\n"
            f"{invite_link.invite_link}\n\n"
            "⚠️ **Atenção:** Este link é de uso único e não pode ser compartilhado."
        )
        await global_bot_app.bot.send_message(chat_id=user_id, text=success_message)
        logger.info(f"✅ Acesso concedido com sucesso para o usuário {user_id}")
    except Exception as e:
        logger.error(f"Falha ao enviar link de acesso para o usuário {user_id}: {e}", exc_info=True)

def process_approved_payment(payment_id: str):
    if payment_id in processed_payments:
        logger.info(f"Pagamento {payment_id} já foi processado anteriormente.")
        return
    payment_details_url = f"https://api.mercadopago.com/v1/payments/{payment_id}"
    headers = {"Authorization": f"Bearer {MERCADO_PAGO_ACCESS_TOKEN}"}
    try:
        response = requests.get(payment_details_url, headers=headers)
        response.raise_for_status()
        payment_info = response.json()
        status = payment_info.get("status")
        external_reference = payment_info.get("external_reference")
        logger.info(f"Detalhes do pagamento {payment_id}: status={status}, external_reference={external_reference}")
        if status == "approved" and external_reference:
            user_id = int(external_reference)
            processed_payments.add(payment_id)
            if global_bot_app and global_bot_app.loop:
                asyncio.run_coroutine_threadsafe(send_access_link(user_id), global_bot_app.loop)
            else:
                logger.error("Bot ou seu loop de eventos não estão prontos. Não foi possível agendar o envio do link.")
        else:
            logger.info(f"Pagamento {payment_id} não está aprovado ou sem external_reference. Status: {status}")
    except requests.exceptions.RequestException as e:
        logger.error(f"Erro ao processar pagamento {payment_id}: {e}", exc_info=True)

# --- ROTAS DO FLASK ---
@app.route("/")
def health_check():
    return "Bot is alive!", 200

@app.route("/webhook/mercadopago", methods=['POST'])
def mercadopago_webhook():
    data = request.get_json(silent=True)
    if not data:
        logger.warning("Webhook recebido sem dados JSON")
        return "Bad Request", 400

    logger.info(f"Webhook do MP recebido: {data}")
    payment_id = None

    # --- LÓGICA DE EXTRAÇÃO DO ID CORRIGIDA ---
    if data.get("topic") == "payment" or "payment" in data.get("action", ""):
        # Formato novo (às vezes 'resource' é uma URL, às vezes só o ID)
        resource = data.get("resource", "")
        if "payments/" in resource:
            payment_id = resource.split("payments/")[-1]
        elif resource.isdigit(): # Checa se o 'resource' é apenas o ID numérico
             payment_id = resource
        else:
            # Formato antigo ou novo com 'data.id'
            payment_id = data.get("data", {}).get("id")

    if payment_id:
        logger.info(f"Processando notificação do pagamento: {payment_id}")
        threading.Thread(target=process_approved_payment, args=(str(payment_id),)).start()
    else:
        logger.warning(f"Webhook recebido sem payment_id identificável: {data}")

    return "OK", 200

# --- INICIALIZAÇÃO DO BOT ---
def run_bot_polling():
    global global_bot_app
    logger.info("Configurando a aplicação do bot na sua própria thread...")
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        application = Application.builder().token(TELEGRAM_BOT_TOKEN).build()
        global_bot_app = application
        global_bot_app.loop = loop
        application.add_handler(CommandHandler("start", start))
        application.add_handler(CallbackQueryHandler(button_handler))
        logger.info("Aplicação do bot configurada. Iniciando polling...")
        application.run_polling(stop_signals=None, drop_pending_updates=True)
    except Exception as e:
        logger.critical(f"ERRO FATAL NA THREAD DO BOT: {e}", exc_info=True)

# --- REMOVIDO: O CÓDIGO QUE INICIAVA A THREAD FOI MOVIDO PARA gunicorn_config.py ---

# --- END OF FILE app.py ---
