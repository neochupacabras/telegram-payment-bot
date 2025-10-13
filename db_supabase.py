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
            return subscription # Retorna os dados existentes

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
        update_response = await asyncio.to_thread(
            lambda: supabase.table('subscriptions')
            .update(update_payload)
            .eq('mp_payment_id', mp_payment_id)
            .select('*, user:users(telegram_user_id)') # Retorna o tg_user_id
            .execute()
        )

        logger.info(f"✅ [DB] Assinatura {subscription['id']} ativada para o pagamento {mp_payment_id}.")
        return update_response.data[0] if update_response.data else None

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
