# MOV.AI Metrics Aggregator

A containerized metrics store and query engine based on
[Grafana Mimir](https://grafana.com/oss/mimir/), providing centralized metrics
storage and PromQL querying for the MOV.AI fleet.

## Overview

This service is the metrics counterpart to
[containers-log-aggregator](https://github.com/MOV-AI/containers-log-aggregator):
where that service stores and queries logs with Loki, this one stores and
queries time-series metrics with Mimir. It **replaces InfluxDB** in the MOV.AI
observability stack, so metrics land in a Prometheus-compatible TSDB that
[containers-obs-dashboard](https://github.com/MOV-AI/containers-obs-dashboard)
can query with PromQL alongside its existing Loki datasource.

It runs Mimir in **monolithic (single-binary) mode** with sensible edge
defaults, starts with zero configuration, and exposes its useful settings as
`MIMIR_*` environment variables.

### Key Features

- **Three ingest paths** — Prometheus remote-write, Influx line protocol (so
  existing telegraf configurations work almost unchanged), and OTLP.
- **Zero-configuration start** — single-tenant, local filesystem storage, no
  external dependencies. Runs on a robot out of the box.
- **Object storage ready** — switch to S3 or MinIO with environment variables
  only, no rebuild.
- **Retention built in** — the compactor enforces `MIMIR_RETENTION_PERIOD` and
  reclaims disk automatically.
- **Edge-tuned defaults** — query fan-out, caches and in-flight request limits
  are deliberately set below upstream's cloud-scale defaults.
- **Real readiness probe** — the container healthcheck polls Mimir's `/ready`,
  not just the presence of the binary.

## Architecture

```
                    metrics path (this service)
  ┌──────────┐   Influx line protocol   ┌──────────────────────┐
  │ telegraf │ ───────────────────────► │                      │
  └──────────┘                          │                      │
  ┌──────────┐   Prometheus remote-write│  metrics-aggregator  │   PromQL   ┌───────────────┐
  │ exporters│ ───────────────────────► │       (Mimir)        │ ◄───────── │ obs-dashboard │
  └──────────┘                          │                      │            │   (Grafana)   │
  ┌──────────┐   OTLP                   │  distributor         │            └───────────────┘
  │ OTel     │ ───────────────────────► │  ingester            │                    ▲
  │ collector│                          │  querier / frontend  │                    │
  └──────────┘                          │  store-gateway       │        logs path   │
                                        │  compactor / ruler   │   ┌────────────────┘
                                        └──────────┬───────────┘   │
                                                   │               │
                                          ┌────────▼────────┐  ┌───┴────────────┐
                                          │ /mimir (volume) │  │ log-aggregator │
                                          │  or S3 / MinIO  │  │     (Loki)     │
                                          └─────────────────┘  └────────────────┘
```

All of Mimir's components run in one process (`target: all`). Data is written
to a local volume by default, or to object storage when
`MIMIR_STORAGE_BACKEND=s3`.

## Image Information

| | |
|---|---|
| **Base Image** | `grafana/mimir:3.2.0` (on `gcr.io/distroless/static-debian13`) |
| **Platforms** | `linux/amd64`, `linux/arm64` |
| **Registry Image** | `registry.cloud.mov.ai/qa/metrics-aggregator` |
| **Public Image** | `pubregistry.aws.cloud.mov.ai/ce/metrics-aggregator`, `ghcr.io/mov-ai/ce/metrics-aggregator` |
| **Data volume** | `/mimir` |

> `grafana/mimir` publishes no `linux/arm/v7` image, so unlike the log-agent and
> log-aggregator images this one is amd64 + arm64 only.

## Prerequisites

### Minimum Requirements

| Deployment | CPU | Memory | Disk |
|---|---|---|---|
| Single robot (~5k series) | 1 core | 1 GB | 2 GB |
| Manager node (~50k series) | 2 cores | 4 GB | 20 GB |
| Central (~100k+ series) | 4 cores | 8 GB | object storage |

### Storage Requirements

Roughly **2 bytes per sample** after compaction. 5 000 series scraped every
15 s is about 57 MB/day, so two weeks is ~800 MB. Add up to 13 h of unshipped
local blocks under `/mimir/tsdb`, and `MIMIR_COMPACTOR_DELETION_DELAY` of extra
retention before disk is actually reclaimed.

## Configuration

Configuration is exposed the same way as in `containers-log-aggregator`: the
config file ships inside the image with `${VAR:default}` placeholders, and
Mimir expands them itself at startup via `-config.expand-env=true`. There is no
entrypoint script and no templating step.

> **Syntax warning.** Mimir's placeholder default takes a **single colon** —
> `${MIMIR_X:336h}`. Loki accepts the bash-like `${MIMIR_X:-336h}`; Mimir does
> not, and would read the default as the literal `-336h`. Never copy
> placeholders between `loki-config.yml` and `mimir-config.yml`. Mimir also
> rejects unknown config keys outright. See the header of
> `files/mimir-config.yml`.

### Docker Compose Example

```yaml
services:
  metrics-aggregator:
    image: registry.cloud.mov.ai/qa/metrics-aggregator:latest
    container_name: metrics-aggregator
    hostname: metrics-aggregator
    restart: unless-stopped
    environment:
      MIMIR_RETENTION_PERIOD: 336h
      MIMIR_MAX_GLOBAL_SERIES_PER_USER: 100000
      MIMIR_LOG_LEVEL: warn
    ports:
      - "8080:8080"
    volumes:
      - mimir-data:/mimir

volumes:
  mimir-data:
```

### Environment Variables

Every variable below is declared as an `ENV` in the Dockerfile *and* as an
inline default in `files/mimir-config.yml`, with identical values.
`tests/check_env_defaults.py` enforces that.

#### Deployment and identity

| Variable | Purpose | Default |
|---|---|---|
| `MIMIR_TARGET` | Components to run. `all` is monolithic; alertmanager is **not** included (use `all,alertmanager`) | `all` |
| `MIMIR_MULTITENANCY_ENABLED` | Require an `X-Scope-OrgID` header on every request | `false` |
| `MIMIR_NO_AUTH_TENANT` | Tenant used when multitenancy is off | `anonymous` |

#### Server

| Variable | Purpose | Default |
|---|---|---|
| `MIMIR_HTTP_PORT` | HTTP: ingest, PromQL, `/ready`, `/metrics` | `8080` |
| `MIMIR_GRPC_PORT` | Internal gRPC mesh | `9095` |
| `MIMIR_LOG_LEVEL` | `debug`, `info`, `warn`, `error` | `warn` |
| `MIMIR_LOG_FORMAT` | `logfmt` or `json` | `logfmt` |

#### Storage

| Variable | Purpose | Default |
|---|---|---|
| `MIMIR_STORAGE_BACKEND` | `filesystem`, `s3`, `gcs`, `azure`, `swift` | `filesystem` |
| `MIMIR_S3_ENDPOINT` | S3/MinIO `host:port`, no scheme | *(empty)* |
| `MIMIR_S3_REGION` | S3 region | *(empty)* |
| `MIMIR_S3_BUCKET` | Bucket name | *(empty)* |
| `MIMIR_S3_ACCESS_KEY_ID` | Access key — pass at run time, not declared in the image | *(unset)* |
| `MIMIR_S3_SECRET_ACCESS_KEY` | Secret key — redacted as `********` on `/config` | *(unset)* |
| `MIMIR_S3_INSECURE` | Plain HTTP to the endpoint (MinIO) | `false` |

#### Ingest

| Variable | Purpose | Default |
|---|---|---|
| `MIMIR_INFLUX_ENDPOINT_ENABLED` | Enable `/api/v1/push/influx/write` | `true` |
| `MIMIR_MAX_INFLUX_REQUEST_SIZE` | Max Influx body, bytes | `16777216` |
| `MIMIR_MAX_OTLP_REQUEST_SIZE` | Max OTLP body, bytes | `16777216` |
| `MIMIR_MAX_REMOTE_WRITE_REQUEST_SIZE` | Max remote-write body, bytes | `16777216` |

#### Rings and topology

| Variable | Purpose | Default |
|---|---|---|
| `MIMIR_RING_STORE` | `inmemory`, `memberlist`, `consul`, `etcd` | `inmemory` |
| `MIMIR_REPLICATION_FACTOR` | Ingester and store-gateway replication factor | `1` |
| `MIMIR_MEMBERLIST_PORT` | Gossip port (never bound when `inmemory`) | `7946` |
| `MIMIR_MEMBERLIST_JOIN_MEMBERS` | Comma-separated peers | *(empty)* |

#### Retention and compaction

| Variable | Purpose | Default |
|---|---|---|
| `MIMIR_RETENTION_PERIOD` | **Data retention.** The Loki `retention_period` analogue | `336h` (14 days) |
| `MIMIR_COMPACTION_INTERVAL` | Compaction frequency | `1h` |
| `MIMIR_COMPACTOR_DELETION_DELAY` | Grace period between marking and deleting a block | `2h` |

#### Limits

| Variable | Purpose | Default |
|---|---|---|
| `MIMIR_INGESTION_RATE` | Samples per second | `10000` |
| `MIMIR_INGESTION_BURST_SIZE` | Burst, in samples | `200000` |
| `MIMIR_MAX_GLOBAL_SERIES_PER_USER` | Active series ceiling | `100000` |
| `MIMIR_MAX_LABEL_NAMES_PER_SERIES` | Max labels per series | `30` |
| `MIMIR_OUT_OF_ORDER_TIME_WINDOW` | Out-of-order acceptance window | `10m` |
| `MIMIR_CREATION_GRACE_PERIOD` | Future-timestamp tolerance | `10m` |
| `MIMIR_NATIVE_HISTOGRAMS_ENABLED` | Accept native histograms | `true` |
| `MIMIR_OTEL_METRIC_SUFFIXES_ENABLED` | Add unit suffixes to OTLP metric names | `false` |
| `MIMIR_MAX_FETCHED_CHUNKS_PER_QUERY` | Per-query chunk ceiling | `500000` |

#### Resource tuning

| Variable | Purpose | Default (upstream) |
|---|---|---|
| `MIMIR_QUERIER_MAX_CONCURRENT` | Concurrent queries | `4` (8) |
| `MIMIR_QUERY_SHARDING_TOTAL_SHARDS` | Shards per query | `4` (16) |
| `MIMIR_MAX_QUERY_PARALLELISM` | Parallel subqueries | `4` (14) |
| `MIMIR_BUCKET_STORE_MAX_CONCURRENT` | Concurrent long-term-storage queries | `20` (200) |
| `MIMIR_TSDB_WAL_COMPRESSION` | Snappy-compress the WAL | `true` (false) |

#### Runtime overrides

| Variable | Purpose | Default |
|---|---|---|
| `MIMIR_RUNTIME_CONFIG_FILE` | Hot-reloaded overrides file or URL | *(empty)* |

Anything env expansion cannot express — per-tenant limits, map-valued settings —
goes in a runtime config file, which Mimir re-reads without a restart.

### Object storage (S3 / MinIO)

```yaml
environment:
  MIMIR_STORAGE_BACKEND: s3
  MIMIR_S3_ENDPOINT: minio:9000
  MIMIR_S3_BUCKET: mimir
  MIMIR_S3_ACCESS_KEY_ID: ${S3_ACCESS_KEY}
  MIMIR_S3_SECRET_ACCESS_KEY: ${S3_SECRET_KEY}
  MIMIR_S3_INSECURE: "true"        # MinIO over plain HTTP
```

The bucket must already exist. Blocks, rules and alerts are separated by
prefix (`blocks/`, `ruler/`, `alertmanager/`), so one bucket serves all three.

## Usage

### Docker Run

```bash
docker run -d --name metrics-aggregator \
  -p 8080:8080 \
  -v mimir-data:/mimir \
  -e MIMIR_RETENTION_PERIOD=336h \
  registry.cloud.mov.ai/qa/metrics-aggregator:latest
```

### Kubernetes

```yaml
apiVersion: apps/v1
kind: StatefulSet
metadata:
  name: metrics-aggregator
spec:
  serviceName: metrics-aggregator
  replicas: 1
  selector:
    matchLabels:
      app: metrics-aggregator
  template:
    metadata:
      labels:
        app: metrics-aggregator
    spec:
      containers:
        - name: metrics-aggregator
          image: registry.cloud.mov.ai/qa/metrics-aggregator:latest
          ports:
            - containerPort: 8080
              name: http
          env:
            - name: MIMIR_RETENTION_PERIOD
              value: "336h"
          readinessProbe:
            httpGet:
              path: /ready
              port: http
            initialDelaySeconds: 15
          volumeMounts:
            - name: data
              mountPath: /mimir
          resources:
            requests:
              cpu: "500m"
              memory: 1Gi
            limits:
              cpu: "2"
              memory: 4Gi
  volumeClaimTemplates:
    - metadata:
        name: data
      spec:
        accessModes: ["ReadWriteOnce"]
        resources:
          requests:
            storage: 20Gi
```

> A `StatefulSet`, not a `Deployment`: the ingester's WAL under `/mimir/tsdb`
> must survive a restart. Scaling past one replica additionally requires
> `MIMIR_RING_STORE=memberlist` and `MIMIR_MEMBERLIST_JOIN_MEMBERS`, because
> `inmemory` rings cannot be shared between processes.

## API Reference

All APIs are served on a single port (`8080` by default).

### Ingestion

| Endpoint | Purpose |
|---|---|
| `POST /api/v1/push` | Prometheus remote-write (snappy-compressed protobuf) |
| `POST /api/v1/push/influx/write` | Influx line protocol |
| `POST /otlp/v1/metrics` | OTLP over HTTP |

```bash
# Influx line protocol
curl -X POST http://localhost:8080/api/v1/push/influx/write \
  --data-binary 'cpu,host=robot1,service=spawner usage_idle=42.5'
```

With multitenancy disabled, no `X-Scope-OrgID` header is needed on any path.

### Query

The PromQL API lives under the `/prometheus` prefix, so the Grafana datasource
URL is `http://metrics-aggregator:8080/prometheus`.

| Endpoint | Purpose |
|---|---|
| `GET /prometheus/api/v1/query` | Instant query |
| `GET /prometheus/api/v1/query_range` | Range query |
| `GET /prometheus/api/v1/labels` | Label names |
| `GET /prometheus/api/v1/label/__name__/values` | Metric names |

```bash
curl -sG http://localhost:8080/prometheus/api/v1/query \
  --data-urlencode 'query=cpu_usage_idle'
```

### Status

| Endpoint | Purpose |
|---|---|
| `GET /ready` | Readiness — `200 ready` only when every component is up |
| `GET /metrics` | Mimir's own Prometheus metrics |
| `GET /config` | Effective configuration after env expansion (secrets redacted) |
| `GET /ingester/ring`, `/store-gateway/ring`, `/compactor/ring` | Ring state |

## Monitoring

`/config` is the fastest way to confirm a configuration change actually took
effect, since it shows the post-expansion values.

Key metrics from `/metrics`:

| Metric | Watch for |
|---|---|
| `cortex_ingester_memory_series` | Approaching `MIMIR_MAX_GLOBAL_SERIES_PER_USER` |
| `cortex_discarded_samples_total` | Non-zero — samples being rejected, `reason` says why |
| `cortex_distributor_influx_requests_total` | Influx ingest is arriving |
| `cortex_ingester_tsdb_out_of_order_samples_appended_total` | Out-of-order traffic volume |
| `cortex_compactor_runs_failed_total` | Compaction failing, so retention is not being enforced |
| `cortex_ingester_tsdb_wal_truncations_failed_total` | Disk problems |

```promql
# Are samples being dropped, and why?
sum by (reason) (rate(cortex_discarded_samples_total[5m]))

# Active series against the configured ceiling
cortex_ingester_memory_series
```

## Data Retention

`MIMIR_RETENTION_PERIOD` is enforced by the compactor, not the ingester.
Blocks whose samples are all older than the window are marked for deletion,
then removed after `MIMIR_COMPACTOR_DELETION_DELAY`. Effective on-disk
retention is therefore the sum of the two, plus up to 13 h of local blocks not
yet shipped to the backend.

The query-frontend also clamps queries to the retention window, so a Grafana
panel asking for 30 days with a 14-day retention returns 14 days, not an error.

## Performance Tuning

### Low memory (under 2 GB)

```yaml
environment:
  MIMIR_MAX_GLOBAL_SERIES_PER_USER: 25000
  MIMIR_QUERY_SHARDING_TOTAL_SHARDS: 1     # disables sharding entirely
  MIMIR_MAX_QUERY_PARALLELISM: 1
  MIMIR_QUERIER_MAX_CONCURRENT: 4
  MIMIR_BUCKET_STORE_MAX_CONCURRENT: 4
  MIMIR_MAX_FETCHED_CHUNKS_PER_QUERY: 100000
  MIMIR_NATIVE_HISTOGRAMS_ENABLED: "false"
  MIMIR_MAX_INFLUX_REQUEST_SIZE: 4194304
  MIMIR_RETENTION_PERIOD: 168h
  GOMEMLIMIT: 1200MiB                      # soft Go heap limit
```

### High volume (central deployment)

```yaml
environment:
  MIMIR_STORAGE_BACKEND: s3
  MIMIR_MAX_GLOBAL_SERIES_PER_USER: 1000000
  MIMIR_INGESTION_RATE: 100000
  MIMIR_INGESTION_BURST_SIZE: 2000000
  MIMIR_QUERIER_MAX_CONCURRENT: 16
  MIMIR_QUERY_SHARDING_TOTAL_SHARDS: 16
  MIMIR_BUCKET_STORE_MAX_CONCURRENT: 100
```

## Troubleshooting

### Container exits immediately with a config error

**Debug:** `docker logs metrics-aggregator`

**Solutions:** Mimir rejects unknown config keys, so a typo is fatal. Validate
without starting anything:

```bash
docker run --rm -v "$PWD/files/mimir-config.yml:/c.yml:ro" \
  grafana/mimir:3.2.0 -config.file=/c.yml -config.expand-env=true -modules
```

`-modules` runs the full parse and validation — including the filesystem path
overlap and bucket checks — then exits.

### `field not found` / `cannot overlap with the configured ruler storage`

`blocks_storage.storage_prefix` was removed. Blocks and ruler storage both
inherit `common.storage.filesystem.dir`, and Mimir refuses to start when one
resolves to a subdirectory of the other. Keep `storage_prefix: blocks`.

### A setting has no effect, or a duration went negative

The placeholder probably uses Loki's `${VAR:-default}` form. Mimir splits on
the first colon, so the hyphen ends up inside the value. Check `/config`, and
run `python3 tests/check_env_defaults.py`.

### Samples rejected

**Debug:** `curl -s localhost:8080/metrics | grep cortex_discarded_samples_total`

| `reason` | Fix |
|---|---|
| `sample-out-of-order`, `sample-too-old` | Raise `MIMIR_OUT_OF_ORDER_TIME_WINDOW` |
| `sample-timestamp-too-new` | Raise `MIMIR_CREATION_GRACE_PERIOD`, or fix clock sync |
| `per_user_series_limit` | Raise `MIMIR_MAX_GLOBAL_SERIES_PER_USER`, or reduce label cardinality |
| `rate_limited` | Raise `MIMIR_INGESTION_RATE` / `MIMIR_INGESTION_BURST_SIZE` |
| `max_label_names_per_series` | Raise `MIMIR_MAX_LABEL_NAMES_PER_SERIES`, or drop telegraf tags |

### Telegraf metrics arrive but some fields are missing

The Influx proxy silently discards **string field values**; only float, integer
and boolean are ingested. Convert or drop string fields in telegraf.

### Healthcheck stays unhealthy after changing the HTTP port

The healthcheck URL is baked in at build time and cannot interpolate
`MIMIR_HTTP_PORT`. Override `healthcheck` in compose when changing the port.

## File Structure

```
containers-metrics-aggregator/
├── docker/
│   └── Dockerfile                    # busybox stage + grafana/mimir, ENV defaults, healthcheck
├── files/
│   └── mimir-config.yml              # Mimir config with ${VAR:default} placeholders
├── tests/
│   ├── check_env_defaults.py         # asserts Dockerfile ENV == YAML defaults
│   ├── docker-compose.yml            # local telegraf -> mimir -> grafana stack
│   ├── telegraf/telegraf.conf        # telegraf pointed at the Influx endpoint
│   └── grafana/datasources/mimir.yml # Grafana datasource for Mimir
├── .github/
│   ├── dependabot.yml                # weekly github-actions + docker bumps
│   └── workflows/
│       ├── docker-ci.yml             # calls the shared MOV-AI docker workflow
│       └── autoupdate.yml            # keeps open PRs rebased
├── .bumpversion.toml                 # version, kept in sync with images-manifest.yml
├── images-manifest.yml               # CI build matrix and version
├── LICENSE
└── README.md
```

## Building

```bash
docker build -t registry.cloud.mov.ai/qa/metrics-aggregator:latest \
  -f docker/Dockerfile .
```

Multi-platform:

```bash
docker buildx build --platform linux/amd64,linux/arm64 \
  -t registry.cloud.mov.ai/qa/metrics-aggregator:latest \
  -f docker/Dockerfile .
```

Before committing:

```bash
pre-commit run --all-files          # hadolint, same gate as CI
python3 tests/check_env_defaults.py # ENV/YAML drift check
```

CI is the shared `MOV-AI/.github` docker workflow. A PR into `main` runs
hadolint and builds to `registry.cloud.mov.ai/ci/metrics-aggregator`; a merge
to `main` bumps the patch version, scans with Snyk, publishes to `qa/`, `ce/`
and `ghcr.io/mov-ai/ce/`, tags `v<version>` and creates a GitHub release.

## Security Considerations

1. **No authentication.** Mimir exposes no auth of its own. Do not publish port
   8080 to an untrusted network — put it behind the fleet's reverse proxy.
2. **Anyone who can query can read everything**, and `/config` reveals the
   effective configuration (with secrets redacted).
3. **Credentials.** `MIMIR_S3_ACCESS_KEY_ID` and `MIMIR_S3_SECRET_ACCESS_KEY`
   are deliberately not declared in the image. Pass them at run time via a
   secret store; anything in a container's environment is visible to
   `docker inspect`.
4. **Multi-tenancy is off by default.** Enable
   `MIMIR_MULTITENANCY_ENABLED=true` if tenants must be isolated — it is an
   isolation boundary, not an authentication mechanism.
5. **Cardinality is a denial-of-service vector.** A single high-cardinality
   telegraf tag can exhaust memory; `MIMIR_MAX_GLOBAL_SERIES_PER_USER` and
   `MIMIR_MAX_LABEL_NAMES_PER_SERIES` are the guards.
6. **Upstream phone-home is disabled** (`usage_stats.enabled: false`), not
   overridable.

Hardened run:

```bash
docker run -d --name metrics-aggregator \
  --read-only \
  --tmpfs /tmp \
  --security-opt no-new-privileges \
  --cap-drop ALL \
  -v mimir-data:/mimir \
  -p 127.0.0.1:8080:8080 \
  registry.cloud.mov.ai/qa/metrics-aggregator:latest
```

## Migrating from InfluxDB

This service replaces `containers-influxdb` (and, with Grafana, the
Chronograf-based `containers-monitoring`).

1. **Repoint telegraf.** Change the `[[outputs.influxdb]]` URL to Mimir's
   Influx endpoint — telegraf appends `/write` itself:

   ```toml
   [[outputs.influxdb]]
     urls = ["http://metrics-aggregator:8080/api/v1/push/influx"]
     skip_database_creation = true
   ```

   Moving to `[[outputs.prometheus_remote_write]]` is the longer-term option.

2. **Expect renamed metrics.** The Influx proxy maps
   `<measurement>,<tags> <field>=<value>` to a metric named
   `<measurement>_<field>` (or just `<measurement>` when the field is literally
   `value`), with tags as labels and an extra `__proxy_source__="influx"`
   label. Non-alphanumeric characters become `_`. So `cpu` + `usage_idle`
   becomes `cpu_usage_idle`. **Grafana dashboards must be rewritten** from
   InfluxQL/Flux to PromQL against the new names.

3. **String fields are dropped.** Only numeric and boolean field values are
   ingested, silently. Audit telegraf inputs for string fields that matter.

4. **No historical backfill.** Mimir will not import existing InfluxDB data.
   Run both stores in parallel for one retention period, or accept the gap.

5. **Out-of-order writes.** InfluxDB accepts them freely; Mimir does not by
   default. `MIMIR_OUT_OF_ORDER_TIME_WINDOW` ships at `10m` for exactly this
   reason — raise it if robots buffer for longer while disconnected.

6. **Add the datasource** to `containers-obs-dashboard`
   (`tests/grafana/datasources/mimir.yml` here is the file to copy), pointing at
   `http://metrics-aggregator:8080/prometheus`.

## Related Services

- **[containers-log-agent](https://github.com/MOV-AI/containers-log-agent)** —
  log collection with Fluent Bit.
- **[containers-log-aggregator](https://github.com/MOV-AI/containers-log-aggregator)** —
  log storage and querying with Loki. The structural sibling of this repo.
- **[containers-obs-dashboard](https://github.com/MOV-AI/containers-obs-dashboard)** —
  Grafana, queries both this service and the log aggregator.
- **[containers-telegraf](https://github.com/MOV-AI/containers-telegraf)** —
  metrics collection; the main producer for this service.

## License

See [LICENSE](./LICENSE) — MOV.AI License version 1.0.

## Additional Resources

- [Grafana Mimir documentation](https://grafana.com/docs/mimir/latest/)
- [Mimir configuration reference](https://grafana.com/docs/mimir/latest/references/configuration-parameters/)
- [Mimir architecture](https://grafana.com/docs/mimir/latest/references/architecture/)
- [PromQL reference](https://prometheus.io/docs/prometheus/latest/querying/basics/)
