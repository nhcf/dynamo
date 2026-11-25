-- Run against stored tables, never SELECT the Kafka queue for routine validation.
SELECT service_name, count() AS stored_rows,
       uniqExact(tuple(cluster_id, trace_id, span_id)) AS unique_spans,
       countIf(is_valid = 0) AS invalid_rows,
       max(ingested_at) AS last_ingested_at
FROM llm_observability.llm_traces_otlp_kafka_v1_all
WHERE ingested_at > now() - INTERVAL 1 HOUR
GROUP BY service_name;

-- Replace the trace_id with the known ID of a test request.
SELECT service_name, scope_name, trace_id, span_id, parent_span_id,
       name, kind, start_time, duration_ms, status_code, status_message
FROM llm_observability.llm_traces_otlp_kafka_v1_all
WHERE trace_id = 'REPLACE_WITH_32_HEX_TRACE_ID'
ORDER BY start_time, span_id;

-- Attribute keys can differ between LiteLLM instrumentation implementations.
SELECT service_name, scope_name,
       groupUniqArrayArray(JSONExtractKeys(attributes)) AS attribute_keys
FROM llm_observability.llm_traces_otlp_kafka_v1_all
WHERE ingested_at > now() - INTERVAL 1 HOUR
  AND service_name IN ('higress-gateway.higress-system', 'litellm')
GROUP BY service_name, scope_name;

-- A replay can duplicate a span; MergeTree does not enforce uniqueness.
SELECT cluster_id, trace_id, span_id, count() AS copies
FROM llm_observability.llm_traces_otlp_kafka_v1_all
WHERE ingested_at > now() - INTERVAL 1 HOUR
GROUP BY cluster_id, trace_id, span_id
HAVING copies > 1
ORDER BY copies DESC
LIMIT 20;

SELECT * FROM system.kafka_consumers
WHERE database = 'llm_observability'
  AND table = 'llm_traces_kafka_queue';
