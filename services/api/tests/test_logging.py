from __future__ import annotations

import logging

from northstar_api.config import Settings
from northstar_api.logging import configure_logging


def test_provider_http_clients_never_log_oauth_urls_at_info() -> None:
    configure_logging(Settings(app_env="test", log_level="INFO"))

    assert logging.getLogger("httpx").getEffectiveLevel() >= logging.WARNING
    assert logging.getLogger("httpcore").getEffectiveLevel() >= logging.WARNING
