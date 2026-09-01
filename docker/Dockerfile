# Build stage: Get busybox
# The -uclibc variant is required, NOT the bare busybox:1.37.0 tag. The bare
# tag is the glibc build: it is dynamically linked against libc.so.6 with
# interpreter /lib64/ld-linux-x86-64.so.2, none of which exist in
# gcr.io/distroless/static-debian13 that grafana/mimir is built on, so the
# copied binary could not exec. -uclibc is fully statically linked and
# publishes amd64, arm64 and arm/v7. (The sibling containers-log-aggregator
# gets away with the bare tag only because grafana/loki is Alpine-based.)
FROM busybox:1.38.0-uclibc AS busybox_builder

# Main image
FROM grafana/mimir:3.2.0 AS base

# Copy busybox from builder stage
COPY --from=busybox_builder /bin/busybox /bin/busybox

# Labels
LABEL description="MOV.AI Metrics Aggregator - Grafana Mimir based metrics store and query engine" \
      maintainer="devops@mov.ai" \
      org.opencontainers.image.source="https://github.com/MOV-AI/containers-metrics-aggregator" \
      org.opencontainers.image.documentation="https://github.com/MOV-AI/containers-metrics-aggregator/blob/main/README.md" \
      org.opencontainers.image.vendor="MOV.AI" \
      org.opencontainers.image.title="MOV.AI Metrics Aggregator"

# Configuration Files: YAML Format
# - mimir-config.yml: Configuration for Grafana Mimir with environment variable
#   placeholders. Mimir expands ${VAR:default} syntax natively with the
#   -config.expand-env flag. Note the SINGLE colon - unlike Loki, Mimir does
#   not accept ${VAR:-default}.
COPY files/mimir-config.yml /etc/mimir/mimir-config.yml

# Data root. distroless ships no shell, so exec-form RUN is the only option.
RUN ["/bin/busybox", "mkdir", "-p", "/mimir"]
VOLUME ["/mimir"]

# Ports: 8080 HTTP (all ingest paths, PromQL API, /ready, /metrics),
#        9095 internal gRPC, 7946 memberlist gossip (only bound when
#        MIMIR_RING_STORE=memberlist).
EXPOSE 8080 9095 7946

# Environment Variables
# Every default below MUST match the inline default in files/mimir-config.yml.
# Mimir's default syntax is ${VAR:default} (one colon), not ${VAR:-default}.

# Deployment and identity
ENV MIMIR_TARGET=all
ENV MIMIR_MULTITENANCY_ENABLED=false
ENV MIMIR_NO_AUTH_TENANT=anonymous

# Server
ENV MIMIR_HTTP_PORT=8080
ENV MIMIR_GRPC_PORT=9095
ENV MIMIR_LOG_LEVEL=warn
ENV MIMIR_LOG_FORMAT=logfmt

# Storage: filesystem by default, S3/MinIO inert until an endpoint is set
ENV MIMIR_STORAGE_BACKEND=filesystem
ENV MIMIR_S3_ENDPOINT=""
ENV MIMIR_S3_REGION=""
ENV MIMIR_S3_BUCKET=""
ENV MIMIR_S3_INSECURE=false
# MIMIR_S3_ACCESS_KEY_ID and MIMIR_S3_SECRET_ACCESS_KEY are intentionally NOT
# declared here. mimir-config.yml reads them with the no-default "${VAR}" form,
# so unset and empty behave identically, and declaring them would bake
# credential-shaped variables into the image metadata for every consumer to
# inherit. Pass them at run time.

# Ingest paths
ENV MIMIR_INFLUX_ENDPOINT_ENABLED=true
ENV MIMIR_MAX_INFLUX_REQUEST_SIZE=16777216
ENV MIMIR_MAX_OTLP_REQUEST_SIZE=16777216
ENV MIMIR_MAX_REMOTE_WRITE_REQUEST_SIZE=16777216

# Rings and topology
ENV MIMIR_RING_STORE=inmemory
ENV MIMIR_REPLICATION_FACTOR=1
ENV MIMIR_MEMBERLIST_PORT=7946
ENV MIMIR_MEMBERLIST_JOIN_MEMBERS=""

# Retention and compaction
ENV MIMIR_RETENTION_PERIOD=336h
ENV MIMIR_COMPACTION_INTERVAL=1h
ENV MIMIR_COMPACTOR_DELETION_DELAY=2h

# Limits
ENV MIMIR_INGESTION_RATE=10000
ENV MIMIR_INGESTION_BURST_SIZE=200000
ENV MIMIR_MAX_GLOBAL_SERIES_PER_USER=100000
ENV MIMIR_MAX_LABEL_NAMES_PER_SERIES=30
ENV MIMIR_OUT_OF_ORDER_TIME_WINDOW=10m
ENV MIMIR_CREATION_GRACE_PERIOD=10m
ENV MIMIR_NATIVE_HISTOGRAMS_ENABLED=true
ENV MIMIR_OTEL_METRIC_SUFFIXES_ENABLED=false
ENV MIMIR_MAX_FETCHED_CHUNKS_PER_QUERY=500000

# Resource tuning (edge defaults, deliberately below upstream)
ENV MIMIR_QUERIER_MAX_CONCURRENT=4
ENV MIMIR_QUERY_SHARDING_TOTAL_SHARDS=4
ENV MIMIR_MAX_QUERY_PARALLELISM=4
ENV MIMIR_BUCKET_STORE_MAX_CONCURRENT=20
ENV MIMIR_TSDB_WAL_COMPRESSION=true

# Runtime overrides
ENV MIMIR_RUNTIME_CONFIG_FILE=""

# Real readiness probe rather than the sibling's binary -version check.
# /ready returns 200 only once every service is running and the ingester has
# joined the ring; 503 otherwise, including during graceful shutdown. busybox
# wget exits non-zero on 5xx, which is what Docker needs. distroless has no
# shell, so exec form is mandatory, and that means the port cannot be
# interpolated - override this healthcheck in compose if MIMIR_HTTP_PORT changes.
HEALTHCHECK --interval=30s --timeout=5s --start-period=60s --retries=3 \
  CMD ["/bin/busybox", "wget", "-q", "-T", "3", "-O", "/dev/null", "http://127.0.0.1:8080/ready"]

# Use Mimir's native environment variable expansion with -config.expand-env flag
CMD ["-config.file=/etc/mimir/mimir-config.yml", "-config.expand-env=true"]
