from __future__ import annotations

from celery import Celery  # type: ignore[import-untyped]
from kombu import Exchange, Queue  # type: ignore[import-untyped]

from northstar_api.config import get_settings

settings = get_settings()
jobs_exchange = Exchange("app.jobs.v1", type="direct", durable=True)
dead_letter_exchange = Exchange("app.jobs.dlx", type="direct", durable=True)

celery_app = Celery("northstar", broker=settings.rabbitmq_url, include=["northstar_api.workers.tasks"])
celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    worker_prefetch_multiplier=1,
    broker_connection_retry_on_startup=True,
    broker_transport_options={"confirm_publish": True, "max_retries": 5},
    task_default_exchange=jobs_exchange.name,
    task_default_exchange_type="direct",
    task_default_routing_key="ingest.source",
    task_queues=(
        Queue(
            "ingest.source",
            exchange=jobs_exchange,
            routing_key="ingest.source",
            durable=True,
            queue_arguments={
                "x-dead-letter-exchange": dead_letter_exchange.name,
                "x-dead-letter-routing-key": "ingest.failed",
            },
        ),
        Queue("ingest.failed", exchange=dead_letter_exchange, routing_key="ingest.failed", durable=True),
        Queue("operations", exchange=jobs_exchange, routing_key="operations", durable=True),
        Queue(
            "whatsapp.inbound",
            exchange=jobs_exchange,
            routing_key="whatsapp.inbound",
            durable=True,
            queue_arguments={
                "x-dead-letter-exchange": dead_letter_exchange.name,
                "x-dead-letter-routing-key": "whatsapp.failed",
            },
        ),
        Queue(
            "whatsapp.outbound",
            exchange=jobs_exchange,
            routing_key="whatsapp.outbound",
            durable=True,
            queue_arguments={
                "x-dead-letter-exchange": dead_letter_exchange.name,
                "x-dead-letter-routing-key": "whatsapp.failed",
            },
        ),
        Queue(
            "whatsapp.failed",
            exchange=dead_letter_exchange,
            routing_key="whatsapp.failed",
            durable=True,
        ),
    ),
    task_routes={
        "northstar.ingest_source": {"queue": "ingest.source", "routing_key": "ingest.source"},
        "northstar.process_whatsapp_inbound": {
            "queue": "whatsapp.inbound",
            "routing_key": "whatsapp.inbound",
        },
        "northstar.send_whatsapp_human_reply": {
            "queue": "whatsapp.outbound",
            "routing_key": "whatsapp.outbound",
        },
    },
)


def main() -> None:
    celery_app.worker_main(["worker", "--loglevel=INFO"])
