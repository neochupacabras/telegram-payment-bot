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
import time

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
required_vars = { "TELEGRAM_BOT_TOKEN": TELEGRAM_BOT_TOKEN, "MERCADO_PAGO_ACCESS_TOKEN": MERCADO_PAGO_ACCESS_TOKEN, "GROUP_CHAT_ID": GROUP_CHAT_ID_STR, "PAYMENT_AMOUNT": PAYMENT_AMOUNT_STR, "WEBHOOK_BASE_URL": WEBHOOK_BASE_URL }
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

# --- INICIALIZAÇÃO DO FLASK ---
app = Flask(__name__)

# --- FUNÇÕES DO BOT (SEM MUDANÇAS) ---
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
    headers = { "Authorization": f"Bearer {MERCADO_PAGO_ACCESS_TOKEN}", "Content-Type": "application/json", "X-Idempotency-Key": str(uuid.uuid4()) }
    payload = { "transaction_amount": PAYMENT_AMOUNT, "description": f"Acesso ao grupo exclusivo para {user_name}", "payment_method_id": "pix", "payer": {"email": f"user_{user_id}@telegram.bot", "first_name": user_name}, "notification_url": NOTIFICATION_URL, "external_reference": str(user_id) }
    try:
        response = requests.post(url, headers=headers, data=json.dumps(payload))
        response.raise_for_status()
        data = response.json()
        return { 'qr_code_base64': data['point_of_interaction']['transaction_data']['qr_code_base64'], 'pix_copy_paste': data['point_of_interaction']['transaction_data']['qr_code'] }
    except requests.exceptions.RequestException as e:
        logger.error(f"Erro ao criar pagamento no Mercado Pago: {e}", exc_info=True)
        return None

# --- ROTAS DO FLASK ---
# --- ROTAS DO FLASK ---
@app.route("/")
def health_check():
    return "Bot is alive!", 200

# ######################################################################
# SUBSTITUA A SUA FUNÇÃO DE WEBHOOK ANTIGA POR ESTA VERSÃO COMPLETA
# ######################################################################
@app.route("/webhook/mercadopago", methods=['POST'])
def mercadopago_webhook():
    data = request.get_json(silent=True)
    if not data:
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
                    logger.info(f"Pagamento aprovado para o usuário {user_id}. Gerando link de convite diretamente...")

                    # --- LÓGICA DE CONVITE INDEPENDENTE ---
                    # 1. Criar o link de convite
                    create_link_url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/createChatInviteLink"
                    link_payload = {"chat_id": GROUP_CHAT_ID, "member_limit": 1}
                    link_response = requests.post(create_link_url, json=link_payload)

                    if link_response.status_code == 200:
                        invite_link = link_response.json().get('result', {}).get('invite_link')
                        if invite_link:
                            # 2. Enviar a mensagem de sucesso para o usuário
                            success_message = (
                                "🎉 Pagamento confirmado com sucesso!\n\n"
                                "Seja bem-vindo(a) ao nosso grupo! Aqui está seu link de acesso exclusivo:\n\n"
                                f"{invite_link}\n\n"
                                "⚠️ **Atenção:** Este link é de uso único e não pode ser compartilhado."
                            )
                            send_message_url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
                            message_payload = {"chat_id": user_id, "text": success_message}
                            requests.post(send_message_url, json=message_payload)
                            logger.info(f"Acesso concedido com sucesso para o usuário {user_id}")
                        else:
                            logger.error("Falha ao extrair o link de convite da resposta da API do Telegram.")
                    else:
                        logger.error(f"Erro ao criar link de convite via API. Status: {link_response.status_code}, Resposta: {link_response.text}")
            else:
                logger.error(f"Falha ao buscar detalhes do pagamento {payment_id}. Status: {response.status_code}")

    return "OK", 200

# --- INICIALIZAÇÃO DO BOT (A PARTE CRÍTICA) ---
def run_bot_polling():
    global global_bot_app
    logger.info("Iniciando a aplicação do bot na sua própria thread...")

    # Cada thread precisa do seu próprio event loop. Esta é a correção crucial.
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    try:
        application = Application.builder().token(TELEGRAM_BOT_TOKEN).build()
        global_bot_app = application
        global_bot_app.loop = loop  # Garante que usamos o loop desta thread

        application.add_handler(CommandHandler("start", start))
        application.add_handler(CallbackQueryHandler(button_handler))

        logger.info("Polling do bot iniciado com sucesso!")
        application.run_polling(stop_signals=None)

    except Exception as e:
        logger.critical(f"ERRO FATAL NA THREAD DO BOT: {e}", exc_info=True)

# O Gunicorn vai importar este arquivo e executar o que está no escopo global.
# Nós iniciamos a thread do bot aqui para garantir que ela sempre rode.
logger.info("Iniciando a thread do bot em modo de produção...")
bot_thread = threading.Thread(target=run_bot_polling)
bot_thread.daemon = True
bot_thread.start()
