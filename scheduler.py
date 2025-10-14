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

# --- NOVA FUNÇÃO REUTILIZÁVEL ---
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

# --- FUNÇÕES DO SCHEDULER ---

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

            # --- MODIFICAÇÃO AQUI ---
            # Chama a nova função reutilizável
            removed_count = await kick_user_from_all_groups(user_id, bot)

            # Atualiza o status da assinatura
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


async def find_and_process_expired_subscriptions(supabase: Client, bot: Bot):
    """Encontra assinaturas vencidas, remove os usuários dos grupos e atualiza o status."""
    try:
        now_iso = datetime.now(TIMEZONE_BR).isoformat()

        # Busca todas as assinaturas ativas que já venceram
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

        # Busca a lista de grupos uma única vez
        groups_response = await asyncio.to_thread(
            lambda: supabase.table('groups').select('telegram_chat_id').execute()
        )
        group_ids = [g['telegram_chat_id'] for g in groups_response.data]

        if not group_ids:
            logger.error("CRÍTICO: Nenhum grupo encontrado no DB. Não é possível remover usuários.")
            return

        for sub in expired_response.data:
            user_id = sub.get('user', {}).get('telegram_user_id')
            sub_id = sub.get('id')

            if not user_id:
                continue

            logger.info(f"Processando expiração para o usuário {user_id} (assinatura {sub_id}).")
            removed_count = 0
            for group_id in group_ids:
                try:
                    # 1. Expulsa o usuário
                    await bot.ban_chat_member(chat_id=group_id, user_id=user_id)
                    logger.info(f"Usuário {user_id} expulso do grupo {group_id}.")

                    # 2. IMPORTANTE: Remove o ban para que ele possa entrar de novo se pagar
                    await bot.unban_chat_member(chat_id=group_id, user_id=user_id, only_if_banned=True)
                    logger.info(f"Ban do usuário {user_id} removido do grupo {group_id}.")

                    removed_count += 1
                except Forbidden:
                    logger.warning(f"Sem permissão para remover {user_id} do grupo {group_id}. O bot é admin?")
                except BadRequest as e:
                    if "user not found" in str(e) or "member not found" in str(e):
                        logger.info(f"Usuário {user_id} já não estava no grupo {group_id}.")
                    else:
                        logger.error(f"Erro do Telegram ao remover {user_id} do grupo {group_id}: {e}")

            # 3. Atualiza o status da assinatura no banco de dados
            await asyncio.to_thread(
                lambda: supabase.table('subscriptions').update({'status': 'expired'}).eq('id', sub_id).execute()
            )
            logger.info(f"Assinatura {sub_id} do usuário {user_id} marcada como 'expired'. Removido de {removed_count} grupos.")
            try:
                await bot.send_message(chat_id=user_id, text="Sua assinatura expirou e seu acesso aos grupos foi removido. Para voltar, use o comando /renovar.")
            except (Forbidden, BadRequest):
                pass # Se não puder avisar, tudo bem.

    except Exception as e:
        logger.error(f"Erro CRÍTICO no processo de expiração: {e}", exc_info=True)


async def main():
    if not all([SUPABASE_URL, SUPABASE_KEY, TELEGRAM_BOT_TOKEN]):
        logger.critical("Variáveis de ambiente essenciais para o scheduler não foram carregadas.")
        sys.exit(1)

    supabase_client = create_client(SUPABASE_URL, SUPABASE_KEY)
    telegram_bot = Bot(token=TELEGRAM_BOT_TOKEN)

    logger.info("--- Iniciando verificação do scheduler ---")
    await find_and_process_expiring_subscriptions(supabase_client, telegram_bot)
    await find_and_process_expired_subscriptions(supabase_client, telegram_bot)
    logger.info("--- Verificação do scheduler concluída ---")


if __name__ == "__main__":
    asyncio.run(main())
