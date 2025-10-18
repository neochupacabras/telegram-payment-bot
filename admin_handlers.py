# --- START OF FILE admin_handlers.py (VERSÃO FINAL COM TODAS AS FUNÇÕES + NOVO GRUPO) ---

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
from telegram.error import BadRequest, Forbidden, RetryAfter # Importar RetryAfter

import db_supabase as db
import scheduler
from utils import send_access_links, format_date_br

logger = logging.getLogger(__name__)

# --- Carrega IDs de Admin do .env ---
ADMIN_IDS_STR = os.getenv("ADMIN_USER_IDS", "")
ADMIN_IDS = [int(admin_id.strip()) for admin_id in ADMIN_IDS_STR.split(',')] if ADMIN_IDS_STR else []

# --- IDs dos produtos para os botões ---
PRODUCT_ID_LIFETIME = int(os.getenv("PRODUCT_ID_LIFETIME", 0))
PRODUCT_ID_MONTHLY = int(os.getenv("PRODUCT_ID_MONTHLY", 0))

# --- Estados da ConversationHandler (MAIS ESTADOS ADICIONADOS) ---
(
    SELECTING_ACTION,
    GETTING_USER_ID_FOR_CHECK,
    GETTING_USER_ID_FOR_GRANT,
    SELECTING_PLAN_FOR_GRANT,
    GETTING_USER_ID_FOR_REVOKE,
    CONFIRMING_REVOKE,
    GETTING_BROADCAST_MESSAGE,
    CONFIRMING_BROADCAST,
    ### NOVO ###
    SELECTING_NEW_GROUP,
    CONFIRMING_NEW_GROUP_BROADCAST,
    ### FIM NOVO ###
) = range(10) # <-- ATUALIZAR O NÚMERO TOTAL DE ESTADOS

# --- DECORATOR DE SEGURANÇA (sem alteração) ---
def admin_only(func):
    @wraps(func)
    async def wrapped(update: Update, context: ContextTypes.DEFAULT_TYPE, *args, **kwargs):
        user_id = update.effective_user.id
        if user_id not in ADMIN_IDS:
            logger.warning(f"Acesso não autorizado ao painel admin pelo usuário {user_id}.")
            if update.message:
                await update.message.reply_text("Você não tem permissão para usar este comando.")
            return ConversationHandler.END # Encerra a conversa se não for admin
        return await func(update, context, *args, **kwargs)
    return wrapped

# --- FUNÇÃO AUXILIAR PARA O MENU PRINCIPAL ---
async def show_main_admin_menu(update: Update, context: ContextTypes.DEFAULT_TYPE, is_edit: bool = False):
    """Mostra o painel de administração principal."""
    keyboard = [
        [InlineKeyboardButton("📊 Checar Status de Usuário", callback_data="admin_check_user")],
        [InlineKeyboardButton("✅ Conceder Acesso Manual", callback_data="admin_grant_access")],
        [InlineKeyboardButton("❌ Revogar Acesso", callback_data="admin_revoke_access")],
        [InlineKeyboardButton("📢 Enviar Mensagem Global", callback_data="admin_broadcast")],
        ### NOVO ###
        [InlineKeyboardButton("✉️ Enviar Link de Novo Grupo", callback_data="admin_grant_new_group")],
        ### FIM NOVO ###
        [InlineKeyboardButton("✖️ Fechar Painel", callback_data="admin_cancel")],
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    text = "👑 *Painel de Administração*\n\nSelecione uma ação:"

    if is_edit and update.callback_query:
        await update.callback_query.edit_message_text(text, reply_markup=reply_markup, parse_mode=ParseMode.MARKDOWN)
    elif update.message:
        await update.message.reply_text(text, reply_markup=reply_markup, parse_mode=ParseMode.MARKDOWN)

# --- HANDLERS PRINCIPAIS ---

@admin_only
async def admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Ponto de entrada para o /admin."""
    await show_main_admin_menu(update, context)
    return SELECTING_ACTION

@admin_only
async def back_to_main_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Callback para o botão Voltar."""
    query = update.callback_query
    await query.answer()
    await show_main_admin_menu(update, context, is_edit=True)
    return SELECTING_ACTION

# --- FLUXO: CHECAR USUÁRIO (sem alteração) ---
@admin_only
async def check_user_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    keyboard = [[InlineKeyboardButton("⬅️ Voltar", callback_data="admin_back_to_menu")]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await query.edit_message_text(text="Por favor, envie o ID numérico ou o @username do usuário que deseja checar.", reply_markup=reply_markup)
    return GETTING_USER_ID_FOR_CHECK

@admin_only
async def check_user_receive_id(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    identifier = update.message.text.strip()
    user_data = await db.find_user_by_id_or_username(identifier)
    if not user_data:
        await update.message.reply_text("Usuário não encontrado. Tente novamente ou cancele com /cancel.")
        return GETTING_USER_ID_FOR_CHECK
    first_name = user_data.get('first_name', 'N/A')
    tg_id = user_data.get('telegram_user_id', 'N/A')
    username = f"@{user_data['username']}" if user_data.get('username') else 'N/A'
    message = (f"📊 *Status do Usuário*\n\n" f"👤 *Nome:* {first_name}\n" f"🆔 *Telegram ID:* `{tg_id}`\n" f"✒️ *Username:* {username}\n\n" "-------------------\n")
    active_sub = next((s for s in user_data.get('subscriptions', []) if s['status'] == 'active'), None)
    if active_sub:
        product_name = active_sub.get('product', {}).get('name', 'N/A')
        start_date = format_date_br(active_sub.get('start_date'))
        end_date = "Vitalício" if not active_sub.get('end_date') else format_date_br(active_sub.get('end_date'))
        message += (f"✅ *Assinatura Ativa*\n" f"📦 *Plano:* {product_name}\n" f"📅 *Início:* {start_date}\n" f"🏁 *Fim:* {end_date}\n")
    else:
        message += "❌ *Nenhuma assinatura ativa encontrada.*"
    await update.message.reply_text(message, parse_mode=ParseMode.MARKDOWN)
    await update.message.reply_text("Para checar outro usuário, envie um novo ID/username. Para voltar ao menu, use /admin.")
    return ConversationHandler.END

# --- FLUXO: CONCEDER ACESSO (sem alteração) ---
@admin_only
async def grant_access_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    keyboard = [[InlineKeyboardButton("⬅️ Voltar", callback_data="admin_back_to_menu")]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await query.edit_message_text(text="Envie o ID numérico ou @username do usuário para conceder acesso.", reply_markup=reply_markup)
    return GETTING_USER_ID_FOR_GRANT

@admin_only
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
        [InlineKeyboardButton("⬅️ Voltar", callback_data="admin_back_to_menu")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text("Usuário encontrado. Qual plano deseja conceder?", reply_markup=reply_markup)
    return SELECTING_PLAN_FOR_GRANT

@admin_only
async def grant_access_select_plan(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    product_id = int(query.data.split('_')[-1])
    db_user_id = context.user_data.get('grant_user_id')
    telegram_user_id = context.user_data.get('grant_telegram_user_id')
    admin_id = update.effective_user.id
    await query.edit_message_text(text="Processando concessão...")
    new_sub = await db.create_manual_subscription(db_user_id, product_id, f"manual_grant_by_admin_{admin_id}")
    if new_sub:
        await send_access_links(context.bot, telegram_user_id, new_sub.get('mp_payment_id', 'manual'))
        await query.edit_message_text(text=f"✅ Acesso concedido com sucesso para o usuário {telegram_user_id}! Os links foram enviados.")
        try:
            await context.bot.send_message(telegram_user_id, "Boas notícias! Um administrador concedeu acesso a você. Seus links de convite estão acima.")
        except Exception:
            pass
    else:
        await query.edit_message_text(text="❌ Falha ao conceder acesso. Verifique os logs.")
    context.user_data.clear()
    return ConversationHandler.END

# --- FLUXO: REVOGAR ACESSO (sem alteração) ---
@admin_only
async def revoke_access_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    keyboard = [[InlineKeyboardButton("⬅️ Voltar", callback_data="admin_back_to_menu")]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await query.edit_message_text(text="Envie o ID numérico ou @username do usuário que terá o acesso revogado.", reply_markup=reply_markup)
    return GETTING_USER_ID_FOR_REVOKE

@admin_only
async def revoke_access_receive_id(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    identifier = update.message.text.strip()
    user_data = await db.find_user_by_id_or_username(identifier)
    if not user_data:
        await update.message.reply_text("Usuário não encontrado. Tente novamente.")
        return GETTING_USER_ID_FOR_REVOKE
    active_sub = next((s for s in user_data.get('subscriptions', []) if s['status'] == 'active'), None)
    if not active_sub:
        await update.message.reply_text("Este usuário não possui uma assinatura ativa para revogar.")
        return ConversationHandler.END
    context.user_data['revoke_db_user_id'] = user_data['id']
    context.user_data['revoke_telegram_user_id'] = user_data['telegram_user_id']
    keyboard = [
        [InlineKeyboardButton("✅ SIM, REVOGAR AGORA", callback_data="revoke_confirm")],
        [InlineKeyboardButton("❌ NÃO, CANCELAR", callback_data="admin_back_to_menu")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text(f"⚠️ ATENÇÃO ⚠️\n\nVocê está prestes a revogar o acesso de {user_data['first_name']} (`{user_data['telegram_user_id']}`) e removê-lo(a) de todos os grupos. Confirma?", reply_markup=reply_markup, parse_mode=ParseMode.MARKDOWN)
    return CONFIRMING_REVOKE

@admin_only
async def revoke_access_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    await query.edit_message_text("Processando revogação...")
    db_user_id = context.user_data.get('revoke_db_user_id')
    telegram_user_id = context.user_data.get('revoke_telegram_user_id')
    admin_id = update.effective_user.id
    success = await db.revoke_subscription(db_user_id, f"revoked_by_admin_{admin_id}")
    if success:
        removed_count = await scheduler.kick_user_from_all_groups(telegram_user_id, context.bot)
        await query.edit_message_text(f"✅ Acesso revogado com sucesso. O usuário foi removido de {removed_count} grupos.")
        try:
            await context.bot.send_message(telegram_user_id, "Seu acesso foi revogado por um administrador.")
        except Exception:
            pass
    else:
        await query.edit_message_text("❌ Falha ao revogar o acesso no banco de dados.")
    context.user_data.clear()
    return ConversationHandler.END

# --- FLUXO: BROADCAST (sem alteração) ---
@admin_only
async def broadcast_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    keyboard = [[InlineKeyboardButton("⬅️ Voltar", callback_data="admin_back_to_menu")]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await query.edit_message_text(text="Envie a mensagem que você deseja enviar a todos os usuários com assinatura ativa. Use /cancel para abortar.", reply_markup=reply_markup)
    return GETTING_BROADCAST_MESSAGE

@admin_only
async def broadcast_receive_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data['broadcast_message'] = update.message
    keyboard = [
        [InlineKeyboardButton("✅ SIM, ENVIAR AGORA", callback_data="broadcast_confirm")],
        [InlineKeyboardButton("❌ NÃO, CANCELAR", callback_data="admin_back_to_menu")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text("Esta é a mensagem que será enviada. Você confirma o envio?", reply_markup=reply_markup)
    return CONFIRMING_BROADCAST

@admin_only
async def broadcast_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    message_to_send = context.user_data.get('broadcast_message')
    if not message_to_send:
        await query.edit_message_text("Erro: Mensagem não encontrada. Operação cancelada.")
        return ConversationHandler.END
    await query.edit_message_text("Buscando usuários... O envio começará em breve.")
    user_ids = await db.get_all_active_tg_user_ids()
    total_users = len(user_ids)
    await query.edit_message_text(f"Iniciando envio para {total_users} usuários... Isso pode levar tempo.")
    asyncio.create_task(
        run_broadcast(context, message_to_send, user_ids, query.message.chat_id, query.message.message_id)
    )
    context.user_data.clear()
    return ConversationHandler.END

async def run_broadcast(context: ContextTypes.DEFAULT_TYPE, message_to_send, user_ids, admin_chat_id, admin_message_id):
    sent_count, failed_count, total_users = 0, 0, len(user_ids)
    for i, user_id in enumerate(user_ids):
        try:
            await context.bot.copy_message(chat_id=user_id, from_chat_id=message_to_send.chat_id, message_id=message_to_send.message_id)
            sent_count += 1
            if i % 25 == 0 and i > 0:
                await context.bot.edit_message_text(chat_id=admin_chat_id, message_id=admin_message_id, text=f"Progresso: {i}/{total_users} enviados... Pausando por 5 segundos para evitar limites.")
                await asyncio.sleep(5)
            else:
                await asyncio.sleep(1)
        except RetryAfter as e:
            logger.warning(f"Limite de flood atingido. Pausando por {e.retry_after} segundos.")
            await context.bot.edit_message_text(chat_id=admin_chat_id, message_id=admin_message_id, text=f"Limite da API atingido. Pausando por {e.retry_after}s...")
            await asyncio.sleep(e.retry_after)
            try:
                await context.bot.copy_message(chat_id=user_id, from_chat_id=message_to_send.chat_id, message_id=message_to_send.message_id)
                sent_count += 1
            except (BadRequest, Forbidden):
                failed_count += 1
        except (BadRequest, Forbidden):
            failed_count += 1
    final_text = f"📢 Envio concluído!\n\n- Mensagens enviadas: {sent_count}\n- Falhas (usuários que bloquearam o bot): {failed_count}"
    await context.bot.edit_message_text(chat_id=admin_chat_id, message_id=admin_message_id, text=final_text)


# --- ### NOVO ### FLUXO: ENVIAR LINK DE NOVO GRUPO ---
@admin_only
async def grant_new_group_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    await query.edit_message_text("Buscando grupos cadastrados...")

    groups = await db.get_all_groups_with_names()
    if not groups:
        await query.edit_message_text("Nenhum grupo encontrado no banco de dados. Cadastre um grupo primeiro.")
        return ConversationHandler.END

    keyboard = []
    for group in groups:
        # Usamos .get para segurança, caso o nome não esteja definido
        group_name = group.get('name', f"ID: {group['telegram_chat_id']}")
        keyboard.append([InlineKeyboardButton(group_name, callback_data=f"new_group_select_{group['telegram_chat_id']}")])

    keyboard.append([InlineKeyboardButton("⬅️ Voltar", callback_data="admin_back_to_menu")])
    reply_markup = InlineKeyboardMarkup(keyboard)
    await query.edit_message_text("Selecione o NOVO grupo para o qual deseja enviar convites a todos os assinantes ativos:", reply_markup=reply_markup)
    return SELECTING_NEW_GROUP

@admin_only
async def grant_new_group_select_group(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    chat_id = int(query.data.split('_')[-1])
    context.user_data['new_group_chat_id'] = chat_id

    try:
        chat = await context.bot.get_chat(chat_id)
        group_name = chat.title
    except Exception as e:
        logger.error(f"Não foi possível obter informações do grupo {chat_id}: {e}")
        group_name = f"ID {chat_id}"

    keyboard = [
        [InlineKeyboardButton("✅ SIM, ENVIAR CONVITES", callback_data="new_group_confirm")],
        [InlineKeyboardButton("❌ NÃO, CANCELAR", callback_data="admin_back_to_menu")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    text = (f"⚠️ **CONFIRMAÇÃO** ⚠️\n\n"
            f"Você está prestes a enviar um convite para o grupo **'{group_name}'** a **TODOS** os assinantes ativos.\n\n"
            f"O bot irá verificar e **não enviará** o link para quem já for membro.\n\n"
            f"Deseja continuar?")
    await query.edit_message_text(text, reply_markup=reply_markup, parse_mode=ParseMode.MARKDOWN)
    return CONFIRMING_NEW_GROUP_BROADCAST

@admin_only
async def grant_new_group_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    chat_id = context.user_data.get('new_group_chat_id')
    if not chat_id:
        await query.edit_message_text("Erro: ID do grupo não encontrado. Operação cancelada.")
        return ConversationHandler.END

    await query.edit_message_text("Buscando usuários ativos... O envio dos convites começará em breve.")

    user_ids = await db.get_all_active_tg_user_ids()
    total_users = len(user_ids)

    if total_users == 0:
        await query.edit_message_text("Nenhum usuário com assinatura ativa foi encontrado.")
        return ConversationHandler.END

    await query.edit_message_text(f"Iniciando envio de convites para {total_users} usuários... Isso pode levar tempo.")

    asyncio.create_task(
        run_new_group_broadcast(context, chat_id, user_ids, query.message.chat_id, query.message.message_id)
    )

    context.user_data.clear()
    return ConversationHandler.END

async def run_new_group_broadcast(context: ContextTypes.DEFAULT_TYPE, chat_id: int, user_ids: list[int], admin_chat_id: int, admin_message_id: int):
    """Envia um link de convite de um grupo específico para uma lista de usuários."""
    sent_count = 0
    failed_count = 0
    already_member_count = 0
    total_users = len(user_ids)

    try:
        chat = await context.bot.get_chat(chat_id)
        group_name = chat.title
    except Exception:
        group_name = f"o novo grupo (ID: {chat_id})"


    for i, user_id in enumerate(user_ids):
        try:
            # 1. VERIFICA SE O USUÁRIO JÁ É MEMBRO
            member = await context.bot.get_chat_member(chat_id=chat_id, user_id=user_id)
            if member.status in ['member', 'administrator', 'creator']:
                already_member_count += 1
                continue # Pula para o próximo

            # 2. GERA E ENVIA O LINK
            link = await context.bot.create_chat_invite_link(chat_id=chat_id, member_limit=1)
            message = (f"Olá! ✨\n\nComo nosso assinante, você ganhou acesso ao nosso novo grupo exclusivo: **{group_name}**.\n\n"
                       f"Clique no link abaixo para entrar:\n{link.invite_link}\n\n"
                       f"Este convite é só para você e expira em breve!")
            await context.bot.send_message(chat_id=user_id, text=message, parse_mode=ParseMode.MARKDOWN)
            sent_count += 1

            # 3. LÓGICA DE RATE LIMIT (igual ao broadcast normal)
            if i % 25 == 0 and i > 0:
                await context.bot.edit_message_text(
                    chat_id=admin_chat_id, message_id=admin_message_id,
                    text=f"Progresso: {i}/{total_users}... Pausando por 5s."
                )
                await asyncio.sleep(5)
            else:
                await asyncio.sleep(1)

        except (BadRequest, Forbidden):
            failed_count += 1
        except Exception as e:
            # Captura outros erros inesperados sem parar o loop
            logger.error(f"Erro inesperado ao processar usuário {user_id} para o grupo {chat_id}: {e}")
            failed_count += 1

    final_text = (f"✉️ **Envio de Convites Concluído!**\n\n"
                  f"▫️ **Grupo:** {group_name}\n"
                  f"▫️ **Total de Assinantes:** {total_users}\n"
                  f"-----------------------------------\n"
                  f"✅ **Convites enviados:** {sent_count}\n"
                  f"👤 **Já eram membros:** {already_member_count}\n"
                  f"❌ **Falhas (bot bloqueado):** {failed_count}")
    await context.bot.edit_message_text(chat_id=admin_chat_id, message_id=admin_message_id, text=final_text, parse_mode=ParseMode.MARKDOWN)
# --- ### FIM NOVO ### ---


# --- CANCELAR E CONVERSATION HANDLER ---
@admin_only
async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = "Operação cancelada."
    if update.callback_query:
        await update.callback_query.answer()
        await update.callback_query.edit_message_text(text=text)
    elif update.message:
        await update.message.reply_text(text)

    context.user_data.clear()
    return ConversationHandler.END

def get_admin_conversation_handler() -> ConversationHandler:
    return ConversationHandler(
        entry_points=[CommandHandler("admin", admin_panel)],
        states={
            SELECTING_ACTION: [
                CallbackQueryHandler(check_user_start, pattern="^admin_check_user$"),
                CallbackQueryHandler(grant_access_start, pattern="^admin_grant_access$"),
                CallbackQueryHandler(revoke_access_start, pattern="^admin_revoke_access$"),
                CallbackQueryHandler(broadcast_start, pattern="^admin_broadcast$"),
                ### NOVO ###
                CallbackQueryHandler(grant_new_group_start, pattern="^admin_grant_new_group$"),
                ### FIM NOVO ###
                CallbackQueryHandler(cancel, pattern="^admin_cancel$"),
            ],
            GETTING_USER_ID_FOR_CHECK: [
                CallbackQueryHandler(back_to_main_menu, pattern="^admin_back_to_menu$"),
                MessageHandler(filters.TEXT & ~filters.COMMAND, check_user_receive_id)
            ],
            GETTING_USER_ID_FOR_GRANT: [
                CallbackQueryHandler(back_to_main_menu, pattern="^admin_back_to_menu$"),
                MessageHandler(filters.TEXT & ~filters.COMMAND, grant_access_receive_id)
            ],
            SELECTING_PLAN_FOR_GRANT: [
                CallbackQueryHandler(grant_access_select_plan, pattern="^grant_plan_"),
                CallbackQueryHandler(back_to_main_menu, pattern="^admin_back_to_menu$")
            ],
            GETTING_USER_ID_FOR_REVOKE: [
                CallbackQueryHandler(back_to_main_menu, pattern="^admin_back_to_menu$"),
                MessageHandler(filters.TEXT & ~filters.COMMAND, revoke_access_receive_id)
            ],
            CONFIRMING_REVOKE: [
                CallbackQueryHandler(revoke_access_confirm, pattern="^revoke_confirm$"),
                CallbackQueryHandler(back_to_main_menu, pattern="^admin_back_to_menu$")
            ],
            GETTING_BROADCAST_MESSAGE: [
                CallbackQueryHandler(back_to_main_menu, pattern="^admin_back_to_menu$"),
                MessageHandler(filters.TEXT & ~filters.COMMAND, broadcast_receive_message)
            ],
            CONFIRMING_BROADCAST: [
                CallbackQueryHandler(broadcast_confirm, pattern="^broadcast_confirm$"),
                CallbackQueryHandler(back_to_main_menu, pattern="^admin_back_to_menu$")
            ],
            ### NOVO ###
            SELECTING_NEW_GROUP: [
                CallbackQueryHandler(grant_new_group_select_group, pattern="^new_group_select_"),
                CallbackQueryHandler(back_to_main_menu, pattern="^admin_back_to_menu$")
            ],
            CONFIRMING_NEW_GROUP_BROADCAST: [
                CallbackQueryHandler(grant_new_group_confirm, pattern="^new_group_confirm$"),
                CallbackQueryHandler(back_to_main_menu, pattern="^admin_back_to_menu$")
            ]
            ### FIM NOVO ###
        },
        fallbacks=[CommandHandler("cancel", cancel), CommandHandler("admin", admin_panel)],
        per_user=True,
        per_chat=True,
    )
