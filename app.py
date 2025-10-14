# --- START OF FILE app.py (ARQUITETURA DE ASSINATURAS) ---

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
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ChatInviteLink, User as TelegramUser, BotCommand
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, MessageHandler, filters, ContextTypes, JobQueue
from telegram.constants import ParseMode
from telegram.error import BadRequest, Forbidden
from telegram.request import HTTPXRequest

import db_supabase as db
import scheduler # Importa nosso novo arquivo

# --- CONFIGURAÇÃO DE LOGGING ---
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', stream=sys.stdout)
logger = logging.getLogger(__name__)

# --- CARREGAMENTO E VALIDAÇÃO DE VARIÁVEIS ---
load_dotenv()

# Variáveis do Telegram e Mercado Pago
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_SECRET_TOKEN = os.getenv("TELEGRAM_SECRET_TOKEN")
MERCADO_PAGO_ACCESS_TOKEN = os.getenv("MERCADO_PAGO_ACCESS_TOKEN")
WEBHOOK_BASE_URL = os.getenv("WEBHOOK_BASE_URL")

# Variáveis do Supabase
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")

# --- NOVO: Variáveis de configuração de produtos e grupos ---
GROUP_CHAT_IDS_STR = os.getenv("GROUP_CHAT_IDS")
PRODUCT_ID_LIFETIME = int(os.getenv("PRODUCT_ID_LIFETIME", 0))
PRODUCT_ID_MONTHLY = int(os.getenv("PRODUCT_ID_MONTHLY", 0))

if not all([TELEGRAM_BOT_TOKEN, TELEGRAM_SECRET_TOKEN, MERCADO_PAGO_ACCESS_TOKEN, WEBHOOK_BASE_URL, SUPABASE_URL, SUPABASE_KEY, GROUP_CHAT_IDS_STR, PRODUCT_ID_LIFETIME, PRODUCT_ID_MONTHLY]):
    logger.critical("ERRO: Variáveis de ambiente essenciais não configuradas.")
    sys.exit(1)

try:
    # Converte a string de IDs em uma lista de inteiros
    GROUP_CHAT_IDS = [int(gid.strip()) for gid in GROUP_CHAT_IDS_STR.split(',')]
except (ValueError, TypeError):
    logger.critical("ERRO CRÍTICO no formato de GROUP_CHAT_IDS.")
    sys.exit(1)

NOTIFICATION_URL = f"{WEBHOOK_BASE_URL}/webhook/mercadopago"
TELEGRAM_WEBHOOK_URL = f"{WEBHOOK_BASE_URL}/webhook/telegram"
TIMEZONE_BR = timezone(timedelta(hours=-3))

# --- INICIALIZAÇÃO DO BOT ---
request_config = {'connect_timeout': 10.0, 'read_timeout': 20.0}
httpx_request = HTTPXRequest(**request_config)
bot_app = Application.builder().token(TELEGRAM_BOT_TOKEN).request(httpx_request).job_queue(JobQueue()).build()
app = Quart(__name__)

# --- FUNÇÕES AUXILIARES ---
def format_date_br(dt: datetime | str | None) -> str:
    """Formata data para o padrão brasileiro."""
    if not dt:
        return "N/A"
    if isinstance(dt, str):
        dt = datetime.fromisoformat(dt)
    return dt.astimezone(TIMEZONE_BR).strftime('%d/%m/%Y às %H:%M')

# --- HANDLERS DE COMANDOS DO USUÁRIO ---

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handler do comando /start. Mostra as opções de pagamento."""
    tg_user = update.effective_user
    await db.get_or_create_user(tg_user)

    # Busca os preços dos produtos no banco de dados
    product_monthly = await db.get_product_by_id(PRODUCT_ID_MONTHLY)
    product_lifetime = await db.get_product_by_id(PRODUCT_ID_LIFETIME)

    if not product_monthly or not product_lifetime:
        await update.message.reply_text("Desculpe, estamos com um problema em nossos sistemas. Tente novamente mais tarde.")
        logger.error("Não foi possível carregar os produtos do banco de dados.")
        return

    welcome_message = f"Olá, {tg_user.first_name}!\n\nBem-vindo(a) ao bot de acesso aos nossos grupos exclusivos.\n\nEscolha seu plano de acesso:"
    keyboard = [
        [InlineKeyboardButton(f"✅ Assinatura Mensal (R$ {product_monthly['price']:.2f})", callback_data=f'pay_{PRODUCT_ID_MONTHLY}')],
        [InlineKeyboardButton(f"💎 Acesso Vitalício (R$ {product_lifetime['price']:.2f})", callback_data=f'pay_{PRODUCT_ID_LIFETIME}')]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text(welcome_message, reply_markup=reply_markup)


async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handler do comando /status. Mostra o status da assinatura."""
    tg_user = update.effective_user
    subscription = await db.get_user_active_subscription(tg_user.id)

    if subscription and subscription.get('status') == 'active':
        product_name = subscription.get('product', {}).get('name', 'N/A')
        start_date_br = format_date_br(subscription.get('start_date'))

        if subscription.get('end_date'): # Assinatura com data de fim
            end_date_br = format_date_br(subscription.get('end_date'))
            message = (
                "📄 **Status da sua Assinatura**\n\n"
                f"**Plano:** {product_name}\n"
                f"**Status:** Ativa ✅\n"
                f"**Início:** {start_date_br}\n"
                f"**Vencimento:** {end_date_br}\n\n"
                "Você tem acesso a todos os nossos grupos. Para renovar, use o comando /renovar."
            )
        else: # Acesso vitalício
            message = (
                "📄 **Status do seu Acesso**\n\n"
                f"**Plano:** {product_name}\n"
                f"**Status:** Ativo ✅\n"
                f"**Data de Início:** {start_date_br}\n\n"
                "Seu acesso é vitalício e não expira!"
            )
    else:
        message = "Você não possui uma assinatura ativa no momento. Use o comando /start para ver as opções."

    await update.message.reply_text(message, parse_mode=ParseMode.MARKDOWN)


async def renew_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handler do comando /renovar."""
    # Este comando basicamente redireciona para o fluxo de pagamento mensal
    product_monthly = await db.get_product_by_id(PRODUCT_ID_MONTHLY)
    if not product_monthly:
        await update.message.reply_text("Erro ao buscar informações de renovação. Tente mais tarde.")
        return

    message = f"Para renovar sua assinatura mensal por mais 30 dias, o valor é de R$ {product_monthly['price']:.2f}.\n\nClique no botão abaixo para gerar o pagamento PIX."
    keyboard = [[InlineKeyboardButton(f"Pagar Renovação (R$ {product_monthly['price']:.2f})", callback_data=f'pay_{PRODUCT_ID_MONTHLY}')]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text(message, reply_markup=reply_markup)


async def support_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handler do comando /suporte."""
    message = (
        "Selecione uma opção de suporte:\n\n"
        "🔗 **Reenviar Links:** Se você já pagou e perdeu os links de acesso.\n"
        "💰 **Problema no Pagamento:** Se precisa de ajuda com um pagamento."
    )
    keyboard = [
        [InlineKeyboardButton("🔗 Reenviar Links de Acesso", callback_data='support_resend_links')],
        [InlineKeyboardButton("💰 Ajuda com Pagamento", callback_data='support_payment_help')]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text(message, reply_markup=reply_markup, parse_mode=ParseMode.MARKDOWN)


# --- HANDLER DE BOTÕES (CALLBACKQUERY) ---

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Processa todos os cliques em botões."""
    query = update.callback_query
    await query.answer()
    tg_user = query.from_user
    chat_id = query.message.chat_id
    data = query.data

    # Fluxo de Pagamento
    if data.startswith('pay_'):
        product_id = int(data.split('_')[1])
        product = await db.get_product_by_id(product_id)
        if not product:
            await query.edit_message_text(text="Desculpe, este produto não está mais disponível.")
            return

        await query.edit_message_text(text=f"Gerando sua cobrança PIX para o plano '{product['name']}', aguarde...")
        payment_data = await create_pix_payment(tg_user, product)

        if payment_data:
            qr_code_image = base64.b64decode(payment_data['qr_code_base64'])
            image_stream = io.BytesIO(qr_code_image)
            await context.bot.send_photo(chat_id=chat_id, photo=image_stream, caption="Use o QR Code acima ou o código abaixo para pagar.")
            await context.bot.send_message(chat_id=chat_id, text=f"PIX Copia e Cola:\n\n`{payment_data['pix_copy_paste']}`", parse_mode=ParseMode.MARKDOWN_V2)
            await context.bot.send_message(chat_id=chat_id, text="Assim que o pagamento for confirmado, você receberá o(s) link(s) de acesso automaticamente!")
        else:
            await query.edit_message_text(text="Desculpe, ocorreu um erro ao gerar sua cobrança. Tente novamente mais tarde ou use /suporte.")

    # Fluxo de Suporte
    elif data == 'support_resend_links':
        await query.edit_message_text("Verificando sua assinatura, um momento...")
        subscription = await db.get_user_active_subscription(tg_user.id)
        if subscription and subscription.get('status') == 'active':
            await query.edit_message_text("Encontramos sua assinatura ativa! Reenviando seus links de acesso...")
            await send_access_links(tg_user.id, subscription['mp_payment_id']) # Chama a função que envia os links
        else:
            await query.edit_message_text("Não encontrei uma assinatura ativa para você. Se você já pagou, use a opção 'Ajuda com Pagamento' ou aguarde alguns minutos pela confirmação.")

    elif data == 'support_payment_help':
        # Escapamos os pontos no nome de usuário e usamos a sintaxe correta do V2
        # Você deve substituir 'SUA_CHAVE_PIX_AQUI' e '@seu_usuario_de_suporte' pelos seus dados reais.
        chave_pix = "234caf84-775c-4649-aaf1-ab7d928ef315" # Coloque sua chave aqui
        usuario_suporte = "@sirigueijo" # Coloque seu @ aqui. Ex: @joao_suporte

        # Precisamos escapar caracteres especiais no nome de usuário para MarkdownV2
        usuario_suporte_escapado = usuario_suporte.replace("_", "\\_")

        texto = (
            "Se o pagamento automático falhou, você pode tentar pagar manualmente para a chave PIX:\n\n"
            f"`{chave_pix}`\n\n" # Código monoespaçado (copia e cola)
            f"*IMPORTANTE:* Após o pagamento manual, envie o comprovante para {usuario_suporte_escapado} para liberação\\."
        )

        await query.edit_message_text(
            text=texto,
            parse_mode=ParseMode.MARKDOWN_V2 # Usamos a versão V2
        )
# --- LÓGICA DE PAGAMENTO E ACESSO ---

async def create_pix_payment(tg_user: TelegramUser, product: dict) -> dict | None:
    """Cria uma cobrança PIX no Mercado Pago e uma assinatura pendente no DB."""
    url = "https://api.mercadopago.com/v1/payments"
    headers = { "Authorization": f"Bearer {MERCADO_PAGO_ACCESS_TOKEN}", "Content-Type": "application/json", "X-Idempotency-Key": str(uuid.uuid4()) }
    # Adicionamos o product_id na referência externa para saber o que foi comprado
    external_ref = f"user:{tg_user.id};product:{product['id']}"
    payload = {
        "transaction_amount": float(product['price']),
        "description": f"Acesso '{product['name']}' para {tg_user.first_name}",
        "payment_method_id": "pix",
        "payer": { "email": f"user_{tg_user.id}@telegram.bot" },
        "notification_url": NOTIFICATION_URL,
        "external_reference": external_ref
    }
    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(url, headers=headers, json=payload, timeout=10)
            response.raise_for_status()
        data = response.json()
        mp_payment_id = str(data.get('id'))

        db_user = await db.get_or_create_user(tg_user)
        if db_user and db_user.get('id'):
            await db.create_pending_subscription(db_user['id'], product['id'], mp_payment_id)
        else:
            logger.error(f"Não foi possível obter/criar o usuário do DB para {tg_user.id}. A transação não foi registrada.")
            return None

        return { 'qr_code_base64': data['point_of_interaction']['transaction_data']['qr_code_base64'], 'pix_copy_paste': data['point_of_interaction']['transaction_data']['qr_code'] }
    except httpx.HTTPError as e:
        logger.error(f"Erro HTTP ao criar pagamento no Mercado Pago: {e}")
        return None
    except Exception as e:
        logger.error(f"Erro inesperado ao criar pagamento ou transação: {e}", exc_info=True)
        return None


async def send_access_links(user_id: int, payment_id: str):
    """Gera e envia os links de acesso para TODOS os grupos configurados."""
    logger.info(f"[JOB][{payment_id}] Iniciando tarefa para enviar links ao usuário {user_id}.")

    # Busca os IDs de todos os grupos do banco de dados
    group_ids = await db.get_all_group_ids()
    if not group_ids:
        logger.error(f"CRÍTICO: Nenhum grupo encontrado no banco de dados para enviar links ao usuário {user_id}.")
        await bot_app.bot.send_message(chat_id=user_id, text="⚠️ Tivemos um problema interno para buscar os grupos. Nossa equipe foi notificada.")
        return

    links_text = ""
    failed_links = 0
    expire_date = datetime.now(timezone.utc) + timedelta(hours=2) # Link válido por 2 horas

    for chat_id in group_ids:
        try:
            # Cria um link de convite de uso único para cada grupo
            link = await bot_app.bot.create_chat_invite_link(
                chat_id=chat_id,
                expire_date=expire_date,
                member_limit=1
            )
            links_text += f"🔗 Link para Grupo {group_ids.index(chat_id) + 1}: {link.invite_link}\n"
            await asyncio.sleep(0.2) # Evita rate limiting
        except Exception as e:
            logger.error(f"[JOB][{payment_id}] Erro ao criar link para o grupo {chat_id}: {e}")
            links_text += f"❌ Falha ao gerar o link para o Grupo {group_ids.index(chat_id) + 1}. Contate o /suporte.\n"
            failed_links += 1

    success_message = (
        "🎉 Pagamento confirmado!\n\n"
        "Seja bem-vindo(a)! Aqui estão seus links de acesso exclusivos para nossos grupos:\n\n"
        f"{links_text}\n"
        "⚠️ **Atenção:** Cada link só pode ser usado **uma vez** e expira em breve. Entre em todos os grupos agora."
    )
    await bot_app.bot.send_message(chat_id=user_id, text=success_message)

    if failed_links == 0:
        logger.info(f"✅ [JOB][{payment_id}] Todos os {len(group_ids)} links de acesso foram enviados com sucesso para o usuário {user_id}")
    else:
         logger.warning(f"⚠️ [JOB][{payment_id}] Foram enviados links para o usuário {user_id}, mas {failed_links} falharam ao ser gerados.")


async def process_approved_payment(payment_id: str):
    """Processa um pagamento aprovado, ativa a assinatura e agenda o envio dos links."""
    logger.info(f"[{payment_id}] Iniciando processamento de pagamento aprovado.")

    # Ativa a assinatura no banco de dados. Esta função retorna os dados da assinatura se for bem sucedida.
    activated_subscription = await db.activate_subscription(payment_id)

    if activated_subscription:
        # A função `activate_subscription` já retorna o telegram_user_id
        telegram_user_id = activated_subscription.get('user', {}).get('telegram_user_id')

        if telegram_user_id:
            logger.info(f"[{payment_id}] Assinatura ativada. Agendando envio de links para o usuário {telegram_user_id}.")
            # Usamos create_task para não bloquear o webhook
            asyncio.create_task(send_access_links(telegram_user_id, payment_id))
        else:
            logger.error(f"[{payment_id}] CRÍTICO: Assinatura ativada, mas não foi possível encontrar o telegram_user_id associado.")
    else:
        logger.warning(f"[{payment_id}] A ativação da assinatura falhou ou já estava ativa. Nenhuma ação de envio de link será tomada.")

# --- WEBHOOKS E CICLO DE VIDA ---
bot_app.add_handler(CommandHandler("start", start))
bot_app.add_handler(CommandHandler("status", status_command))
bot_app.add_handler(CommandHandler("renovar", renew_command))
bot_app.add_handler(CommandHandler("suporte", support_command))
bot_app.add_handler(CallbackQueryHandler(button_handler))

# --- ROTA PARA EXECUTAR O SCHEDULER EXTERNAMENTE ---
# Pega o token secreto das variáveis de ambiente
SCHEDULER_SECRET_TOKEN = os.getenv("SCHEDULER_SECRET_TOKEN")

@app.route("/webhook/run-scheduler", methods=['POST'])
async def run_scheduler_webhook():
    # Medida de segurança: verifica se um token secreto foi enviado no cabeçalho
    auth_token = request.headers.get("Authorization")
    if not SCHEDULER_SECRET_TOKEN or auth_token != f"Bearer {SCHEDULER_SECRET_TOKEN}":
        logger.warning("Tentativa de acesso não autorizado ao webhook do scheduler.")
        abort(403) # Forbidden

    logger.info("Webhook do scheduler acionado. Executando tarefas agendadas...")
    # Executa a função principal do nosso arquivo scheduler.py
    # Usamos create_task para que a resposta ao webhook seja imediata
    asyncio.create_task(scheduler.main())

    return "Scheduler tasks triggered.", 200


@app.before_serving
async def startup():
    await bot_app.initialize()
    await bot_app.start()

    # --- NOVO CÓDIGO AQUI ---
    # Define a lista de comandos que aparecerão no menu
    commands = [
        BotCommand("start", "▶️ Inicia o bot e mostra os planos"),
        BotCommand("status", "📄 Verifica o status da sua assinatura"),
        BotCommand("renovar", "🔄 Pagar para renovar assinatura"),
        BotCommand("suporte", "❓ Ajuda com pagamentos ou links de acesso"),
    ]
    # Envia a lista de comandos para o Telegram
    await bot_app.bot.set_my_commands(commands)
    logger.info("Comandos do menu registrados com sucesso.")
    # --- FIM DO NOVO CÓDIGO ---

    await bot_app.bot.set_webhook(url=TELEGRAM_WEBHOOK_URL, secret_token=TELEGRAM_SECRET_TOKEN)
    logger.info("Bot inicializado e webhook registrado com sucesso.")

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
    logger.info(f"Webhook do MP recebido: {json.dumps(data)}")

    if data and data.get("action") == "payment.updated":
        payment_id = data.get("data", {}).get("id")
        if payment_id:
            # Apenas processamos pagamentos que estão REALMENTE aprovados
            # Consultamos a API do MP para ter certeza
            try:
                async with httpx.AsyncClient() as client:
                    headers = {"Authorization": f"Bearer {MERCADO_PAGO_ACCESS_TOKEN}"}
                    response = await client.get(f"https://api.mercadopago.com/v1/payments/{payment_id}", headers=headers)
                    payment_info = response.json()

                if response.status_code == 200 and payment_info.get("status") == "approved":
                    logger.info(f"Pagamento {payment_id} confirmado como 'approved'. Agendando processamento.")
                    asyncio.create_task(process_approved_payment(str(payment_id)))
                else:
                    logger.info(f"Notificação para pagamento {payment_id} recebida, mas status não é 'approved' (Status: {payment_info.get('status')}). Ignorando.")

            except Exception as e:
                logger.error(f"Erro ao verificar status do pagamento {payment_id} na API do MP: {e}")

    return "OK", 200
