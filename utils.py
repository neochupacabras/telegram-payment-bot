# --- START OF FILE utils.py ---

import logging
import asyncio
from datetime import datetime, timezone, timedelta

from telegram import Bot
from telegram.ext import Application
from telegram.constants import ParseMode

import db_supabase as db

logger = logging.getLogger(__name__)

# --- Carrega o fuso horário uma vez ---
TIMEZONE_BR = timezone(timedelta(hours=-3))

def format_date_br(dt: datetime | str | None) -> str:
    """Formata data para o padrão brasileiro."""
    if not dt:
        return "N/A"
    if isinstance(dt, str):
        dt = datetime.fromisoformat(dt)
    return dt.astimezone(TIMEZONE_BR).strftime('%d/%m/%Y às %H:%M')


async def send_access_links(bot: Bot, user_id: int, payment_id: str, is_support_request: bool = False):
    """
    Gera e envia links de acesso, verificando se o usuário já é membro.
    O parâmetro 'is_support_request' diferencia uma compra nova de um pedido de suporte.
    """
    logger.info(f"[JOB][{payment_id}] Iniciando tarefa para enviar links ao usuário {user_id}.")

    group_ids = await db.get_all_group_ids()
    if not group_ids:
        logger.error(f"CRÍTICO: Nenhum grupo encontrado no DB para enviar links ao usuário {user_id}.")
        await bot.send_message(chat_id=user_id, text="⚠️ Tivemos um problema interno para buscar os grupos. Nossa equipe foi notificada.")
        return

    links_to_send_text = ""
    groups_already_in_text = ""
    failed_links = 0
    new_links_generated = 0
    expire_date = datetime.now(timezone.utc) + timedelta(hours=2)

    for chat_id in group_ids:
        try:
            # --- NOVA LÓGICA DE VERIFICAÇÃO DE MEMBRO ---
            member = await bot.get_chat_member(chat_id=chat_id, user_id=user_id)
            if member.status in ['member', 'administrator', 'creator']:
                chat = await bot.get_chat(chat_id)
                groups_already_in_text += f"✅ Você já é membro do grupo: *{chat.title}*\n"
                continue # Pula para o próximo grupo
            # ----------------------------------------------

            # Se chegou aqui, o usuário não é membro, então geramos o link.
            link = await bot.create_chat_invite_link(
                chat_id=chat_id,
                expire_date=expire_date,
                member_limit=1
            )
            chat = await bot.get_chat(chat_id)
            group_title = chat.title or f"Grupo {group_ids.index(chat_id) + 1}"
            links_to_send_text += f"🔗 *{group_title}:* {link.invite_link}\n"
            new_links_generated += 1

        except Exception as e:
            if "user not found" in str(e).lower(): # O usuário não está no grupo, o que é esperado
                try:
                    # Tentamos gerar o link mesmo assim
                    link = await bot.create_chat_invite_link(chat_id=chat_id, expire_date=expire_date, member_limit=1)
                    chat = await bot.get_chat(chat_id)
                    group_title = chat.title or f"Grupo {group_ids.index(chat_id) + 1}"
                    links_to_send_text += f"🔗 *{group_title}:* {link.invite_link}\n"
                    new_links_generated += 1
                except Exception as inner_e:
                     logger.error(f"[JOB][{payment_id}] Erro interno ao criar link para o grupo {chat_id}: {inner_e}")
                     failed_links += 1
            else:
                logger.error(f"[JOB][{payment_id}] Erro ao verificar membro ou criar link para o grupo {chat_id}: {e}")
                failed_links += 1

        await asyncio.sleep(0.2) # Evita rate limiting

    # --- LÓGICA DE MENSAGEM FINAL APRIMORADA ---
    final_message = ""
    if is_support_request:
        final_message += "Aqui está o status dos seus links de acesso:\n\n"
    else:
        final_message += "🎉 Pagamento confirmado!\n\nSeja bem-vindo(a)! Aqui estão seus links de acesso:\n\n"

    if links_to_send_text:
        final_message += links_to_send_text + "\n"

    if groups_already_in_text:
        final_message += groups_already_in_text + "\n"

    if new_links_generated > 0:
        final_message += "⚠️ **Atenção:** Cada link só pode ser usado **uma vez** e expira em breve."

    if new_links_generated == 0 and is_support_request:
        final_message += "Parece que você já está em todos os nossos grupos! Nenhum link novo foi necessário."

    if failed_links > 0:
        final_message += f"\n\n❌ Não foi possível gerar links para {failed_links} grupo(s). Por favor, contate o suporte se precisar."

    await bot.send_message(chat_id=user_id, text=final_message, parse_mode=ParseMode.MARKDOWN)

    logger.info(f"✅ [JOB][{payment_id}] Tarefa de links para o usuário {user_id} concluída. Gerados: {new_links_generated}, Já membro: {len(group_ids) - new_links_generated - failed_links}, Falhas: {failed_links}")
