# NHI Grafana dashboard memory

## Router request metric

`smg_worker_requests_active` is scraped from the Router (`job="vector_sglang_router"`). It does **not** carry a `model_name` label. Relevant labels include:

- `service_label_nhi_zjlab_io_business_id="nhi-zero2x"`
- `service_label_jobname="<jobname>"`
- `cluster`, `namespace`, `pod`, `worker`, and endpoint metadata

To map a model to this metric, query MySQL `llm_service_business` by `business_id='nhi-zero2x'` and the model's `model_name`, read its `jobname`, then select Router metrics with `service_label_jobname`.

Current zero2x model names:

- `021SFM-236B-Thinking-1030`
- `021SFM-32B-Thinking-1024`

The zero2x water-level numerator uses `smg_worker_requests_active`; capacity remains the model-specific MySQL capacity variable.

## Grafana access

- URL: `http://isg.fed.zhejianglab.org/grafana`
- Dashboard UIDs: `fg3uoxfcwc45ce` (zero2x), `sglang-model-service-waterlevel` (six-model research dashboard)
- Username: `admin`
- Password: do not store in repository files. Supply it at runtime with `GRAFANA_PASSWORD` or use a secret manager.
