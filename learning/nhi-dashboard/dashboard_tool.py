# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Validate, export, render, and deploy the NHI Grafana dashboard templates."""

import argparse
import base64
import json
import os
import re
import urllib.error
import urllib.request
from pathlib import Path

KEY_PLACEHOLDER = "__NHI_FILTER_API_KEY__"
KEY_PATTERN = re.compile(r"sk-[A-Za-z0-9_-]+")
DIRECTORY = Path(__file__).resolve().parent


def validate(dashboard):
    """Reject invalid dashboard identities, duplicate panel IDs, and literal keys."""
    if not dashboard.get("uid") or not dashboard.get("title"):
        raise ValueError("Dashboard uid and title are required")
    if KEY_PATTERN.search(json.dumps(dashboard)):
        raise ValueError("Literal API Key found; export a sanitized template first")
    identifiers = set()

    def visit(panels):
        for panel in panels:
            identifier = panel["id"]
            if identifier in identifiers:
                raise ValueError(f"Duplicate panel ID: {identifier}")
            identifiers.add(identifier)
            visit(panel.get("panels", []))

    visit(dashboard["panels"])


def sanitize(dashboard):
    """Replace the single user filter key without changing other Grafana variables."""
    serialized = json.dumps(dashboard, ensure_ascii=False)
    keys = set(KEY_PATTERN.findall(serialized))
    if len(keys) > 1:
        raise ValueError("Multiple API Keys found; cannot map them to one placeholder")
    return json.loads(KEY_PATTERN.sub(KEY_PLACEHOLDER, serialized))


def render(dashboard):
    """Inject the user filter key in memory, leaving datasource variables intact."""
    validate(dashboard)
    serialized = json.dumps(dashboard, ensure_ascii=False)
    if KEY_PLACEHOLDER in serialized:
        key = os.environ["NHI_FILTER_API_KEY"]
        if not KEY_PATTERN.fullmatch(key):
            raise ValueError("NHI_FILTER_API_KEY must be a valid sk- API Key")
        serialized = serialized.replace(KEY_PLACEHOLDER, key)
    return json.loads(serialized)


def write_json(path, value, private=False):
    """Write JSON; rendered files may contain credentials and use mode 0600."""
    if private:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "w") as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
    else:
        Path(path).write_text(
            json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )


def request(path, payload=None):
    """Call Grafana without logging credentials or server response bodies."""
    token = os.environ.get("GRAFANA_TOKEN")
    if token:
        authorization = "Bearer " + token
    else:
        credentials = os.environ["GRAFANA_USER"] + ":" + os.environ["GRAFANA_PASSWORD"]
        authorization = "Basic " + base64.b64encode(credentials.encode()).decode()
    req = urllib.request.Request(
        os.environ["GRAFANA_URL"].rstrip("/") + path,
        data=None if payload is None else json.dumps(payload).encode(),
        headers={"Authorization": authorization, "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as response:
            return json.load(response)
    except urllib.error.HTTPError as error:
        raise RuntimeError(f"Grafana API returned HTTP {error.code}") from None


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    check = commands.add_parser("validate")
    check.add_argument("files", nargs="*", type=Path)
    export = commands.add_parser("export")
    export.add_argument("uid")
    export.add_argument("output", type=Path)
    deployment = commands.add_parser("deploy")
    deployment.add_argument("file", type=Path)
    rendering = commands.add_parser("render")
    rendering.add_argument("file", type=Path)
    rendering.add_argument("output", type=Path)
    args = parser.parse_args()
    if args.command == "validate":
        for path in args.files or sorted(DIRECTORY.glob("*-dashboard.json")):
            validate(json.loads(path.read_text(encoding="utf-8")))
            print(f"Validated {path.name}")
    elif args.command == "export":
        dashboard = sanitize(request("/api/dashboards/uid/" + args.uid)["dashboard"])
        validate(dashboard)
        write_json(args.output, dashboard)
        print(f"Exported {dashboard['uid']} version {dashboard['version']}")
    else:
        if args.command == "render" and args.file.resolve() == args.output.resolve():
            raise ValueError("Render output must not overwrite the template")
        dashboard = render(json.loads(args.file.read_text(encoding="utf-8")))
        if args.command == "render":
            write_json(args.output, dashboard, private=True)
        else:
            current = request("/api/dashboards/uid/" + dashboard["uid"])
            for name in ("id", "version"):
                dashboard[name] = current["dashboard"][name]
            result = request(
                "/api/dashboards/db",
                {
                    "dashboard": dashboard,
                    "folderUid": current["meta"]["folderUid"],
                    "overwrite": False,
                    "message": "Deploy reviewed NHI dashboard template",
                },
            )
            saved = request("/api/dashboards/uid/" + dashboard["uid"])["dashboard"]
            for field in ("panels", "templating", "title", "refresh"):
                if saved.get(field) != dashboard.get(field):
                    raise RuntimeError(f"Deployment readback mismatch: {field}")
            print(f"Deployed {dashboard['uid']} version {result['version']}")


if __name__ == "__main__":
    main()
