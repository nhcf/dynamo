-- Column contract: opentelemetry-collector-contrib clickhouseexporter v0.120.0.
CREATE TABLE llm_observability.otel_traces_maas_dev_local ON CLUSTER 'cluster-storage'
(
    Timestamp DateTime64(9, 'UTC') CODEC(Delta, ZSTD(1)),
    TraceId String,
    SpanId String,
    ParentSpanId String,
    TraceState String,
    SpanName LowCardinality(String),
    SpanKind LowCardinality(String),
    ServiceName LowCardinality(String),
    ResourceAttributes Map(LowCardinality(String), String),
    ScopeName String,
    ScopeVersion String,
    SpanAttributes Map(LowCardinality(String), String),
    Duration UInt64,
    StatusCode LowCardinality(String),
    StatusMessage String,
    Events Nested(
        Timestamp DateTime64(9),
        Name LowCardinality(String),
        Attributes Map(LowCardinality(String), String)
    ),
    Links Nested(
        TraceId String,
        SpanId String,
        TraceState String,
        Attributes Map(LowCardinality(String), String)
    ),
    ingested_at DateTime64(3, 'UTC') DEFAULT now64(3),
    INDEX idx_trace_id TraceId TYPE bloom_filter(0.001) GRANULARITY 1
)
ENGINE = ReplicatedMergeTree('/clickhouse/tables/llm_observability/{shard}/otel_traces_maas_dev_local', '{replica}')
PARTITION BY toDate(ingested_at)
ORDER BY (ServiceName, Timestamp, TraceId, SpanId)
TTL toDateTime(ingested_at) + INTERVAL 7 DAY;

CREATE TABLE llm_observability.otel_traces_maas_dev ON CLUSTER 'cluster-storage'
AS llm_observability.otel_traces_maas_dev_local
ENGINE = Distributed('cluster-storage', 'llm_observability', 'otel_traces_maas_dev_local', cityHash64(TraceId));

CREATE VIEW llm_observability.otel_traces_maas_dev_grafana ON CLUSTER 'cluster-storage' AS
SELECT
    TraceId AS trace_id,
    SpanId AS span_id,
    ParentSpanId AS parent_span_id,
    toString(SpanName) AS name,
    replaceOne(lowerUTF8(CAST(SpanKind AS String)), 'span_kind_', '') AS span_kind,
    transform(span_kind, ['internal', 'server', 'client', 'producer', 'consumer'], [1, 2, 3, 4, 5], 0) AS kind,
    toUInt64(toUnixTimestamp64Nano(Timestamp)) AS start_time_unix_nano,
    start_time_unix_nano + Duration AS end_time_unix_nano,
    Timestamp AS start_time,
    Duration / 1000000. AS duration_ms,
    'maas-dev' AS cluster_id,
    toString(ServiceName) AS service_name,
    multiIf(lowerUTF8(CAST(StatusCode AS String)) IN ('error', 'status_code_error'), 2,
            lowerUTF8(CAST(StatusCode AS String)) IN ('ok', 'status_code_ok'), 1, 0) AS status_code,
    toUInt8(length(TraceId) = 32 AND length(SpanId) = 16 AND start_time_unix_nano > 0) AS is_valid,
    ingested_at
FROM llm_observability.otel_traces_maas_dev;
