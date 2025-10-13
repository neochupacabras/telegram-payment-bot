# --- START OF FILE db_supabase.py (CORRIGIDO) ---

import os
import asyncio
import logging
from supabase import create_client, Client
from telegram import User as TelegramUser # Renomeia para evitar conflito de nome

# Apenas pega o logger. A configuração será feita em app.py
logger = logging.getLogger(__name__)

# Carrega as credenciais do ambiente
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
        # MUDANÇA AQUI: Removemos o .single() para evitar o erro quando o usuário não existe.
        # A busca agora retorna uma lista.
        response = await asyncio.to_thread(
            lambda: supabase.table('users').select('id, first_name, username').eq('telegram_user_id', tg_user.id).execute()
        )

        # Se a lista de dados não estiver vazia, o usuário já existe.
        if response.data:
            user_data = response.data[0] # Pegamos o primeiro (e único) item da lista

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

        # Se a lista está vazia, criamos o usuário.
        else:
            logger.info(f"➕ [DB] Usuário {tg_user.id} não encontrado. Criando...")
            # Aqui podemos usar .single() pois temos certeza que a inserção retornará um único item.
            insert_response = await asyncio.to_thread(
                lambda: supabase.table('users').insert({
                    "telegram_user_id": tg_user.id,
                    "first_name": tg_user.first_name,
                    "username": tg_user.username
                }).select('id, first_name, username').single().execute()
            )
            logger.info(f"✅ [DB] Usuário {tg_user.id} criado com sucesso.")
            return insert_response.data

    except Exception as e:
        logger.error(f"❌ [DB] Erro inesperado em get_or_create_user para {tg_user.id}: {e}", exc_info=True)
        return None


async def create_pending_transaction(db_user_id: int, mp_payment_id: str, amount: float):
    """Cria um registro de transação com status 'pending'."""
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
    """Busca o status de uma transação pelo ID de pagamento do Mercado Pago."""
    if not supabase:
        logger.error("❌ [DB] Cliente Supabase não disponível.")
        return None

    try:
        # MUDANÇA AQUI: Também removemos o .single() daqui.
        response = await asyncio.to_thread(
            lambda: supabase.table('transactions').select('status').eq('mp_payment_id', mp_payment_id).execute()
        )
        if response.data:
            return response.data[0].get('status')
        return None # Retorna None se a transação não for encontrada
    except Exception as e:
        logger.error(f"❌ [DB] Erro inesperado em get_transaction_status para {mp_payment_id}: {e}", exc_info=True)
        return None


async def update_transaction_status(mp_payment_id: str, new_status: str):
    """Atualiza o status de uma transação."""
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
