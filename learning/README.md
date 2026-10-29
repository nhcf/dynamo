# Dynamo source learning notes

Offline Chinese tutorials and deployment notes maintained alongside Dynamo.
The commit message and maintenance instructions use English; tutorial prose remains Chinese.

## Entry points

- [Learning roadmap](index.html)
- [Observability source walkthrough](observability.html)
- [Planner source walkthrough](planner.html)
- [PD trace capabilities and boundaries](pd-trace.html)
- [ACK deployment and trace checklist](ack-LB97i2CZNR/index.html)
- [maas-dev historical platform audit](maas-dev-deployment/index.html)

## Evidence and version boundaries

Source tutorials are pinned to `2f296b59274867529ea8deef994e6954520024b6`.
Builders read source from that Git revision, independently of the current checkout.
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
python3 learning/maas-dev-deployment/render.py
```

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
