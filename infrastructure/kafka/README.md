# Kafka

Kafka carries durable application events for analytics and object-cleanup consumers. Local listeners, health checks, persistence, and topic bootstrap are defined by the `kafka` services in the root `compose.yaml`.

Production deployments should provide brokers through `KAFKA_BOOTSTRAP_SERVERS` and configure replication, retention, authentication, TLS, and monitoring.
