# Kubernetes

`base/` contains one manifest per independently scalable process. PostgreSQL, Redis, RabbitMQ, Kafka, and S3 are external durable services in production; this base does not create unsafe single-node stateful dependencies.

The base enforces the Restricted Pod Security Standard, default-deny networking, explicit workload identities, bounded resources, disruption budgets, topology spreading, and autoscaling for stateless HTTP workloads. Queue workers intentionally remain fixed in the base; scale them from RabbitMQ queue depth with KEDA or an equivalent external-metrics adapter.

Create `northstar-secrets` from a secure secret manager. Never use the example file as a deployed Secret. Update `overlays/production` with the real domain, object store, Kafka endpoint, and the exact release SHA/digest, then render it:

```shell
kubectl kustomize kubernetes/overlays/production
```

Before the first automated deployment, a cluster administrator must bootstrap the namespace, service account, non-secret configuration, and externally managed `northstar-secrets`. Thereafter the protected deployment workflow owns migration sequencing and workload rollout. Prefer cloud OIDC/workload federation over a long-lived kubeconfig; the generic workflow accepts `KUBE_CONFIG_B64` only because the target cloud provider is not fixed.

Release order is deliberate:

1. Verify the GitHub provenance attestation and vulnerability result for both images.
2. Back up PostgreSQL and test that the backup is restorable.
3. Apply only the migration Job and wait for it to complete.
4. Apply the remaining production overlay and wait for every rollout.
5. Run external health, login, upload, chat, and WhatsApp webhook probes.

Do not deploy directly from a mutable branch tag. Database migrations must be backward compatible with the previous application revision so an application rollback remains safe.
