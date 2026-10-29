# Dynamo source learning notes

Offline Chinese tutorials and deployment notes maintained alongside Dynamo.
The commit message and maintenance instructions use English; tutorial prose remains Chinese.

## Entry points

- [Complete 00–12 module navigation](index.html)
- [00 System architecture and runtime shapes](system-map.html)
- [01 Observability source walkthrough](observability.html)
- [02 Frontend and protocol pipeline](frontend.html)
- [03 Runtime and communications](runtime.html)
- [04 KV-aware routing and scheduling](router.html)
- [05 Engine adapters and Rust Sidecar](backends.html)
- [06 Prefill/decode disaggregation](pd-serving.html)
- [07 KV cache and memory management](kv-cache.html)
- [08 Planner source walkthrough](planner.html)
- [09 Control-plane architecture and observability](control-plane.html)
- [10 Reliability and streaming lifecycle](reliability.html)
- [11 Global routing and pool views](global-routing.html)
- [12 Verification, replay, and performance experiments](validation.html)
- [PD trace capabilities and boundaries](pd-trace.html)
- [Storage and events architecture and observability](storage-events.html)
- [ACK deployment and trace checklist](ack-LB97i2CZNR/index.html)
- [maas-dev historical platform audit](maas-dev-deployment/index.html)

All roadmap modules now have architecture and selected source analysis.
This is not an exhaustive file audit or a claim that every feature was runtime-tested.
Global Rust placeholders, native Sidecar capabilities, and unwired consumers are
identified separately from implemented request paths.

## Evidence and version boundaries

The original observability, Planner, PD Trace, and index source excerpts are pinned to
`2f296b59274867529ea8deef994e6954520024b6`. All other source chapters use
`9734418e5bc71027e6bd04a09314bf1765208c72`; compare revisions before combining claims.
This source revision precedes the learning-documentation commits; source links
therefore avoid documentation-only SHAs. Builders read their pinned Git revision,
independently of the current checkout.
The `*-source-manifest.json` files and `source-manifest.json` retain excerpt paths,
line ranges, revisions, and source hashes; they are part of the documentation.

The user-provided ACK snapshot describes a private SGLang runtime tagged 1.4.1.
It is a separate evidence source from the pinned source analysis and the maas-dev audit.
The ACK YAML fragment is a merge reference, not a complete deployment manifest.
Collector addresses must be confirmed and replaced before use.

## Rebuild

Python 3 and the pinned Git objects are required. No cluster access is needed.

```sh
python3 learning/build_tutorial.py
python3 learning/build_planner.py
python3 learning/build_pd_trace.py
python3 learning/build_control_plane.py
python3 learning/build_storage_events.py
python3 learning/build_reliability.py
python3 learning/build_pd_serving.py
python3 learning/build_remaining.py
python3 learning/maas-dev-deployment/render.py
```

The architecture and lifecycle diagram sources are adjacent `.mmd` files; the chapters
also present their architecture as offline HTML, without an external renderer.

The eight remaining-map chapters and their adjacent architecture diagrams are built
by `build_remaining.py`. Its sources are the pinned Git objects; diagram lane definitions
live in the builder. The other `.mmd` diagrams remain directly maintained.

The HTML walkthroughs and source manifests are generated artifacts. Update their
Python builders and regenerate them instead of editing the outputs directly.
The ACK landing page, deployment snapshot, checklist, and YAML fragment are maintained directly.

## Browser validation

Requires Node.js 22+ and Chrome. Set `CHROME_BIN` if Chrome is not installed at
`/opt/google/chrome/chrome`.

```sh
node learning/validate.mjs observability.html
node learning/validate.mjs planner.html
node learning/validate.mjs pd-trace.html
node learning/validate.mjs control-plane.html
node learning/validate.mjs storage-events.html
node learning/validate.mjs reliability.html
node learning/validate.mjs pd-serving.html
for page in system-map frontend runtime router backends kv-cache global-routing validation; do
  node learning/validate.mjs "$page.html" || exit 1
done
```

The validator checks rendering, source counts, navigation, interactive controls,
and desktop/mobile widths. It removes its temporary Chrome profile and prints
results to stdout instead of retaining reports or screenshots in the repository.
These checks do not run inference, exercise Kubernetes, or prove deployment readiness.

The maas-dev JSON snapshots are inputs to its report renderer. Keep them with
the report. `collect.py` is a separate, explicit live read-only collection tool;
do not run it as part of an offline documentation rebuild.

## Repository hygiene

Keep published documents, reproducible builders, validators, and required evidence.
Do not commit Python caches, browser profiles, screenshots, generated validation
reports, transient graph coverage diagnostics, or superseded drafts.
Keep unpublished skillpack issue drafts local; they are ignored under `maintenance/`.
