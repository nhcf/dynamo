-- maas-dev / ClickHouse 25.4 target. Generated design; deployment is separate.
CREATE TABLE llm_observability.llm_traces_kafka_queue ON CLUSTER 'cluster-storage'
(
    `resource_spans` Array(Tuple(
        resource Tuple(
            attributes Array(Tuple(
                key String,
                value Tuple(
                    string_value Nullable(String),
                    bool_value Nullable(UInt8),
                    int_value Nullable(Int64),
                    double_value Nullable(Float64),
                    array_value Nullable(String),
                    kvlist_value Nullable(String),
                    bytes_value Nullable(String)))),
            dropped_attributes_count UInt32),
        scope_spans Array(Tuple(
            scope Tuple(
                name String,
                version String,
                attributes Array(Tuple(
                    key String,
                    value Tuple(
                        string_value Nullable(String),
                        bool_value Nullable(UInt8),
                        int_value Nullable(Int64),
                        double_value Nullable(Float64),
                        array_value Nullable(String),
                        kvlist_value Nullable(String),
                        bytes_value Nullable(String)))),
                dropped_attributes_count UInt32),
            spans Array(Tuple(
                trace_id String,
                span_id String,
                trace_state String,
                parent_span_id String,
                name String,
                kind Int32,
                start_time_unix_nano UInt64,
                end_time_unix_nano UInt64,
                attributes Array(Tuple(
                    key String,
                    value Tuple(
                        string_value Nullable(String),
                        bool_value Nullable(UInt8),
                        int_value Nullable(Int64),
                        double_value Nullable(Float64),
                        array_value Nullable(String),
                        kvlist_value Nullable(String),
                        bytes_value Nullable(String)))),
                dropped_attributes_count UInt32,
                events Array(Tuple(
                    time_unix_nano UInt64,
                    name String,
                    attributes Array(Tuple(
                        key String,
                        value Tuple(
                            string_value Nullable(String),
                            bool_value Nullable(UInt8),
                            int_value Nullable(Int64),
                            double_value Nullable(Float64),
                            array_value Nullable(String),
                            kvlist_value Nullable(String),
                            bytes_value Nullable(String)))),
                    dropped_attributes_count UInt32)),
                dropped_events_count UInt32,
                links Array(Tuple(
                    trace_id String,
                    span_id String,
                    trace_state String,
                    attributes Array(Tuple(
                        key String,
                        value Tuple(
                            string_value Nullable(String),
                            bool_value Nullable(UInt8),
                            int_value Nullable(Int64),
                            double_value Nullable(Float64),
                            array_value Nullable(String),
                            kvlist_value Nullable(String),
                            bytes_value Nullable(String)))),
                    dropped_attributes_count UInt32,
                    flags UInt32)),
                dropped_links_count UInt32,
                status Tuple(
                    message String,
                    code Int32),
                flags UInt32)),
            schema_url String)),
        schema_url String))
)
ENGINE = Kafka
SETTINGS
    kafka_broker_list = '10.107.159.7:9092,10.107.159.8:9092,10.107.159.9:9092,10.107.159.10:9092',
    kafka_topic_list = 'llm-trace-dev',
    kafka_group_name = 'clickhouse-maas-dev-otlp-v1',
    kafka_format = 'ProtobufSingle',
    kafka_schema = 'otel_trace_clickhouse:ExportTraceServiceRequest',
    kafka_num_consumers = 1,
    kafka_thread_per_consumer = 1,
    kafka_skip_broken_messages = 0,
    kafka_handle_error_mode = 'default',
    kafka_commit_every_batch = 0,
    kafka_commit_on_select = 0,
    kafka_max_block_size = 8192;
