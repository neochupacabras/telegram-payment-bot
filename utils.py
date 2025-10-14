# --- START OF FILE utils.py ---

import logging
import asyncio
from datetime import datetime, timezone, timedelta

from telegram import Bot
from telegram.ext import Application

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


async def send_access_links(bot: Bot, user_id: int, payment_id: str):
    """Gera e envia os links de acesso para TODOS os grupos configurados."""
    logger.info(f"[JOB][{payment_id}] Iniciando tarefa para enviar links ao usuário {user_id}.")

    # Busca os IDs de todos os grupos do banco de dados
    group_ids = await db.get_all_group_ids()
    if not group_ids:
        logger.error(f"CRÍTICO: Nenhum grupo encontrado no banco de dados para enviar links ao usuário {user_id}.")
        await bot.send_message(chat_id=user_id, text="⚠️ Tivemos um problema interno para buscar os grupos. Nossa equipe foi notificada.")
        return

    links_text = ""
    failed_links = 0
    expire_date = datetime.now(timezone.utc) + timedelta(hours=2) # Link válido por 2 horas

    for chat_id in group_ids:
        try:
            # Cria um link de convite de uso único para cada grupo
            link = await bot.create_chat_invite_link(
                chat_id=chat_id,
                expire_date=expire_date,
                member_limit=1
            )
            # Adiciona o nome do grupo ao link para melhor identificação
            chat = await bot.get_chat(chat_id)
            group_title = chat.title or f"Grupo {group_ids.index(chat_id) + 1}"
            links_text += f"🔗 *{group_title}:* {link.invite_link}\n"
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
    # Importante: precisamos do parse_mode para o negrito no título do grupo
    await bot.send_message(chat_id=user_id, text=success_message, parse_mode='Markdown')

    if failed_links == 0:
        logger.info(f"✅ [JOB][{payment_id}] Todos os {len(group_ids)} links de acesso foram enviados com sucesso para o usuário {user_id}")
    else:
         logger.warning(f"⚠️ [JOB][{payment_id}] Foram enviados links para o usuário {user_id}, mas {failed_links} falharam ao ser gerados.")
