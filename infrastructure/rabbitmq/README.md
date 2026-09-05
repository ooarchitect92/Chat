# RabbitMQ

RabbitMQ is the command queue used by the Celery workers. Local ports, health checks, persistence, and credentials are defined by the `rabbitmq` service in the root `compose.yaml`.

Production deployments should provide the broker through `CELERY_BROKER_URL` and configure TLS, durable queues, dead-letter handling, monitoring, and high availability.
