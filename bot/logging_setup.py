import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path


def configure_logging(
    file: str = "logs/bot.log",
    level: str = "INFO",
    max_bytes: int = 5_000_000,
    backup_count: int = 5,
) -> None:
    log_path = Path(file)
    log_path.parent.mkdir(parents=True, exist_ok=True)

    formatter = logging.Formatter(
        "%(asctime)s %(levelname)s %(name)s: %(message)s"
    )

    file_handler = RotatingFileHandler(
        log_path, maxBytes=max_bytes, backupCount=backup_count
    )
    file_handler.setFormatter(formatter)

    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)

    root = logging.getLogger()
    root.setLevel(level)
    root.addHandler(file_handler)
    root.addHandler(console_handler)
