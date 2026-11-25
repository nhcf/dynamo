-- Optional query adapter for existing consumers that expect ISO timestamp strings.
-- Match the existing llm_traces_otlp column names and ISO timestamp representation.
-- This is a query adapter, not an INSERT target or a replacement for the old table.
CREATE VIEW llm_observability.llm_traces_otlp_kafka_v1_compat ON CLUSTER 'cluster-storage'
AS SELECT
    trace_id, span_id, parent_span_id, name, kind,
    concat(replaceOne(toString(start_time), ' ', 'T'), 'Z') AS start_time_unix_nano,
    concat(replaceOne(toString(end_time), ' ', 'T'), 'Z') AS end_time_unix_nano,
    start_time, end_time, duration_ms, cluster_id, service_name, attributes,
    pod_name, pod_namespace, node_name, deployment_name, container_name, app_name, jobname
FROM llm_observability.llm_traces_otlp_kafka_v1_all;
