-- maas-dev / ClickHouse 25.4 target. Generated design; deployment is separate.
CREATE MATERIALIZED VIEW llm_observability.llm_traces_kafka_mv ON CLUSTER 'cluster-storage'
TO llm_observability.llm_traces_otlp_kafka_v1_local
AS
WITH
    (v -> multiIf(
        isNotNull(v.string_value), toJSONString(assumeNotNull(v.string_value)),
        isNotNull(v.bool_value), if(assumeNotNull(v.bool_value) = 0, 'false', 'true'),
        isNotNull(v.int_value), toString(assumeNotNull(v.int_value)),
        isNotNull(v.double_value), if(isFinite(assumeNotNull(v.double_value)), toString(assumeNotNull(v.double_value)), concat('{"_otlp_type":"double","value":', toJSONString(toString(assumeNotNull(v.double_value))), '}')),
        isNotNull(v.array_value), concat('{"_otlp_type":"array","protobuf_base64":', toJSONString(base64Encode(assumeNotNull(v.array_value))), '}'),
        isNotNull(v.kvlist_value), concat('{"_otlp_type":"kvlist","protobuf_base64":', toJSONString(base64Encode(assumeNotNull(v.kvlist_value))), '}'),
        isNotNull(v.bytes_value), concat('{"_otlp_type":"bytes","base64":', toJSONString(base64Encode(assumeNotNull(v.bytes_value))), '}'),
        'null'
    )) AS value_json,
    (aa -> concat('{', arrayStringConcat(arrayMap(a -> concat(toJSONString(a.key), ':', value_json(a.value)), aa), ','), '}')) AS attrs_json,
    rs.resource.attributes AS resource_attrs,
    mapFromArrays(
        arrayMap(a -> a.key, resource_attrs),
        arrayMap(a -> a.value.string_value, resource_attrs)
    ) AS resource_strings,
    (key -> ifNull(resource_strings[key], '')) AS resource_string
SELECT
    lower(hex(s.trace_id)) AS trace_id,
    lower(hex(s.span_id)) AS span_id,
    lower(hex(s.parent_span_id)) AS parent_span_id,
    s.name AS name,
    s.kind AS kind,
    s.start_time_unix_nano AS start_time_unix_nano,
    s.end_time_unix_nano AS end_time_unix_nano,
    'maas-dev' AS cluster_id,
    resource_string('service.name') AS service_name,
    attrs_json(s.attributes) AS attributes,
    attrs_json(resource_attrs) AS resource_attributes,
    attrs_json(ss.scope.attributes) AS scope_attributes,
    ss.scope.name AS scope_name,
    ss.scope.version AS scope_version,
    rs.schema_url AS resource_schema_url,
    ss.schema_url AS scope_schema_url,
    s.trace_state AS trace_state,
    s.flags AS flags,
    s.status.code AS status_code,
    s.status.message AS status_message,
    resource_string('k8s.pod.name') AS pod_name,
    resource_string('k8s.namespace.name') AS pod_namespace,
    resource_string('k8s.node.name') AS node_name,
    resource_string('k8s.deployment.name') AS deployment_name,
    resource_string('k8s.container.name') AS container_name,
    resource_string('app.name') AS app_name,
    resource_string('jobname') AS jobname,
    rs.resource.dropped_attributes_count AS dropped_resource_attributes_count,
    ss.scope.dropped_attributes_count AS dropped_scope_attributes_count,
    s.dropped_attributes_count AS dropped_attributes_count,
    s.dropped_events_count AS dropped_events_count,
    s.dropped_links_count AS dropped_links_count,
    arrayMap(e -> tuple(e.time_unix_nano, e.name, attrs_json(e.attributes), e.dropped_attributes_count), s.events) AS events,
    arrayMap(l -> tuple(lower(hex(l.trace_id)), lower(hex(l.span_id)), l.trace_state, attrs_json(l.attributes), l.dropped_attributes_count, l.flags), s.links) AS links,
    _topic AS kafka_topic,
    toUInt64(_partition) AS kafka_partition,
    toUInt64(_offset) AS kafka_offset,
    toUInt64(ri) AS resource_index,
    toUInt64(si) AS scope_index,
    toUInt64(pi) AS span_index,
    toUInt8(length(s.trace_id) = 16 AND s.trace_id != unhex(repeat('00', 16))
        AND length(s.span_id) = 8 AND s.span_id != unhex(repeat('00', 8))
        AND (length(s.parent_span_id) = 0 OR (length(s.parent_span_id) = 8 AND s.parent_span_id != unhex(repeat('00', 8))))
        AND s.start_time_unix_nano > 0 AND s.start_time_unix_nano <= 9223372036854775807
        AND s.end_time_unix_nano >= s.start_time_unix_nano AND s.end_time_unix_nano <= 9223372036854775807) AS is_valid
FROM llm_observability.llm_traces_kafka_queue
ARRAY JOIN resource_spans AS rs, arrayEnumerate(resource_spans) AS ri
ARRAY JOIN rs.scope_spans AS ss, arrayEnumerate(rs.scope_spans) AS si
ARRAY JOIN ss.spans AS s, arrayEnumerate(ss.spans) AS pi;
