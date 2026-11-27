-- maas-dev / ClickHouse 25.4 target. Generated design; deployment is separate.
CREATE TABLE llm_observability.llm_traces_otlp_kafka_v1_local ON CLUSTER 'cluster-storage'
(
    trace_id String,
    span_id String,
    parent_span_id String,
    name String,
    kind Int32,
    start_time_unix_nano UInt64,
    end_time_unix_nano UInt64,
    start_time DateTime64(9, 'UTC') MATERIALIZED fromUnixTimestamp64Nano(toInt64(least(start_time_unix_nano, 9223372036854775807))),
    end_time DateTime64(9, 'UTC') MATERIALIZED fromUnixTimestamp64Nano(toInt64(least(end_time_unix_nano, 9223372036854775807))),
    duration_ms Nullable(Float64) MATERIALIZED if(end_time_unix_nano >= start_time_unix_nano, (end_time_unix_nano - start_time_unix_nano) / 1000000., NULL),
    cluster_id LowCardinality(String),
    service_name LowCardinality(String),
    attributes String CODEC(ZSTD(1)),
    resource_attributes String CODEC(ZSTD(1)),
    scope_attributes String CODEC(ZSTD(1)),
    scope_name LowCardinality(String),
    scope_version String,
    resource_schema_url String,
    scope_schema_url String,
    trace_state String,
    flags UInt32,
    status_code Int32,
    status_message String,
    pod_name String,
    pod_namespace String,
    node_name String,
    deployment_name String,
    container_name String,
    app_name String,
    jobname String,
    dropped_resource_attributes_count UInt32,
    dropped_scope_attributes_count UInt32,
    dropped_attributes_count UInt32,
    dropped_events_count UInt32,
    dropped_links_count UInt32,
    events Array(Tuple(time_unix_nano UInt64, name String, attributes String, dropped_attributes_count UInt32)),
    links Array(Tuple(trace_id String, span_id String, trace_state String, attributes String, dropped_attributes_count UInt32, flags UInt32)),
    kafka_topic LowCardinality(String),
    kafka_partition UInt64,
    kafka_offset UInt64,
    resource_index UInt64,
    scope_index UInt64,
    span_index UInt64,
    is_valid UInt8,
    ingested_at DateTime64(3, 'UTC') DEFAULT now64(3),
    INDEX idx_service_name service_name TYPE bloom_filter GRANULARITY 4,
    INDEX idx_trace_id trace_id TYPE bloom_filter(0.01) GRANULARITY 1,
    INDEX idx_kind kind TYPE minmax GRANULARITY 4,
    INDEX idx_duration_ms duration_ms TYPE minmax GRANULARITY 4
)
ENGINE = ReplicatedMergeTree('/clickhouse/tables/llm_observability/{shard}/llm_traces_otlp_kafka_v1_local', '{replica}')
PARTITION BY toYYYYMMDD(ingested_at)
PRIMARY KEY (cluster_id, service_name, start_time)
ORDER BY (cluster_id, service_name, start_time, trace_id, span_id)
TTL toDateTime(ingested_at) + INTERVAL 7 DAY
SETTINGS index_granularity = 8192;

CREATE TABLE llm_observability.llm_traces_otlp_kafka_v1_all ON CLUSTER 'cluster-storage'
AS llm_observability.llm_traces_otlp_kafka_v1_local
ENGINE = Distributed('cluster-storage', 'llm_observability', 'llm_traces_otlp_kafka_v1_local', cityHash64(trace_id));
