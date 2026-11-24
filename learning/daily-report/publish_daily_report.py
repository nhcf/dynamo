#!/usr/bin/env python3
"""Publish the same SLA report with data insights to staging and production."""

import argparse
import hashlib
import json
import os
import subprocess
import sys
from copy import deepcopy
from datetime import date, datetime, timedelta
from pathlib import Path
from tempfile import NamedTemporaryFile

from report_insights import render_insights
from run_report import ROUTE_MODELS, SHANGHAI, InlineReportParser, render

ROOT = Path(__file__).resolve().parent
CONFIG = Path.home() / ".config/dynamo-sla/credentials.json"
STATE = Path.home() / ".local/state/dynamo-sla"
TARGETS = {
    "pre": ("https://pre-zjic.zhejianglab.com", "rsb-1320473654566223872"),
    "prod": ("https://zjic.zhejianglab.com", "rsb-1320818356024680448"),
}


def upload_environment(stage):
    if CONFIG.stat().st_mode & 0o077:
        raise ValueError("发布凭据文件须仅允许当前用户访问")
    credentials = json.loads(CONFIG.read_text(encoding="utf-8"))[stage]
    key = credentials.get("ZJIC_ACCESS_KEY")
    secret = credentials.get("ZJIC_ACCESS_SECRET")
    if not key or not secret:
        raise ValueError(f"{stage} 发布凭据未完整配置")
    return dict(os.environ, ZJIC_ACCESS_KEY=key, ZJIC_ACCESS_SECRET=secret)


def read_report(path, report_date):
    content = path.read_bytes()
    parser = InlineReportParser()
    parser.feed(content.decode("utf-8"))
    data = json.loads("".join(parser.inline))
    models = [row["model"] for row in data["rows"]]
    if (
        data["business_date"] != report_date
        or len(models) != len(set(models))
        or parser.external_assets
    ):
        raise ValueError("日报的业务日期、模型唯一性或独立 HTML 校验失败")
    return data, hashlib.sha256(content).hexdigest()


def add_insights(day, path, data):
    previous_date = (day - timedelta(days=1)).isoformat()
    try:
        previous, _ = read_report(ROOT / previous_date / "index.html", previous_date)
    except FileNotFoundError:
        previous = None
    if (
        previous
        and previous["gateway_key_exclusion_applied"]
        != data["gateway_key_exclusion_applied"]
    ):
        previous = None
    report_date = day.isoformat()
    content = render(deepcopy(data), render_insights(data, previous))
    with NamedTemporaryFile(
        mode="w", encoding="utf-8", dir=path.parent, delete=False
    ) as file:
        temporary = Path(file.name)
        try:
            file.write(content)
            file.flush()
            rendered_data, digest = read_report(temporary, report_date)
            if rendered_data != data:
                raise ValueError("数据洞察渲染改变了日报指标或判定")
            temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)
    return digest


def upload(stage, report_date, path, digest, environment):
    origin, block = TARGETS[stage]
    args = [
        sys.executable,
        "-B",
        str(ROOT / "zjic-report-upload/upload_report.py"),
        "--origin",
        origin,
        "--block-id",
        block,
        "--report-date",
        report_date,
        "--file",
        str(path),
    ]
    validation = json.loads(subprocess.check_output([*args, "--dry-run"], text=True))
    if validation["manifest"]["contentSha256"] != digest:
        raise ValueError("待发布 HTML 在校验期间发生变化")
    process = subprocess.run(args, env=environment, stdout=subprocess.PIPE, text=True)
    result = json.loads(process.stdout)
    if process.returncode or result.get("status") != "uploaded":
        if result.get("outcomeUnknown"):
            raise ValueError(f"{stage} 发布结果未知，需核实远端状态后再重传")
        raise ValueError(
            f"{stage} 发布失败，HTTP {result.get('httpStatus', '未知')} "
            f"业务码 {result.get('code', '未知')}"
        )
    if (
        result["origin"] != origin
        or result["blockId"] != block
        or result["reportDate"] != report_date
        or result["sha256"] != digest
    ):
        raise ValueError("上传回执与目标区块、日期或 HTML 摘要不一致")
    return result


def save_receipt(path, result):
    STATE.mkdir(mode=0o700, parents=True, exist_ok=True)
    with NamedTemporaryFile(
        mode="w", encoding="utf-8", dir=STATE, delete=False
    ) as temporary:
        temp_path = Path(temporary.name)
        try:
            json.dump(result, temporary, ensure_ascii=False)
            temporary.flush()
            temp_path.replace(path)
        finally:
            temp_path.unlink(missing_ok=True)


def publish(stage, day, reuse_existing):
    report_date = day.isoformat()
    path = ROOT / report_date / "index.html"
    receipt = STATE / f"{report_date}.{stage}.json"
    environment = upload_environment(stage)
    if stage == "pre" and not reuse_existing:
        subprocess.run(
            [sys.executable, "-B", str(ROOT / "run_report.py"), "--date", report_date],
            check=True,
        )
    data, digest = read_report(path, report_date)
    if {row["model"] for row in data["rows"]} != set(ROUTE_MODELS.values()):
        raise ValueError("当前日报的模型名单与发布配置不一致")
    if stage == "pre":
        if reuse_existing:
            render(data)
        digest = add_insights(day, path, data)
    else:
        pre_receipt = STATE / f"{report_date}.pre.json"
        previous = json.loads(pre_receipt.read_text(encoding="utf-8"))
        if (
            previous["status"] != "uploaded"
            or (previous["origin"], previous["blockId"]) != TARGETS["pre"]
            or previous["reportDate"] != report_date
            or previous.get("includesDataInsights") is not True
            or previous["sha256"] != digest
        ):
            raise ValueError("正式环境只发布与同日预发回执摘要一致的含数据洞察日报")
    result = upload(stage, report_date, path, digest, environment)
    result["includesDataInsights"] = True
    save_receipt(receipt, result)
    print(json.dumps(result, ensure_ascii=False), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("stage", choices=TARGETS)
    parser.add_argument("--date", type=date.fromisoformat)
    parser.add_argument("--reuse-existing", action="store_true", help="预发使用已生成的日报")
    args = parser.parse_args()
    today = datetime.now(SHANGHAI).date()
    day = args.date or today - timedelta(days=1)
    if day >= today:
        parser.error("请选择已经结束的北京时间自然日")
    if args.reuse_existing and args.stage != "pre":
        parser.error("--reuse-existing 仅用于预发")
    try:
        publish(args.stage, day, args.reuse_existing)
    except FileNotFoundError as error:
        print(f"{args.stage} {day} 缺少所需文件 {error.filename}", file=sys.stderr)
        return 1
    except (OSError, ValueError, KeyError, subprocess.CalledProcessError) as error:
        print(f"{args.stage} {day}: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
