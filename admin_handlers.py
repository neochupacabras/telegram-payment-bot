# --- START OF FILE admin_handlers.py ---

import os
import logging
import asyncio
from functools import wraps

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    filters,
    ContextTypes,
    ConversationHandler,
)
from telegram.constants import ParseMode

import db_supabase as db
import scheduler  # Para usar a função de expulsão
from app import send_access_links, format_date_br # Importa funções do app.py

logger = logging.getLogger(__name__)

# --- Carrega IDs de Admin do .env ---
ADMIN_IDS_STR = os.getenv("ADMIN_USER_IDS", "")
ADMIN_IDS = [int(admin_id.strip()) for admin_id in ADMIN_IDS_STR.split(',')] if ADMIN_IDS_STR else []

# --- IDs dos produtos para os botões ---
PRODUCT_ID_LIFETIME = int(os.getenv("PRODUCT_ID_LIFETIME", 0))
PRODUCT_ID_MONTHLY = int(os.getenv("PRODUCT_ID_MONTHLY", 0))

# --- Estados da ConversationHandler ---
(
    SELECTING_ACTION,
    GETTING_USER_ID_FOR_CHECK,
    GETTING_USER_ID_FOR_GRANT,
    SELECTING_PLAN_FOR_GRANT,
    GETTING_USER_ID_FOR_REVOKE,
    CONFIRMING_REVOKE,
    GETTING_BROADCAST_MESSAGE,
    CONFIRMING_BROADCAST,
) = range(8)

# --- DECORATOR DE SEGURANÇA ---
def admin_only(func):
    """Restringe o uso de um handler apenas para admins."""
    @wraps(func)
    async def wrapped(update: Update, context: ContextTypes.DEFAULT_TYPE, *args, **kwargs):
        user_id = update.effective_user.id
        if user_id not in ADMIN_IDS:
            logger.warning(f"Acesso não autorizado ao painel admin pelo usuário {user_id}.")
            await update.message.reply_text("Você não tem permissão para usar este comando.")
            return
        return await func(update, context, *args, **kwargs)
    return wrapped

# --- FUNÇÕES DE ADMIN ---

@admin_only
async def admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Mostra o painel de administração principal."""
    keyboard = [
        [InlineKeyboardButton("📊 Checar Status de Usuário", callback_data="admin_check_user")],
        [InlineKeyboardButton("✅ Conceder Acesso Manual", callback_data="admin_grant_access")],
        [InlineKeyboardButton("❌ Revogar Acesso", callback_data="admin_revoke_access")],
        [InlineKeyboardButton("📢 Enviar Mensagem Global", callback_data="admin_broadcast")],
        [InlineKeyboardButton("✖️ Fechar Painel", callback_data="admin_cancel")],
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text("👑 *Painel de Administração*\n\nSelecione uma ação:", reply_markup=reply_markup, parse_mode=ParseMode.MARKDOWN)
    return SELECTING_ACTION

async def check_user_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Pede o ID ou @username do usuário para checar."""
    query = update.callback_query
    await query.answer()
    await query.edit_message_text(text="Por favor, envie o ID numérico ou o @username do usuário que deseja checar.")
    return GETTING_USER_ID_FOR_CHECK

async def check_user_receive_id(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Recebe o ID/username e mostra as informações."""
    identifier = update.message.text.strip()
    user_data = await db.find_user_by_id_or_username(identifier)

    if not user_data:
        await update.message.reply_text("Usuário não encontrado no banco de dados. Tente novamente ou cancele com /cancel.")
        return GETTING_USER_ID_FOR_CHECK

    # Formata a mensagem de status
    first_name = user_data.get('first_name', 'N/A')
    tg_id = user_data.get('telegram_user_id', 'N/A')
    username = f"@{user_data['username']}" if user_data.get('username') else 'N/A'

    message = (
        f"📊 *Status do Usuário*\n\n"
        f"👤 *Nome:* {first_name}\n"
        f"🆔 *Telegram ID:* `{tg_id}`\n"
        f"✒️ *Username:* {username}\n\n"
        "-------------------\n"
    )

    active_sub = next((s for s in user_data.get('subscriptions', []) if s['status'] == 'active'), None)

    if active_sub:
        product_name = active_sub.get('product', {}).get('name', 'N/A')
        start_date = format_date_br(active_sub.get('start_date'))
        end_date = "Vitalício" if not active_sub.get('end_date') else format_date_br(active_sub.get('end_date'))
        message += (
            f"✅ *Assinatura Ativa*\n"
            f"📦 *Plano:* {product_name}\n"
            f"📅 *Início:* {start_date}\n"
            f"🏁 *Fim:* {end_date}\n"
        )
    else:
        message += "❌ *Nenhuma assinatura ativa encontrada.*"

    await update.message.reply_text(message, parse_mode=ParseMode.MARKDOWN)
    await update.message.reply_text("Para checar outro usuário, envie um novo ID/username. Para voltar ao menu, use /admin.")
    return ConversationHandler.END


async def grant_access_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    await query.edit_message_text(text="Envie o ID numérico ou @username do usuário para conceder acesso.")
    return GETTING_USER_ID_FOR_GRANT

async def grant_access_receive_id(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    identifier = update.message.text.strip()
    user_data = await db.find_user_by_id_or_username(identifier)

    if not user_data:
        await update.message.reply_text("Usuário não encontrado. Peça para o usuário iniciar o bot primeiro com /start.")
        return ConversationHandler.END

    active_sub = next((s for s in user_data.get('subscriptions', []) if s['status'] == 'active'), None)
    if active_sub:
        await update.message.reply_text("⚠️ Este usuário já possui uma assinatura ativa! Revogue a assinatura atual antes de conceder uma nova.")
        return ConversationHandler.END

    context.user_data['grant_user_id'] = user_data['id']
    context.user_data['grant_telegram_user_id'] = user_data['telegram_user_id']

    keyboard = [
        [InlineKeyboardButton("Assinatura Mensal", callback_data=f"grant_plan_{PRODUCT_ID_MONTHLY}")],
        [InlineKeyboardButton("Acesso Vitalício", callback_data=f"grant_plan_{PRODUCT_ID_LIFETIME}")],
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text("Usuário encontrado. Qual plano deseja conceder?", reply_markup=reply_markup)
    return SELECTING_PLAN_FOR_GRANT

async def grant_access_select_plan(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    product_id = int(query.data.split('_')[-1])

    db_user_id = context.user_data.get('grant_user_id')
    telegram_user_id = context.user_data.get('grant_telegram_user_id')
    admin_id = update.effective_user.id

    await query.edit_message_text(text="Processando concessão...")

    # Cria a assinatura manual
    new_sub = await db.create_manual_subscription(db_user_id, product_id, f"manual_grant_by_admin_{admin_id}")

    if new_sub:
        # Envia os links de acesso para o usuário
        await send_access_links(telegram_user_id, new_sub['mp_payment_id'])
        await query.edit_message_text(text=f"✅ Acesso concedido com sucesso para o usuário {telegram_user_id}! Os links foram enviados.")

        # Tenta notificar o usuário
        try:
            await context.bot.send_message(telegram_user_id, "Boas notícias! Um administrador concedeu acesso a você. Seus links de convite estão acima.")
        except Exception:
            pass # Ignora se o usuário bloqueou o bot
    else:
        await query.edit_message_text(text="❌ Falha ao conceder acesso. Verifique os logs.")

    context.user_data.clear()
    return ConversationHandler.END

# ... (Funções para Revogar e Broadcast podem ser adicionadas aqui no mesmo padrão) ...

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Cancela a operação atual e limpa os dados de contexto."""
    query = update.callback_query
    if query:
        await query.answer()
        await query.edit_message_text(text="Operação cancelada. Painel fechado.")
    else:
        await update.message.reply_text("Operação cancelada.")

    context.user_data.clear()
    return ConversationHandler.END

def get_admin_conversation_handler() -> ConversationHandler:
    """Cria e retorna o ConversationHandler para o painel de admin."""
    return ConversationHandler(
        entry_points=[CommandHandler("admin", admin_panel)],
        states={
            SELECTING_ACTION: [
                CallbackQueryHandler(check_user_start, pattern="^admin_check_user$"),
                CallbackQueryHandler(grant_access_start, pattern="^admin_grant_access$"),
                # Adicionar handlers para revoke e broadcast aqui
                CallbackQueryHandler(cancel, pattern="^admin_cancel$"),
            ],
            GETTING_USER_ID_FOR_CHECK: [MessageHandler(filters.TEXT & ~filters.COMMAND, check_user_receive_id)],
            GETTING_USER_ID_FOR_GRANT: [MessageHandler(filters.TEXT & ~filters.COMMAND, grant_access_receive_id)],
            SELECTING_PLAN_FOR_GRANT: [CallbackQueryHandler(grant_access_select_plan, pattern="^grant_plan_")],
        },
        fallbacks=[CommandHandler("cancel", cancel), CommandHandler("admin", admin_panel)],
        per_user=True,
        per_chat=True,
    )
