# --- START OF FILE db_supabase.py (FINAL VERSION - TAROT LOGIC APPLIED) ---

import os
import asyncio
import logging
from supabase import create_client, Client
from telegram import User as TelegramUser

logger = logging.getLogger(__name__)

url: str = os.getenv("SUPABASE_URL")
key: str = os.getenv("SUPABASE_KEY")

supabase: Client = None
if not url or not key:
    logger.critical("ERRO CRÍTICO: Credenciais do Supabase (URL ou KEY) não encontradas.")
else:
    try:
        supabase: Client = create_client(url, key)
        logger.info("✅ Cliente Supabase criado com sucesso.")
    except Exception as e:
        logger.critical(f"Falha ao criar o cliente Supabase: {e}", exc_info=True)


async def get_or_create_user(tg_user: TelegramUser) -> dict | None:
    """Busca um usuário no DB pelo ID do Telegram ou o cria se não existir."""
    if not supabase:
        logger.error("❌ [DB] Cliente Supabase não disponível.")
        return None

    try:
        # 1. Tenta buscar o usuário (lógica do bot de pagamentos)
        response = await asyncio.to_thread(
            lambda: supabase.table('users').select('id, first_name, username').eq('telegram_user_id', tg_user.id).execute()
        )

        # 2. Se a busca retornar dados, o usuário existe (lógica do bot de pagamentos)
        if response.data:
            user_data = response.data[0]
            # Opcional: Atualiza dados se mudaram
            if user_data.get('first_name') != tg_user.first_name or user_data.get('username') != tg_user.username:
                await asyncio.to_thread(
                    lambda: supabase.table('users').update({
                        "first_name": tg_user.first_name,
                        "username": tg_user.username
                    }).eq('telegram_user_id', tg_user.id).execute()
                )
                logger.info(f"🔄 [DB] Dados do usuário {tg_user.id} atualizados.")
            return user_data

        # 3. Se a busca NÃO retornar dados, o usuário precisa ser criado (lógica do bot de Tarô)
        else:
            logger.info(f"➕ [DB] Usuário {tg_user.id} não encontrado. Criando...")
            # CORREÇÃO APLICADA AQUI: Inserimos primeiro
            await asyncio.to_thread(
                lambda: supabase.table('users').insert({
                    "telegram_user_id": tg_user.id,
                    "first_name": tg_user.first_name,
                    "username": tg_user.username
                }).execute()
            )
            # E DEPOIS buscamos o usuário que acabamos de criar para retornar seus dados
            logger.info(f"✅ [DB] Usuário {tg_user.id} criado. Buscando novamente para confirmar...")
            new_user_response = await asyncio.to_thread(
                lambda: supabase.table('users').select('id, first_name, username').eq('telegram_user_id', tg_user.id).execute()
            )
            if new_user_response.data:
                return new_user_response.data[0]
            else:
                logger.error(f"❌ [DB] CRÍTICO: Falha ao buscar o usuário {tg_user.id} imediatamente após a criação.")
                return None

    except Exception as e:
        logger.error(f"❌ [DB] Erro inesperado em get_or_create_user para {tg_user.id}: {e}", exc_info=True)
        return None


# As funções abaixo já estavam corretas e não precisam de alteração
async def create_pending_transaction(db_user_id: int, mp_payment_id: str, amount: float):
    if not supabase:
        logger.error("❌ [DB] Cliente Supabase não disponível.")
        return
    try:
        logger.info(f"💾 [DB] Registrando transação pendente {mp_payment_id} para o usuário ID {db_user_id}...")
        await asyncio.to_thread(
            lambda: supabase.table('transactions').insert({
                "user_id": db_user_id,
                "mp_payment_id": mp_payment_id,
                "amount": amount,
                "status": "pending"
            }).execute()
        )
        logger.info(f"✅ [DB] Transação pendente {mp_payment_id} registrada.")
    except Exception as e:
        logger.error(f"❌ [DB] Erro ao criar transação pendente para {mp_payment_id}: {e}", exc_info=True)


async def get_transaction_status(mp_payment_id: str) -> str | None:
    if not supabase:
        logger.error("❌ [DB] Cliente Supabase não disponível.")
        return None
    try:
        response = await asyncio.to_thread(
            lambda: supabase.table('transactions').select('status').eq('mp_payment_id', mp_payment_id).execute()
        )
        if response.data:
            return response.data[0].get('status')
        return None
    except Exception as e:
        logger.error(f"❌ [DB] Erro inesperado em get_transaction_status para {mp_payment_id}: {e}", exc_info=True)
        return None


async def update_transaction_status(mp_payment_id: str, new_status: str):
    if not supabase:
        logger.error("❌ [DB] Cliente Supabase não disponível.")
        return
    try:
        logger.info(f"🔄 [DB] Atualizando transação {mp_payment_id} para status '{new_status}'...")
        await asyncio.to_thread(
            lambda: supabase.table('transactions').update({
                "status": new_status
            }).eq('mp_payment_id', mp_payment_id).execute()
        )
        logger.info(f"✅ [DB] Transação {mp_payment_id} atualizada para '{new_status}'.")
    except Exception as e:
        logger.error(f"❌ [DB] Erro ao atualizar status da transação {mp_payment_id}: {e}", exc_info=True)
