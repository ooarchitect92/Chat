from __future__ import annotations

from prometheus_client import Counter, Gauge, Histogram

HTTP_REQUESTS = Counter(
    "northstar_http_requests_total", "HTTP requests", labelnames=("method", "route", "status")
)
HTTP_DURATION = Histogram(
    "northstar_http_request_duration_seconds", "HTTP request duration", labelnames=("method", "route")
)
CHAT_REQUESTS = Counter("northstar_chat_requests_total", "Chat requests", labelnames=("result",))
CHAT_DURATION = Histogram("northstar_chat_duration_seconds", "Verified chat response duration")
RETRIEVAL_DURATION = Histogram("northstar_retrieval_duration_seconds", "Knowledge retrieval duration")
RETRIEVAL_EVIDENCE = Histogram("northstar_retrieval_evidence_count", "Selected evidence count")
MODEL_ERRORS = Counter("northstar_model_errors_total", "Model errors", labelnames=("kind",))
RATE_LIMITED = Counter("northstar_rate_limited_total", "Rate-limited requests", labelnames=("scope",))
OUTBOX_BACKLOG = Gauge("northstar_outbox_backlog", "Unpublished outbox events")
INGESTION_TASKS = Counter(
    "northstar_ingestion_tasks_total", "Ingestion task outcomes", labelnames=("result",)
)
