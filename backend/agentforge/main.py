"""uvicorn 入口：`uvicorn agentforge.main:app --reload`"""

import logging

from agentforge.api.app import create_app

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-7s %(name)s | %(message)s",
    datefmt="%H:%M:%S",
)

app = create_app()
