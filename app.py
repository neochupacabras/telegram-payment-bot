import os
import logging
import requests
import json
import uuid
import base64
import io
import threading
import sys  # Importar a biblioteca sys
import asyncio

from flask import Flask, request
from dotenv import load_dotenv
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes
from logging.handlers import RotatingFileHandler

# --- CONFIGURAÇÃO DE LOGGING (Corrigida e no lugar certo) ---
log_formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
log_file = 'bot_activity.log'
my_handler = RotatingFileHandler(log_file, mode='a', maxBytes=5*1024*1024, backupCount=2, encoding=None, delay=0)
my_handler.setFormatter(log_formatter)
my_handler.setLevel(logging.INFO)

# Adiciona um handler para o console também, para vermos os logs no terminal
console_handler = logging.StreamHandler()
console_handler.setFormatter(log_formatter)
console_handler.setLevel(logging.INFO)

logger = logging.getLogger(__name__) # Usar __name__ é uma boa prática
logger.setLevel(logging.INFO)
logger.addHandler(my_handler)
logger.addHandler(console_handler)
logger.propagate = False # Evita logs duplicados no root logger

# --- VARIÁVEIS GLOBAIS ---
# Carrega as variáveis de ambiente do arquivo .env
load_dotenv()

# Carregamos como strings primeiro, a conversão será feita depois
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
MERCADO_PAGO_ACCESS_TOKEN = os.getenv("MERCADO_PAGO_ACCESS_TOKEN")
GROUP_CHAT_ID_STR = os.getenv("GROUP_CHAT_ID")
PAYMENT_AMOUNT_STR = os.getenv("PAYMENT_AMOUNT")
WEBHOOK_BASE_URL = os.getenv("WEBHOOK_BASE_URL")

# Variáveis que serão preenchidas na função main
GROUP_CHAT_ID = None
PAYMENT_AMOUNT = None
NOTIFICATION_URL = None

global_bot_app = None

# --- FUNÇÕES DO BOT (Nenhuma mudança aqui) ---
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

# --- LÓGICA DE PAGAMENTO (Nenhuma mudança aqui) ---
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
            "payment_id": data['id'],
            "qr_code_base64": data['point_of_interaction']['transaction_data']['qr_code_base64'],
            "pix_copy_paste": data['point_of_interaction']['transaction_data']['qr_code']
        }
    except requests.exceptions.RequestException as e:
        error_response_body = "Nenhum corpo de resposta."
        if e.response is not None:
            try: error_response_body = e.response.json()
            except json.JSONDecodeError: error_response_body = e.response.text
        logger.error(f"Erro ao criar pagamento no Mercado Pago: {e}")
        logger.error(f"Detalhes do erro da API: {error_response_body}")
        return None

# --- FUNÇÃO DE LIBERAÇÃO DE ACESSO (Nenhuma mudança aqui) ---
async def grant_access(user_id: int):
    """Gera um link de convite de uso único e o envia ao usuário."""
    try:
        # A linha problemática foi REMOVIDA.
        # Agora criamos um link de uso único sem data de expiração, que é mais seguro.
        link = await global_bot_app.bot.create_chat_invite_link(
            chat_id=GROUP_CHAT_ID,
            member_limit=1
        )
        success_message = (
            "🎉 Pagamento confirmado com sucesso!\n\n"
            "Seja bem-vindo(a) ao nosso grupo! Aqui está seu link de acesso exclusivo:\n\n"
            f"{link.invite_link}\n\n"
            "⚠️ **Atenção:** Este link é de uso único e não pode ser compartilhado."
        )
        await global_bot_app.bot.send_message(chat_id=user_id, text=success_message)
        logger.info(f"Acesso concedido para o usuário {user_id} com o link: {link.invite_link}")

    except Exception as e:
        logger.error(f"Erro ao conceder acesso para o usuário {user_id}: {e}", exc_info=True) # Adicionado exc_info=True para mais detalhes
        # Notificar o administrador ou o usuário sobre o erro
        await global_bot_app.bot.send_message(chat_id=user_id, text="Houve um problema ao gerar seu link de acesso. Por favor, entre em contato com o suporte.")

# --- SERVIDOR FLASK (Nenhuma mudança aqui) ---
flask_app = Flask(__name__)

@flask_app.route("/webhook/mercadopago", methods=['POST'])
def mercadopago_webhook():
    """Recebe as notificações de pagamento do Mercado Pago."""

    logger.info("--- NOVO WEBHOOK RECEBIDO ---")
    logger.info(f"Headers da requisição: {request.headers}")
    logger.info(f"Corpo (raw) da requisição: {request.get_data(as_text=True)}")

    data = None
    try:
        # Tenta pegar os dados como JSON primeiro (para webhooks reais)
        if request.is_json:
            data = request.get_json()
        else:
            # Se não for JSON, tenta pegar como form-data (para o botão de teste)
            # O botão de teste do MP envia os dados como form, mas o `id` está no `data[id]`
            # É uma estrutura um pouco estranha, então vamos simular a estrutura esperada
            if request.form.get('data[id]'):
                 data = {
                     "action": "payment.updated",
                     "data": {
                         "id": request.form.get('data[id]')
                     }
                 }
            else: # Se não for nenhum dos dois, tentamos ler o corpo como json de qualquer forma
                 data = json.loads(request.get_data(as_text=True))


    except Exception as e:
        logger.error(f"Erro ao processar o corpo da requisição do webhook: {e}")
        return "Bad Request: Could not parse body", 400

    if not data:
        logger.warning("Webhook recebido, mas sem dados válidos para processar.")
        return "Bad Request: No data", 400

    logger.info(f"Dados do webhook processados: {data}")

    if data and data.get("action") == "payment.updated":
        payment_id = data.get("data", {}).get("id")
        if payment_id:
            # O ID "123456" do teste não existe, então a consulta vai falhar. Isso é NORMAL.
            # O importante é que o webhook CHEGUE e seja processado até aqui.
            if payment_id == "123456":
                 logger.info("Webhook de teste do painel recebido e processado com sucesso!")
                 return "OK - Test Webhook Received", 200

            payment_details_url = f"https://api.mercadopago.com/v1/payments/{payment_id}"
            headers = {"Authorization": f"Bearer {MERCADO_PAGO_ACCESS_TOKEN}"}
            response = requests.get(payment_details_url, headers=headers)

            if response.status_code == 200:
                payment_info = response.json()
                if payment_info.get("status") == "approved" and payment_info.get("external_reference"):
                    user_id = int(payment_info["external_reference"])
                    logger.info(f"Pagamento aprovado para o usuário {user_id}")
                    loop = global_bot_app.loop
                    asyncio.run_coroutine_threadsafe(grant_access(user_id), loop)
            else:
                logger.error(f"Falha ao buscar detalhes do pagamento {payment_id}. Status: {response.status_code}, Resposta: {response.text}")

    return "OK", 200

def run_flask():
    flask_app.run(host='0.0.0.0', port=5001, use_reloader=False)

# --- FUNÇÃO PRINCIPAL (TOTALMENTE REFEITA E ROBUSTA) ---
def main() -> None:
    global global_bot_app, GROUP_CHAT_ID, PAYMENT_AMOUNT, NOTIFICATION_URL

    logger.info("Iniciando o bot de pagamentos...")

    # VALIDAÇÃO DAS VARIÁVEIS DE AMBIENTE
    required_vars = {
        "TELEGRAM_BOT_TOKEN": TELEGRAM_BOT_TOKEN,
        "MERCADO_PAGO_ACCESS_TOKEN": MERCADO_PAGO_ACCESS_TOKEN,
        "GROUP_CHAT_ID": GROUP_CHAT_ID_STR,
        "PAYMENT_AMOUNT": PAYMENT_AMOUNT_STR,
        "WEBHOOK_BASE_URL": WEBHOOK_BASE_URL
    }

    for var_name, var_value in required_vars.items():
        if not var_value:
            logger.critical(f"ERRO CRÍTICO: A variável de ambiente '{var_name}' não foi definida. O bot não pode iniciar.")
            sys.exit(1) # Encerra o programa com código de erro

    try:
        GROUP_CHAT_ID = int(GROUP_CHAT_ID_STR)
        PAYMENT_AMOUNT = float(PAYMENT_AMOUNT_STR)
    except (ValueError, TypeError):
        logger.critical("ERRO CRÍTICO: GROUP_CHAT_ID ou PAYMENT_AMOUNT não são números válidos.")
        sys.exit(1)

    NOTIFICATION_URL = f"{WEBHOOK_BASE_URL}/webhook/mercadopago"

    # CRIAÇÃO DA APLICAÇÃO DO BOT
    application = Application.builder().token(TELEGRAM_BOT_TOKEN).build()
    global_bot_app = application
    global_bot_app.loop = asyncio.get_event_loop()

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CallbackQueryHandler(button_handler))

    # NÃO VAMOS MAIS INICIAR O FLASK EM UMA THREAD AQUI
    # O GUNICORN VAI CUIDAR DO FLASK

    # INICIA O BOT DO TELEGRAM
    logger.info("Iniciando o polling do bot do Telegram...")
    application.run_polling()

# O Gunicorn precisa da variável 'flask_app', então vamos garantir que ela é chamada de 'app'
app = flask_app

if __name__ == '__main__':
    # Quando rodamos localmente, iniciamos o bot diretamente
    main()
