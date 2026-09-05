from pathlib import Path

import yaml
from dotenv import load_dotenv

DEFAULT_CONFIG_PATH = Path("config/config.yaml")


def load_config(path: Path = DEFAULT_CONFIG_PATH) -> dict:
    load_dotenv()
    with open(path, "r") as f:
        return yaml.safe_load(f)
