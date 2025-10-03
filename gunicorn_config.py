# --- START OF FILE gunicorn_config.py ---

import threading
from app import run_bot_polling, logger

# Esta função 'hook' é executada pelo Gunicorn em cada processo 'worker'
# depois que ele é criado.
def post_fork(server, worker):
    logger.info(f"Gunicorn worker (PID: {worker.pid}) iniciado. Iniciando a thread do bot...")

    # Inicia a thread do bot aqui, garantindo que seja executada apenas uma vez por worker.
    bot_thread = threading.Thread(target=run_bot_polling, daemon=True)
    bot_thread.start()

    logger.info("Thread do bot iniciada com sucesso no worker.")

# --- END OF FILE gunicorn_config.py ---
