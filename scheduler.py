# --- START OF FILE scheduler.py (VERSÃO CORRIGIDA E COMPLETA) ---

import os
import asyncio
import logging
import sys
from datetime import datetime, timedelta, timezone
from dotenv import load_dotenv
from supabase import create_client, Client
from telegram import Bot
from telegram.error import BadRequest, Forbidden

# --- CONFIGURAÇÃO ---
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', stream=sys.stdout)
logger = logging.getLogger("Scheduler")
load_dotenv()

# Carrega as mesmas variáveis de ambiente
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TIMEZONE_BR = timezone(timedelta(hours=-3))

# --- FUNÇÃO REUTILIZÁVEL ---
async def kick_user_from_all_groups(user_id: int, bot: Bot):
    """Expulsa e desbane um usuário de todos os grupos listados no DB."""
    # Conexão com Supabase dentro da função para ser autônoma
    supabase_client = create_client(os.getenv("SUPABASE_URL"), os.getenv("SUPABASE_KEY"))

    groups_response = await asyncio.to_thread(
        lambda: supabase_client.table('groups').select('telegram_chat_id').execute()
    )
    group_ids = [g['telegram_chat_id'] for g in groups_response.data]

    if not group_ids:
        logger.error(f"CRÍTICO: [kick_user] Nenhum grupo encontrado no DB. Não é possível remover {user_id}.")
        return 0

    removed_count = 0
    for group_id in group_ids:
        try:
            await bot.ban_chat_member(chat_id=group_id, user_id=user_id)
            await bot.unban_chat_member(chat_id=group_id, user_id=user_id, only_if_banned=True)
            logger.info(f"[kick_user] Usuário {user_id} removido do grupo {group_id}.")
            removed_count += 1
        except Forbidden:
            logger.warning(f"[kick_user] Sem permissão para remover {user_id} do grupo {group_id}.")
        except BadRequest as e:
            if "user not found" in str(e) or "member not found" in str(e):
                logger.info(f"[kick_user] Usuário {user_id} já não estava no grupo {group_id}.")
            else:
                logger.error(f"[kick_user] Erro do Telegram ao remover {user_id} do {group_id}: {e}")
    return removed_count

# --- FUNÇÕES DO SCHEDULER (A FUNÇÃO QUE FALTAVA FOI REINSERIDA) ---

async def find_and_process_expiring_subscriptions(supabase: Client, bot: Bot):
    """Encontra assinaturas que estão para vencer e envia avisos."""
    try:
        three_days_from_now = (datetime.now(TIMEZONE_BR) + timedelta(days=3)).isoformat()
        two_days_from_now = (datetime.now(TIMEZONE_BR) + timedelta(days=2)).isoformat()

        # Busca assinaturas que vencem em exatamente 3 dias (entre 2 e 3 dias a partir de agora)
        response = await asyncio.to_thread(
            lambda: supabase.table('subscriptions')
            .select('*, user:users(telegram_user_id)')
            .eq('status', 'active')
            .lte('end_date', three_days_from_now)
            .gte('end_date', two_days_from_now)
            .execute()
        )

        if not response.data:
            logger.info("Nenhuma assinatura encontrada para enviar aviso de vencimento.")
            return

        for sub in response.data:
            user_id = sub.get('user', {}).get('telegram_user_id')
            if user_id:
                end_date_br = datetime.fromisoformat(sub['end_date']).astimezone(TIMEZONE_BR).strftime('%d/%m/%Y')
                message = f"Olá! 👋 Sua assinatura está próxima de vencer (em {end_date_br}). Para não perder o acesso, use o comando /renovar e efetue o pagamento."
                try:
                    await bot.send_message(chat_id=user_id, text=message)
                    logger.info(f"Aviso de vencimento enviado para o usuário {user_id}.")
                except (Forbidden, BadRequest):
                    logger.warning(f"Não foi possível enviar aviso para o usuário {user_id} (bloqueou o bot?).")
    except Exception as e:
        logger.error(f"Erro ao processar avisos de expiração: {e}", exc_info=True)


async def find_and_process_expired_subscriptions(supabase: Client, bot: Bot):
    """Encontra assinaturas vencidas, remove os usuários e atualiza o status."""
    try:
        now_iso = datetime.now(TIMEZONE_BR).isoformat()

        expired_response = await asyncio.to_thread(
            lambda: supabase.table('subscriptions')
            .select('id, user:users(telegram_user_id)')
            .eq('status', 'active')
            .lt('end_date', now_iso)
            .execute()
        )

        if not expired_response.data:
            logger.info("Nenhuma assinatura vencida encontrada.")
            return

        logger.info(f"Encontradas {len(expired_response.data)} assinaturas vencidas para processar.")

        for sub in expired_response.data:
            user_id = sub.get('user', {}).get('telegram_user_id')
            sub_id = sub.get('id')

            if not user_id: continue

            logger.info(f"Processando expiração para o usuário {user_id} (assinatura {sub_id}).")

            removed_count = await kick_user_from_all_groups(user_id, bot)

            await asyncio.to_thread(
                lambda: supabase.table('subscriptions').update({'status': 'expired'}).eq('id', sub_id).execute()
            )
            logger.info(f"Assinatura {sub_id} do usuário {user_id} marcada como 'expired'. Removido de {removed_count} grupos.")
            try:
                await bot.send_message(chat_id=user_id, text="Sua assinatura expirou e seu acesso aos grupos foi removido. Para voltar, use o comando /renovar.")
            except (Forbidden, BadRequest):
                pass
    except Exception as e:
        logger.error(f"Erro CRÍTICO no processo de expiração: {e}", exc_info=True)


