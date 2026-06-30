import logging
import os

os.makedirs("logs", exist_ok=True)

logger = logging.getLogger("ALGO_BOT")
logger.setLevel(logging.INFO)
logger.propagate = False

if not logger.handlers:
    file_handler = logging.FileHandler("logs/bot.log", encoding="utf-8")
    file_handler.setFormatter(logging.Formatter("%(asctime)s - %(levelname)s - %(message)s"))
    logger.addHandler(file_handler)

    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.CRITICAL)
    console_handler.setFormatter(logging.Formatter("CRITICAL: %(message)s"))
    logger.addHandler(console_handler)
