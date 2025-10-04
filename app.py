# --- START OF FILE app.py (FINAL & FULLY LOGGED) ---

import os
import logging
import httpx
import json
import uuid
import base64
import io
import asyncio
import sys
from datetime import datetime, timedelta, timezone

from quart import Quart, request, abort
from dotenv import load_dotenv
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes, JobQueue
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

if not all([TELEGRAM_BOT_TOKEN, TELEGRAM_SECRET_TOKEN, MERCADO_PAGO_ACCESS_TOKEN, GROUP_CHAT_ID_STR, PAYMENT_AMOUNT_STR, WEBHOOK_BASE_URL]):
    logger.critical("ERRO: Variáveis de ambiente essenciais não configuradas.")
    sys.exit(1)

try:
    GROUP_CHAT_ID = int(GROUP_CHAT_ID_STR)
    PAYMENT_AMOUNT = float(PAYMENT_AMOUNT_STR)
except (ValueError, TypeError):
    logger.critical("ERRO CRÍTICO nos valores de ambiente.")
    sys.exit(1)

NOTIFICATION_URL = f"{WEBHOOK_BASE_URL}/webhook/mercadopago"
TELEGRAM_WEBHOOK_URL = f"{WEBHOOK_BASE_URL}/webhook/telegram"

processed_payments = set()

# --- INICIALIZAÇÃO DO BOT ---
request_config = {'connect_timeout': 10.0, 'read_timeout': 20.0}
httpx_request = HTTPXRequest(**request_config)
bot_app = (
    Application.builder()
    .token(TELEGRAM_BOT_TOKEN)
    .request(httpx_request)
    .job_queue(JobQueue())
    .build()
)

app = Quart(__name__)

# --- HANDLERS DO BOT ---
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
        payment_data = await create_pix_payment(user_id, user_name)
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
async def create_pix_payment(user_id: int, user_name: str) -> dict:
    url = "https://api.mercadopago.com/v1/payments"
    headers = { "Authorization": f"Bearer {MERCADO_PAGO_ACCESS_TOKEN}", "Content-Type": "application/json", "X-Idempotency-Key": str(uuid.uuid4()) }
    payload = {"transaction_amount": PAYMENT_AMOUNT, "description": f"Acesso ao grupo exclusivo para {user_name}", "payment_method_id": "pix", "payer": { "email": f"user_{user_id}@telegram.bot", "first_name": user_name }, "notification_url": NOTIFICATION_URL, "external_reference": str(user_id)}
    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(url, headers=headers, json=payload, timeout=10)
            response.raise_for_status()
        data = response.json()
        return { 'qr_code_base64': data['point_of_interaction']['transaction_data']['qr_code_base64'], 'pix_copy_paste': data['point_of_interaction']['transaction_data']['qr_code'] }
    except httpx.HTTPError as e:
        logger.error(f"Erro HTTP ao criar pagamento no Mercado Pago: {e}")
        return None

async def send_access_link_job(context: ContextTypes.DEFAULT_TYPE):
    user_id = context.job.data['user_id']
    payment_id = context.job.data['payment_id']
    logger.info(f"[JOB][{payment_id}] Iniciando tarefa para enviar link ao usuário {user_id}.")
    try:
        logger.info(f"[JOB][{payment_id}] Gerando link de convite...")
        expire_date = datetime.now(timezone.utc) + timedelta(hours=1)
        invite_link = await bot_app.bot.create_chat_invite_link(chat_id=GROUP_CHAT_ID, member_limit=1, expire_date=expire_date)

        logger.info(f"[JOB][{payment_id}] Link gerado. Enviando mensagem para {user_id}...")
        success_message = (f"🎉 Pagamento confirmado!\n\nSeja bem-vindo(a)! Aqui está seu link de acesso exclusivo:\n\n{invite_link.invite_link}\n\n⚠️ **Atenção:** Este link é de uso único e expira em 1 hora.")
        await bot_app.bot.send_message(chat_id=user_id, text=success_message)
        logger.info(f"✅ [JOB][{payment_id}] Acesso concedido com sucesso para o usuário {user_id}")
    except Exception as e:
        logger.error(f"❌ [JOB][{payment_id}] Falha CRÍTICA ao enviar link de acesso para o usuário {user_id}: {e}", exc_info=True)

async def process_approved_payment(payment_id: str):
    logger.info(f"[{payment_id}] Iniciando processamento do pagamento.")

    if payment_id in processed_payments:
        logger.warning(f"[{payment_id}] Pagamento já processado anteriormente. Ignorando.")
        return

    processed_payments.add(payment_id)

    payment_details_url = f"https://api.mercadopago.com/v1/payments/{payment_id}"
    headers = {"Authorization": f"Bearer {MERCADO_PAGO_ACCESS_TOKEN}"}
    try:
        logger.info(f"[{payment_id}] Consultando detalhes do pagamento na API do MP.")
        async with httpx.AsyncClient() as client:
            response = await client.get(payment_details_url, headers=headers)
            response.raise_for_status()
        payment_info = response.json()
        status = payment_info.get("status")
        external_reference = payment_info.get("external_reference")
        logger.info(f"[{payment_id}] Detalhes recebidos: Status='{status}', UserID='{external_reference}'.")

        if status == "approved" and external_reference:
            user_id = int(external_reference)
            logger.info(f"[{payment_id}] Pagamento APROVADO. Agendando job para enviar link ao usuário {user_id}.")
            bot_app.job_queue.run_once(send_access_link_job, when=0, data={'user_id': user_id, 'payment_id': payment_id})
        else:
            logger.warning(f"[{payment_id}] Pagamento não está 'approved'. Removendo do cache para futuras notificações.")
            processed_payments.remove(payment_id)
    except httpx.HTTPError as e:
        logger.error(f"[{payment_id}] Erro HTTP ao consultar pagamento: {e}. Removendo do cache para nova tentativa.")
        processed_payments.remove(payment_id)
    except Exception as e:
        logger.error(f"[{payment_id}] Erro inesperado ao processar pagamento: {e}. Removendo do cache.", exc_info=True)
        processed_payments.remove(payment_id)

# --- CICLO DE VIDA DO QUART ---
@app.before_serving
async def startup():
    await bot_app.initialize()
    await bot_app.start()
    await bot_app.bot.set_webhook(url=TELEGRAM_WEBHOOK_URL, secret_token=TELEGRAM_SECRET_TOKEN)
    logger.info("Bot inicializado e webhook registrado.")

@app.after_serving
async def shutdown():
    await bot_app.stop()
    await bot_app.shutdown()
    logger.info("Bot desligado.")

# --- ROTAS ---
@app.route("/")
async def health_check():
    return "Bot is alive and running!", 200

@app.route("/webhook/telegram", methods=['POST'])
async def telegram_webhook():
    secret_token = request.headers.get("X-Telegram-Bot-Api-Secret-Token")
    if secret_token != TELEGRAM_SECRET_TOKEN:
        abort(403)
    try:
        update_data = await request.get_json()
        update = Update.de_json(update_data, bot_app.bot)
        await bot_app.process_update(update)
        return "OK", 200
    except Exception as e:
        logger.error(f"Erro no webhook do Telegram: {e}", exc_info=True)
        return "Error", 500

@app.route("/webhook/mercadopago", methods=['POST'])
async def mercadopago_webhook():
    data = await request.get_json()
    if not data:
        return "Bad Request", 400

    payment_id = data.get("data", {}).get("id")
    if payment_id:
        logger.info(f"Webhook do MP recebido para o pagamento {payment_id}. Agendando processamento.")
        asyncio.create_task(process_approved_payment(str(payment_id)))

    return "OK", 200
