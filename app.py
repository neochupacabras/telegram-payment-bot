# --- START OF FILE app.py (FINAL & ROBUST ARCHITECTURE + SUPABASE) ---

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
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ChatInviteLink
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes, JobQueue
from telegram.request import HTTPXRequest

# --- NOVO: Importações do Banco de Dados Supabase ---
import db_supabase as db

# --- CONFIGURAÇÃO DE LOGGING ---
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', stream=sys.stdout)
logger = logging.getLogger(__name__)

# --- CARREGAMENTO E VALIDAÇÃO DE VARIÁVEIS ---
load_dotenv()
# Adicione as variáveis do Supabase ao seu arquivo .env
# SUPABASE_URL="sua_url_aqui"
# SUPABASE_KEY="sua_chave_anon_aqui"

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_SECRET_TOKEN = os.getenv("TELEGRAM_SECRET_TOKEN")
MERCADO_PAGO_ACCESS_TOKEN = os.getenv("MERCADO_PAGO_ACCESS_TOKEN")
GROUP_CHAT_ID_STR = os.getenv("GROUP_CHAT_ID")
PAYMENT_AMOUNT_STR = os.getenv("PAYMENT_AMOUNT")
WEBHOOK_BASE_URL = os.getenv("WEBHOOK_BASE_URL")

# Validação (incluindo Supabase)
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")

if not all([TELEGRAM_BOT_TOKEN, TELEGRAM_SECRET_TOKEN, MERCADO_PAGO_ACCESS_TOKEN, GROUP_CHAT_ID_STR, PAYMENT_AMOUNT_STR, WEBHOOK_BASE_URL, SUPABASE_URL, SUPABASE_KEY]):
    logger.critical("ERRO: Variáveis de ambiente essenciais (incluindo Supabase) não configuradas.")
    sys.exit(1)
try:
    GROUP_CHAT_ID = int(GROUP_CHAT_ID_STR)
    PAYMENT_AMOUNT = float(PAYMENT_AMOUNT_STR)
except (ValueError, TypeError):
    logger.critical("ERRO CRÍTICO nos valores de ambiente.")
    sys.exit(1)

NOTIFICATION_URL = f"{WEBHOOK_BASE_URL}/webhook/mercadopago"
TELEGRAM_WEBHOOK_URL = f"{WEBHOOK_BASE_URL}/webhook/telegram"

# --- REMOVIDO: O banco de dados agora controla os pagamentos processados ---
# processed_payments = set()

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

# --- HANDLERS DO BOT (MODIFICADOS) ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    tg_user = update.effective_user

    # --- MODIFICADO: Interação com o banco de dados Supabase ---
    await db.get_or_create_user(tg_user)

    welcome_message = (f"Olá, {tg_user.first_name}!\n\nBem-vindo(a) ao bot de acesso ao nosso grupo exclusivo.\n\nO valor do acesso único é de R$ {PAYMENT_AMOUNT:.2f}.\n\nPara entrar, clique no botão abaixo e realize o pagamento via PIX.")
    keyboard = [[InlineKeyboardButton("✅ Quero Entrar (Pagar com PIX)", callback_data='generate_payment')]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text(welcome_message, reply_markup=reply_markup)

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    chat_id = query.message.chat_id
    tg_user = query.from_user

    if query.data == 'generate_payment':
        await query.edit_message_text(text="Gerando sua cobrança PIX, aguarde um instante...")

        # --- MODIFICADO: Usa a nova função do Supabase ---
        payment_data = await create_pix_payment(tg_user)

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

# --- FUNÇÕES DE PAGAMENTO (MODIFICADAS) ---
async def create_pix_payment(tg_user: "telegram.User") -> dict | None:
    url = "https://api.mercadopago.com/v1/payments"
    headers = { "Authorization": f"Bearer {MERCADO_PAGO_ACCESS_TOKEN}", "Content-Type": "application/json", "X-Idempotency-Key": str(uuid.uuid4()) }
    payload = { "transaction_amount": PAYMENT_AMOUNT, "description": f"Acesso ao grupo para {tg_user.first_name}", "payment_method_id": "pix", "payer": { "email": f"user_{tg_user.id}@telegram.bot" }, "notification_url": NOTIFICATION_URL, "external_reference": str(tg_user.id)}
    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(url, headers=headers, json=payload, timeout=10)
            response.raise_for_status()
        data = response.json()
        mp_payment_id = str(data.get('id'))

        # --- MODIFICADO: Salva a transação no Supabase ---
        db_user = await db.get_or_create_user(tg_user)
        if db_user and db_user.get('id'):
            await db.create_pending_transaction(db_user['id'], mp_payment_id, PAYMENT_AMOUNT)
        else:
            logger.error(f"Não foi possível obter/criar o usuário do DB para {tg_user.id}. A transação não foi registrada.")
            # Você pode decidir se quer retornar o erro ao usuário aqui

        return { 'qr_code_base64': data['point_of_interaction']['transaction_data']['qr_code_base64'], 'pix_copy_paste': data['point_of_interaction']['transaction_data']['qr_code'] }
    except httpx.HTTPError as e:
        logger.error(f"Erro HTTP ao criar pagamento no Mercado Pago: {e}")
        return None
    except Exception as e:
        logger.error(f"Erro inesperado ao criar pagamento ou transação: {e}", exc_info=True)
        return None

# --- send_access_link_job (sem alteração) ---
# ... (código idêntico) ...
async def send_access_link_job(context: ContextTypes.DEFAULT_TYPE):
    user_id = context.job.data['user_id']
    payment_id = context.job.data['payment_id']
    logger.info(f"[JOB][{payment_id}] Iniciando tarefa para enviar link ao usuário {user_id}.")

    def _now_epoch_utc():
        return int(datetime.now(timezone.utc).timestamp())

    EXPIRE_SECONDS = 60 * 60  # 1 hora
    MIN_BUFFER = 60 * 10      # +10 min para garantir janela útil
    expire_epoch = _now_epoch_utc() + EXPIRE_SECONDS + MIN_BUFFER

    async def _create_link_once(member_limit: int | None) -> "ChatInviteLink | None":
        try:
            logger.info(f"[JOB][{payment_id}] Gerando link (member_limit={member_limit}, expire_epoch={expire_epoch})...")
            link = await bot_app.bot.create_chat_invite_link(
                chat_id=GROUP_CHAT_ID,
                expire_date=expire_epoch,
                member_limit=member_limit
            )
            logger.info(f"[JOB][{payment_id}] Link criado: is_revoked={getattr(link, 'is_revoked', None)}, expire_date={getattr(link, 'expire_date', None)}")
            return link
        except Exception as e:
            logger.error(f"[JOB][{payment_id}] Erro ao criar link: {e}", exc_info=True)
            return None

    try:
        invite_link = await _create_link_once(member_limit=1)

        def _link_ok(l: ChatInviteLink | None) -> bool:
            if l is None: return False
            l_exp = getattr(l, 'expire_date', None)
            if isinstance(l_exp, datetime):
                l_exp = int(l_exp.timestamp())
            else:
                l_exp = expire_epoch

            not_revoked = not getattr(l, "is_revoked", False)
            in_future = l_exp > _now_epoch_utc() + 60
            return not_revoked and in_future

        if not _link_ok(invite_link):
            logger.warning(f"[JOB][{payment_id}] Link primário potencialmente inválido. Tentando recriar...")
            await asyncio.sleep(1) # Pequena pausa antes de recriar
            invite_link = await _create_link_once(member_limit=1)

        if not _link_ok(invite_link):
            logger.warning(f"[JOB][{payment_id}] Falha com member_limit=1. Fazendo fallback para link sem limite de uso.")
            invite_link = await _create_link_once(member_limit=None)

        if not _link_ok(invite_link):
            raise RuntimeError("Falha ao criar um link de convite utilizável após reintentos.")

        success_message = (
            "🎉 Pagamento confirmado!\n\n"
            "Seja bem-vindo(a)! Aqui está seu link de acesso exclusivo:\n\n"
            f"{invite_link.invite_link}\n\n"
            "⚠️ **Atenção:** Este link tem validade limitada. Use-o assim que possível."
        )
        await bot_app.bot.send_message(chat_id=user_id, text=success_message)
        logger.info(f"✅ [JOB][{payment_id}] Acesso concedido com sucesso para o usuário {user_id}")

    except Exception as e:
        logger.error(f"❌ [JOB][{payment_id}] Falha CRÍTICA ao enviar link: {e}", exc_info=True)
        try:
            await bot_app.bot.send_message(chat_id=user_id, text="⚠️ Tivemos um problema ao gerar seu link de acesso. Nossa equipe já foi notificada e entrará em contato.")
        except Exception:
            pass

# --- MODIFICADO: Processamento de pagamento agora usa o Supabase ---
async def process_approved_payment(payment_id: str):
    logger.info(f"[{payment_id}] Iniciando processamento do webhook.")

    # Verifica o status no nosso banco de dados primeiro
    current_status = await db.get_transaction_status(payment_id)

    if current_status == 'approved':
        logger.warning(f"[{payment_id}] Transação já está como 'aprovada' no banco. Ignorando notificação duplicada.")
        return

    if current_status is None:
        logger.warning(f"[{payment_id}] Transação não encontrada no banco. Pode ser de outro sistema. Ignorando.")
        return

    # Consulta os detalhes na API do MP para garantir que está realmente aprovado
    payment_details_url = f"https://api.mercadopago.com/v1/payments/{payment_id}"
    headers = {"Authorization": f"Bearer {MERCADO_PAGO_ACCESS_TOKEN}"}
    try:
        async with httpx.AsyncClient() as client:
            response = await client.get(payment_details_url, headers=headers)
            response.raise_for_status()
        payment_info = response.json()

        status = payment_info.get("status")
        external_reference = payment_info.get("external_reference")
        logger.info(f"[{payment_id}] Detalhes do MP: Status='{status}', UserID='{external_reference}'.")

        if status == "approved" and external_reference:
            user_id = int(external_reference)

            # Atualiza o status no nosso banco
            await db.update_transaction_status(payment_id, 'approved')

            # Agenda o job para enviar o link
            logger.info(f"[{payment_id}] Agendando job para enviar link ao usuário {user_id}.")
            bot_app.job_queue.run_once(send_access_link_job, when=0, data={'user_id': user_id, 'payment_id': payment_id})
        else:
             logger.warning(f"[{payment_id}] Pagamento não está 'approved' na API do MP (status: {status}). Nenhuma ação tomada.")
             if status and status != 'pending':
                 await db.update_transaction_status(payment_id, status) # ex: 'failed', 'cancelled'

    except httpx.HTTPError as e:
        logger.error(f"[{payment_id}] Erro HTTP ao consultar pagamento: {e}.")
    except Exception as e:
        logger.error(f"[{payment_id}] Erro inesperado ao processar pagamento: {e}.", exc_info=True)


# --- CICLO DE VIDA DO QUART ---
@app.before_serving
async def startup():
    # --- REMOVIDO: init_db() não é mais necessário com Supabase ---
    await bot_app.initialize()
    await bot_app.start()
    await bot_app.bot.set_webhook(url=TELEGRAM_WEBHOOK_URL, secret_token=TELEGRAM_SECRET_TOKEN)
    logger.info("Bot inicializado e webhook registrado.")

# ... (shutdown e rotas sem alteração) ...
@app.after_serving
async def shutdown():
    await bot_app.stop()
    await bot_app.shutdown()
    logger.info("Bot desligado.")

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
