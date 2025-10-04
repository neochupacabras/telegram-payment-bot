# --- START OF FILE gunicorn_config.py ---

import threading
import fcntl  # Módulo para controle de I/O, incluindo file locks
import os
from app import run_bot_polling, logger

# O caminho para o nosso arquivo de lock. /tmp é um bom lugar em ambientes Linux.
LOCK_FILE = '/tmp/telegram_bot.lock'
f = None

# Esta função é executada ANTES da criação dos workers
def on_starting(server):
    global f
    # Abrimos o arquivo de lock aqui.
    f = open(LOCK_FILE, 'w')
    try:
        # Tentamos adquirir um lock exclusivo (LOCK_EX) sem bloquear (LOCK_NB).
        # Se o arquivo já estiver travado por outro processo, isso levantará um erro.
        fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
        logger.info("Lock adquirido. Este processo será o mestre do bot.")
    except (IOError, OSError):
        # Se não conseguirmos o lock, outro processo já é o mestre.
        logger.warning("Não foi possível adquirir o lock. Outro processo já está rodando o bot.")
        f = None # Marcamos que não temos o lock.

# Esta função é executada em cada worker APÓS sua criação
def post_fork(server, worker):
    # Apenas o processo que conseguiu adquirir o lock (f não é None)
    # deve iniciar a thread do bot.
    if f:
        logger.info(f"Gunicorn worker (PID: {worker.pid}) iniciado pelo processo mestre. Iniciando a thread do bot...")
        bot_thread = threading.Thread(target=run_bot_polling, daemon=True)
        bot_thread.start()
        logger.info("Thread do bot iniciada com sucesso no worker.")
    else:
        logger.info(f"Gunicorn worker (PID: {worker.pid}) iniciado, mas não é o mestre. Não iniciará o bot.")

# Esta função é executada quando o processo mestre está saindo
def on_exit(server):
    # Garante que o lock seja liberado quando o serviço parar.
    if f:
        logger.info("Processo mestre saindo. Liberando o lock.")
        fcntl.flock(f, fcntl.LOCK_UN)
        f.close()

# --- END OF FILE gunicorn_config.py ---
