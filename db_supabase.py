# --- START OF FILE db_supabase.py (ARQUITETURA DE ASSINATURAS) ---

import os
import asyncio
import logging
from datetime import datetime, timedelta, timezone
from supabase import create_client, Client
from telegram import User as TelegramUser

logger = logging.getLogger(__name__)

url: str = os.getenv("SUPABASE_URL")
key: str = os.getenv("SUPABASE_KEY")
TIMEZONE_BR = timezone(timedelta(hours=-3))

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
    # (Esta função permanece a mesma da versão anterior, sem alterações necessárias)
    if not supabase: return None
    try:
        response = await asyncio.to_thread(
            lambda: supabase.table('users').select('id, first_name, username').eq('telegram_user_id', tg_user.id).execute()
        )
        if response.data:
            user_data = response.data[0]
            if user_data.get('first_name') != tg_user.first_name or user_data.get('username') != tg_user.username:
                await asyncio.to_thread(
                    lambda: supabase.table('users').update({
                        "first_name": tg_user.first_name,
                        "username": tg_user.username
                    }).eq('telegram_user_id', tg_user.id).execute()
                )
            return user_data
        else:
            await asyncio.to_thread(
                lambda: supabase.table('users').insert({
                    "telegram_user_id": tg_user.id,
                    "first_name": tg_user.first_name,
                    "username": tg_user.username
                }).execute()
            )
            new_user_response = await asyncio.to_thread(
                lambda: supabase.table('users').select('id, first_name, username').eq('telegram_user_id', tg_user.id).execute()
            )
            return new_user_response.data[0] if new_user_response.data else None
    except Exception as e:
        logger.error(f"❌ [DB] Erro inesperado em get_or_create_user para {tg_user.id}: {e}", exc_info=True)
        return None

# --- NOVAS FUNÇÕES ---

async def get_product_by_id(product_id: int) -> dict | None:
    """Busca os detalhes de um produto pelo seu ID."""
    if not supabase: return None
    try:
        response = await asyncio.to_thread(
            lambda: supabase.table('products').select('*').eq('id', product_id).single().execute()
        )
        return response.data
    except Exception as e:
        logger.error(f"❌ [DB] Erro ao buscar produto {product_id}: {e}", exc_info=True)
        return None

async def create_pending_subscription(db_user_id: int, product_id: int, mp_payment_id: str) -> dict | None:
    """Cria um registro de assinatura com status 'pending_payment'."""
    if not supabase: return None
    try:
        logger.info(f"💾 [DB] Registrando assinatura pendente para user {db_user_id}, produto {product_id}...")
        response = await asyncio.to_thread(
            lambda: supabase.table('subscriptions').insert({
                "user_id": db_user_id,
                "product_id": product_id,
                "mp_payment_id": mp_payment_id,
                "status": "pending_payment"
            }).execute()
        )
        return response.data[0] if response.data else None
    except Exception as e:
        logger.error(f"❌ [DB] Erro ao criar assinatura pendente: {e}", exc_info=True)
        return None

async def activate_subscription(mp_payment_id: str) -> dict | None:
    """Ativa uma assinatura, definindo as datas de início e fim."""
    if not supabase: return None
    try:
        # 1. Busca a assinatura e o produto associado
        sub_response = await asyncio.to_thread(
            lambda: supabase.table('subscriptions')
            .select('*, product:products(*)')
            .eq('mp_payment_id', mp_payment_id)
            .single()
            .execute()
        )
        if not sub_response.data:
            logger.warning(f"⚠️ [DB] Assinatura com mp_payment_id {mp_payment_id} não encontrada para ativação.")
            return None

        subscription = sub_response.data
        product = subscription.get('product')

        if subscription.get('status') == 'active':
            logger.warning(f"⚠️ [DB] Assinatura {subscription['id']} já está ativa. Ignorando.")
            # --- CORREÇÃO APLICADA AQUI ---
            # Se já estiver ativa, precisamos buscar os dados do usuário para retornar
            user_data_response = await asyncio.to_thread(
                lambda: supabase.table('subscriptions')
                .select('*, user:users(telegram_user_id)')
                .eq('mp_payment_id', mp_payment_id)
                .single()
                .execute()
            )
            return user_data_response.data if user_data_response.data else None


        # 2. Calcula as datas
        start_date = datetime.now(TIMEZONE_BR)
        end_date = None
        if product and product.get('duration_days'):
            end_date = start_date + timedelta(days=product['duration_days'])

        # 3. Atualiza o registro
        update_payload = {
            "status": "active",
            "start_date": start_date.isoformat(),
            "end_date": end_date.isoformat() if end_date else None
        }

        # --- CORREÇÃO APLICADA AQUI: SEPARAMOS UPDATE DO SELECT ---
        # 3.1. Primeiro, apenas executamos a atualização.
        await asyncio.to_thread(
            lambda: supabase.table('subscriptions')
            .update(update_payload)
            .eq('mp_payment_id', mp_payment_id)
            .execute()
        )

        # 3.2. Agora, buscamos os dados atualizados em uma nova query.
        final_data_response = await asyncio.to_thread(
            lambda: supabase.table('subscriptions')
            .select('*, user:users(telegram_user_id)') # Retorna o tg_user_id
            .eq('mp_payment_id', mp_payment_id)
            .single() # Usamos single() pois esperamos apenas um resultado
            .execute()
        )

        logger.info(f"✅ [DB] Assinatura {subscription['id']} ativada para o pagamento {mp_payment_id}.")
        return final_data_response.data if final_data_response.data else None

    except Exception as e:
        logger.error(f"❌ [DB] Erro ao ativar assinatura {mp_payment_id}: {e}", exc_info=True)
        return None

async def get_user_active_subscription(telegram_user_id: int) -> dict | None:
    """Busca a assinatura ativa de um usuário, incluindo dados do produto."""
    if not supabase: return None
    try:
        response = await asyncio.to_thread(
            lambda: supabase.table('users')
            .select('*, subscriptions(*, product:products(*))')
            .eq('telegram_user_id', telegram_user_id)
            .eq('subscriptions.status', 'active')
            .single()
            .execute()
        )
        if response.data and response.data.get('subscriptions'):
            # A API retorna uma lista, mesmo que haja apenas uma assinatura ativa
            return response.data['subscriptions'][0]
        return None
    except Exception as e:
        # Não loga como erro se for 'single result requested but more than one was found'
        if "single result" not in str(e):
             logger.error(f"❌ [DB] Erro ao buscar assinatura ativa para {telegram_user_id}: {e}")
        return None

async def get_all_group_ids() -> list[int]:
    """Busca os IDs de todos os grupos cadastrados."""
    if not supabase: return []
    try:
        response = await asyncio.to_thread(
            lambda: supabase.table('groups').select('telegram_chat_id').execute()
        )
        return [item['telegram_chat_id'] for item in response.data] if response.data else []
    except Exception as e:
        logger.error(f"❌ [DB] Erro ao buscar IDs dos grupos: {e}", exc_info=True)
        return []


# --- NOVAS FUNÇÕES DE ADMIN ---

async def find_user_by_id_or_username(identifier: str) -> dict | None:
    """Busca um usuário pelo seu Telegram ID ou username (@ a ser removido)."""
    if not supabase: return None
    try:
        query = supabase.table('users').select('*, subscriptions(*, product:products(*))')
        if identifier.isdigit():
            query = query.eq('telegram_user_id', int(identifier))
        else:
            # Remove o '@' se presente
            username = identifier[1:] if identifier.startswith('@') else identifier
            query = query.eq('username', username)

        response = await asyncio.to_thread(lambda: query.single().execute())
        return response.data
    except Exception as e:
        if "single result" not in str(e): # Ignora erro comum de não encontrar usuário
            logger.error(f"[DB] Erro ao buscar usuário por '{identifier}': {e}")
        return None

async def create_manual_subscription(db_user_id: int, product_id: int, admin_notes: str) -> dict | None:
    """Cria uma assinatura ativa manualmente por um admin."""
    if not supabase: return None
    try:
        product = await get_product_by_id(product_id)
        if not product:
            logger.error(f"[DB] Produto {product_id} não encontrado para concessão manual.")
            return None

        start_date = datetime.now(TIMEZONE_BR)
        end_date = None
        if product.get('duration_days'):
            end_date = start_date + timedelta(days=product['duration_days'])

        # Cria a assinatura
        response = await asyncio.to_thread(
            lambda: supabase.table('subscriptions')
            .insert({
                "user_id": db_user_id,
                "product_id": product_id,
                "mp_payment_id": admin_notes, # Usamos para registrar que foi manual
                "status": "active",
                "start_date": start_date.isoformat(),
                "end_date": end_date.isoformat() if end_date else None
            }).execute()
        )
        logger.info(f"✅ [DB] Assinatura manual criada para o usuário {db_user_id}.")
        return response.data[0] if response.data else None
    except Exception as e:
        logger.error(f"❌ [DB] Erro ao criar assinatura manual para o usuário {db_user_id}: {e}")
        return None

async def revoke_subscription(db_user_id: int, admin_notes: str) -> bool:
    """Revoga a assinatura ativa de um usuário."""
    if not supabase: return False
    try:
        await asyncio.to_thread(
            lambda: supabase.table('subscriptions')
            .update({
                "status": "revoked_by_admin",
                "end_date": datetime.now(TIMEZONE_BR).isoformat()
            })
            .eq('user_id', db_user_id)
            .eq('status', 'active')
            .execute()
        )
        logger.info(f"✅ [DB] Assinatura do usuário {db_user_id} revogada pelo admin: {admin_notes}")
        return True
    except Exception as e:
        logger.error(f"❌ [DB] Erro ao revogar assinatura do usuário {db_user_id}: {e}")
        return False

async def get_all_active_tg_user_ids() -> list[int]:
    """Retorna uma lista de Telegram User IDs de todos os usuários com assinatura ativa."""
    if not supabase: return []
    try:
        response = await asyncio.to_thread(
            lambda: supabase.table('subscriptions')
            .select('user:users(telegram_user_id)')
            .eq('status', 'active')
            .execute()
        )
        if not response.data:
            return []
        # Extrai os IDs da estrutura aninhada e remove duplicatas
        user_ids = {item['user']['telegram_user_id'] for item in response.data if item.get('user')}
        return list(user_ids)
    except Exception as e:
        logger.error(f"❌ [DB] Erro ao buscar todos os usuários ativos: {e}")
        return []

async def get_all_groups_with_names() -> list[dict]:
    """Busca os IDs e nomes de todos os grupos cadastrados."""
    if not supabase: return []
    try:
        response = await asyncio.to_thread(
            lambda: supabase.table('groups').select('telegram_chat_id, name').execute()
        )
        return response.data if response.data else []
    except Exception as e:
        logger.error(f"❌ [DB] Erro ao buscar grupos com nomes: {e}", exc_info=True)
        return []
