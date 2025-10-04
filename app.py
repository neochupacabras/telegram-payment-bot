# --- START OF FILE app.py (FINAL WEBHOOK ARCHITECTURE) ---

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
from datetime import datetime, timedelta

from flask import Flask, request, abort
from dotenv import load_dotenv
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes
from telegram.request import HTTPXRequest

# --- CONFIGURAÇÃO DE LOGGING ---
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', stream=sys.stdout)
logger = logging.getLogger(__name__)

# --- CARREGAMENTO E VALIDAÇÃO DE VARIÁVEIS ---
load_dotenv()
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_SECRET_TOKEN = os.getenv("TELEGRAM_SECRET_TOKEN")
MERCADO_PAGO_ACCESS_TOKEN = os.getenv("MERCADO_PAGO_ACCESS_TOKEN")
GROUP_CHAT_ID_STR = os.getenv("GROUP_CHAT_ID")
PAYMENT_AMOUNT_STR = os.getenv("PAYMENT_AMOUNT")
WEBHOOK_BASE_URL = os.getenv("WEBHOOK_BASE_URL")

required_vars = {
    "TELEGRAM_BOT_TOKEN": TELEGRAM_BOT_TOKEN, "TELEGRAM_SECRET_TOKEN": TELEGRAM_SECRET_TOKEN,
    "MERCADO_PAGO_ACCESS_TOKEN": MERCADO_PAGO_ACCESS_TOKEN, "GROUP_CHAT_ID": GROUP_CHAT_ID_STR,
    "PAYMENT_AMOUNT": PAYMENT_AMOUNT_STR, "WEBHOOK_BASE_URL": WEBHOOK_BASE_URL
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
TELEGRAM_WEBHOOK_URL = f"{WEBHOOK_BASE_URL}/webhook/telegram"

processed_payments = set()
payment_processing_lock = threading.Lock()

# --- INICIALIZAÇÃO DO BOT (MODO WEBHOOK) ---
request_config = {'connect_timeout': 10.0, 'read_timeout': 20.0, 'write_timeout': 30.0}
httpx_request = HTTPXRequest(**request_config)
bot_app = Application.builder().token(TELEGRAM_BOT_TOKEN).request(httpx_request).build()

# --- INICIALIZAÇÃO DO FLASK ---
app = Flask(__name__)

# --- FUNÇÕES DE LÓGICA DO BOT (Handlers) ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    welcome_message = (f"Olá, {user.first_name}!\n\nBem-vindo(a) ao bot de acesso ao nosso grupo exclusivo.\n\nO valor do acesso único é de R$ {PAYMENT_AMOUNT:.2f}.\n\nPara entrar, clique no botão abaixo e realize o pagamento via PIX.")
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
            qr_code_image = base64.b64decode(payment_data['qr_code_base64'])
            image_stream = io.BytesIO(qr_code_image)
            await context.bot.send_photo(chat_id=chat_id, photo=image_stream, caption="Use o QR Code acima ou o código abaixo para pagar.")
            await context.bot.send_message(chat_id=chat_id, text=f"PIX Copia e Cola:\n\n`{payment_data['pix_copy_paste']}`", parse_mode='MarkdownV2')
            await context.bot.send_message(chat_id=chat_id, text="Assim que o pagamento for confirmado, você receberá o link de acesso automaticamente!")
        else:
            await query.edit_message_text(text="Desculpe, ocorreu um erro ao gerar sua cobrança. Tente novamente mais tarde.")

bot_app.add_handler(CommandHandler("start", start))
bot_app.add_handler(CallbackQueryHandler(button_handler))

# --- FUNÇÕES DE PAGAMENTO ---
def create_pix_payment(user_id: int, user_name: str) -> dict:
    url = "https://api.mercadopago.com/v1/payments"
    headers = { "Authorization": f"Bearer {MERCADO_PAGO_ACCESS_TOKEN}", "Content-Type": "application/json", "X-Idempotency-Key": str(uuid.uuid4()) }
    payload = {"transaction_amount": PAYMENT_AMOUNT, "description": f"Acesso ao grupo exclusivo para {user_name}", "payment_method_id": "pix", "payer": { "email": f"user_{user_id}@telegram.bot", "first_name": user_name }, "notification_url": NOTIFICATION_URL, "external_reference": str(user_id)}
    try:
        response = requests.post(url, headers=headers, data=json.dumps(payload), timeout=10)
        response.raise_for_status()
        data = response.json()
        return { 'qr_code_base64': data['point_of_interaction']['transaction_data']['qr_code_base64'], 'pix_copy_paste': data['point_of_interaction']['transaction_data']['qr_code'] }
    except requests.exceptions.RequestException as e:
        logger.error(f"Erro ao criar pagamento no Mercado Pago: {e}")
        return None

async def send_access_link_job(context: ContextTypes.DEFAULT_TYPE):
    user_id = context.job.data['user_id']
    try:
        logger.info(f"Gerando link de convite para o usuário {user_id}.")
        expire_date = datetime.now() + timedelta(minutes=15)
        invite_link = await bot_app.bot.create_chat_invite_link(chat_id=GROUP_CHAT_ID, member_limit=1, expire_date=expire_date)
        success_message = (f"🎉 Pagamento confirmado com sucesso!\n\nSeja bem-vindo(a) ao nosso grupo! Aqui está seu link de acesso exclusivo:\n\n{invite_link.invite_link}\n\n⚠️ **Atenção:** Este link é de uso único e expira em 15 minutos.")
        await bot_app.bot.send_message(chat_id=user_id, text=success_message)
        logger.info(f"✅ Acesso concedido com sucesso para o usuário {user_id}")
    except Exception as e:
        logger.error(f"Falha ao enviar link de acesso para o usuário {user_id}: {e}")

def process_approved_payment(payment_id: str):
    with payment_processing_lock:
        if payment_id in processed_payments:
            logger.info(f"Pagamento {payment_id} já foi processado ou está em processamento.")
            return
        processed_payments.add(payment_id)

    payment_details_url = f"https://api.mercadopago.com/v1/payments/{payment_id}"
    headers = {"Authorization": f"Bearer {MERCADO_PAGO_ACCESS_TOKEN}"}
    try:
        response = requests.get(payment_details_url, headers=headers)
        response.raise_for_status()
        payment_info = response.json()
        status = payment_info.get("status")
        external_reference = payment_info.get("external_reference")

        if status == "approved" and external_reference:
            user_id = int(external_reference)
            # --- CORREÇÃO 2: USANDO A JOB QUEUE PARA MAIOR EFICIÊNCIA ---
            # Em vez de asyncio.run(), agendamos a tarefa na fila do bot.
            bot_app.job_queue.run_once(send_access_link_job, when=0, data={'user_id': user_id})
        else:
            logger.info(f"Pagamento {payment_id} não aprovado (Status: {status}). Removendo do cache.")
            with payment_processing_lock:
                if payment_id in processed_payments:
                    processed_payments.remove(payment_id)
    except requests.exceptions.RequestException as e:
        logger.error(f"Erro ao processar pagamento {payment_id}: {e}")
        with payment_processing_lock:
            if payment_id in processed_payments:
                processed_payments.remove(payment_id)

# --- ROTAS DO FLASK (WEBHOOKS) ---
@app.route("/")
def health_check():
    return "Bot is alive and running in webhook mode!", 200

@app.route("/webhook/telegram", methods=['POST'])
async def telegram_webhook():
    secret_token = request.headers.get("X-Telegram-Bot-Api-Secret-Token")
    if secret_token != TELEGRAM_SECRET_TOKEN:
        logger.warning("Webhook do Telegram recebido com token secreto inválido.")
        abort(403)

    try:
        update_data = request.get_json(force=True)
        update = Update.de_json(update_data, bot_app.bot)
        await bot_app.process_update(update)
        return "OK", 200
    except Exception as e:
        logger.error(f"Erro ao processar webhook do Telegram: {e}", exc_info=True)
        return "Internal Server Error", 500

@app.route("/webhook/mercadopago", methods=['POST'])
def mercadopago_webhook():
    data = request.get_json(silent=True)
    if not data:
        return "Bad Request", 400

    logger.info(f"Webhook do MP recebido: {data}")
    payment_id = data.get("data", {}).get("id")

    if payment_id:
        threading.Thread(target=process_approved_payment, args=(str(payment_id),)).start()

    return "OK", 200

# --- FUNÇÃO DE SETUP: INICIALIZAÇÃO E REGISTRO DO WEBHOOK ---
async def main_setup():
    # --- CORREÇÃO 1: INICIALIZANDO A APLICAÇÃO ANTES DE USÁ-LA ---
    await bot_app.initialize()

    logger.info(f"Registrando webhook para a URL: {TELEGRAM_WEBHOOK_URL}")
    try:
        await bot_app.bot.set_webhook(
            url=TELEGRAM_WEBHOOK_URL,
            secret_token=TELEGRAM_SECRET_TOKEN,
            allowed_updates=Update.ALL_TYPES
        )
        logger.info("Webhook do Telegram registrado com sucesso!")
    except Exception as e:
        logger.error(f"Falha ao registrar webhook do Telegram: {e}")

# Ao iniciar a aplicação Flask, executa a função de setup.
if __name__ != '__main__':
    asyncio.run(main_setup())
