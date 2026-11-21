"""Environment handed to the subprocesses the gateway spawns (the headless ``claude`` agent and its MCP child).

The gateway's own process holds every secret the deployment knows (``load_dotenv`` puts the OpenAI, Alpha Vantage
and Enable Banking credentials in ``os.environ``). An agent that runs tools on the user's behalf has no business
seeing them, so the children get a copy of the environment with the secrets removed. The agent must run on the
user's Claude login only: an ``ANTHROPIC_API_KEY`` is dropped as well.
"""

import os
import re
from typing import Dict, Mapping, Optional

_SECRET_PREFIXES = ("OPENAI_", "ALPHAVANTAGE_", "ENABLE_BANKING_", "DB_", "ANTHROPIC_", "AWS_", "AZURE_", "GOOGLE_")
_SECRET_SUFFIX = re.compile(r"(_API_KEY|_SECRET|_SECRET_KEY|_TOKEN|_PASSWORD|PASSWORD)$")


def is_secret_name(name: str) -> bool:
    return name.startswith(_SECRET_PREFIXES) or bool(_SECRET_SUFFIX.search(name))


def child_env(base: Optional[Mapping[str, str]] = None, **extra: str) -> Dict[str, str]:
    """A copy of ``base`` (default: this process's environment) without secrets, plus ``extra``."""
    source = os.environ if base is None else base
    env = {k: v for k, v in source.items() if not is_secret_name(k)}
    env.update(extra)
    return env
