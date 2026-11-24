#!/usr/bin/env python3
"""通过 ZJIC_ACCESS_KEY / ZJIC_ACCESS_SECRET 签名上传 HTML 或 ZIP 报表。"""
import argparse
import base64
import datetime
import hashlib
import http.client
import json
import os
import re
import ssl
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

MAX_BYTES = 20 * 1024 * 1024
UPLOAD_PATH = "/apis/control-zjic/reportSquare/upload"


def validate_origin(value):
    parsed = urllib.parse.urlsplit(value)
    try:
        port = parsed.port
    except ValueError as error:
        raise ValueError("origin 端口无效") from error
    if (
        not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path
        or "?" in value
        or "#" in value
        or "\\" in value
        or any(character.isspace() or ord(character) < 32 for character in value)
        or (port is not None and not 1 <= port <= 65535)
    ):
        raise ValueError("origin 只能包含协议、主机和可选端口")
    if parsed.scheme != "https" and not (
        parsed.scheme == "http" and parsed.hostname in {"localhost", "127.0.0.1", "::1"}
    ):
        raise ValueError("origin 必须使用 HTTPS；仅 localhost 或回环 IP 允许 HTTP")
    return value


class RejectRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, response, code, message, headers, new_url):
        raise urllib.error.HTTPError(
            request.full_url, code, "上传不允许重定向", headers, response
        )


def manifest_for(
    block_id, report_date, file_name, entry_file, content, range_start=None
):
    manifest = {
        "blockId": block_id,
        "fileName": file_name,
        "entryFile": entry_file or "",
        "contentSha256": hashlib.sha256(content).hexdigest(),
    }
    if range_start is not None:
        manifest["rangeStart"] = range_start
        manifest["rangeEnd"] = report_date
    else:
        manifest["reportDate"] = report_date
    return manifest


def signature_for(manifest, secret):
    signed = dict(manifest, secretKey=secret)
    canonical = json.dumps(
        signed, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    # 现有账号服务的签名协议；这里不是密码存储或自行选择哈希算法。
    return "Signature-" + hashlib.md5(canonical.encode("utf-8")).hexdigest()


def parse_date(value, label):
    if value is None or not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", value):
        raise ValueError(f"{label}须为 YYYY-MM-DD")
    try:
        return datetime.date.fromisoformat(value)
    except ValueError as error:
        raise ValueError(f"{label}无效") from error


def read_input(args):
    origin = validate_origin(args.origin)
    if not re.fullmatch(r"rsb-[A-Za-z0-9]{1,60}", args.block_id):
        raise ValueError("blockId 格式错误，应为已有区块的 rsb- 标识")
    if args.range_start is None and args.range_end is None:
        parse_date(args.report_date, "单日业务日期")
    else:
        if (
            args.report_date is not None
            or args.range_start is None
            or args.range_end is None
        ):
            raise ValueError("单日请传 --report-date；区间请同时传 --range-start 和 --range-end")
        if parse_date(args.range_start, "区间开始日期") > parse_date(
            args.range_end, "区间结束日期"
        ):
            raise ValueError("区间开始日期不能晚于结束日期")
    name = args.file.name
    if (
        args.file.suffix.lower() not in {".html", ".htm", ".zip"}
        or len(name) > 255
        or "\\" in name
        or any(ord(c) < 32 or ord(c) == 127 for c in name)
    ):
        raise ValueError("文件须为有效命名的 HTML、HTM 或 ZIP")
    if args.entry_file:
        if args.file.suffix.lower() != ".zip":
            raise ValueError("entry-file 仅用于 ZIP")
        parts = args.entry_file.split("/")
        if (
            len(args.entry_file) > 1024
            or "\\" in args.entry_file
            or ":" in args.entry_file
            or any(p in {"", ".", ".."} for p in parts)
            or any(ord(c) < 32 or ord(c) == 127 for c in args.entry_file)
            or Path(args.entry_file).suffix.lower() not in {".html", ".htm"}
        ):
            raise ValueError("entry-file 须为 ZIP 内 HTML 的有效相对路径")
    if not args.file.is_file():
        raise ValueError("报表文件不存在或不是普通文件")
    with args.file.open("rb") as stream:
        content = stream.read(MAX_BYTES + 1)
    if not content or len(content) > MAX_BYTES:
        raise ValueError("文件须非空且不超过 20 MiB")
    return origin, content


def credentials():
    names = ("ZJIC_ACCESS_KEY", "ZJIC_ACCESS_SECRET")
    missing = [name for name in names if not os.environ.get(name)]
    if missing:
        raise ValueError("缺少环境变量：" + ", ".join(missing))
    key, secret = (os.environ[name] for name in names)
    if any(ord(c) < 33 or ord(c) > 126 for c in key):
        raise ValueError("ZJIC_ACCESS_KEY 含无效的请求头字符")
    return key, secret


def emit(data, sensitive=()):
    def clean(value):
        if isinstance(value, str):
            for secret in sorted((x for x in sensitive if x), key=len, reverse=True):
                value = value.replace(secret, "[REDACTED]")
            return value[:2000]
        if isinstance(value, dict):
            return {key: clean(item) for key, item in value.items()}
        if isinstance(value, list):
            return [clean(item) for item in value]
        return value

    print(json.dumps(clean(data), ensure_ascii=False))


def response_error(status, raw):
    result = {
        "status": "failed",
        "httpStatus": status,
        "outcomeUnknown": status >= 500 or 200 <= status < 300,
    }
    try:
        data = json.loads(raw)
        if not isinstance(data, dict):
            raise ValueError("invalid response")
        for key in ("code", "message", "requestId"):
            if isinstance(data.get(key), (str, int)):
                result[key] = str(data[key])
    except (ValueError, UnicodeError):
        result["message"] = "服务器返回非 JSON 响应，请检查入口和服务状态"
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--origin", required=True, help="系统 origin，不带路径或尾部斜杠")
    parser.add_argument("--block-id", required=True, help="已有区块 ID")
    parser.add_argument("--report-date", help="单日业务日期 YYYY-MM-DD")
    parser.add_argument("--range-start", help="区间开始日期 YYYY-MM-DD，与 --range-end 配对")
    parser.add_argument("--range-end", help="区间结束日期 YYYY-MM-DD，与 --range-start 配对")
    parser.add_argument("--file", type=Path, required=True, help="HTML、HTM 或 ZIP 文件")
    parser.add_argument("--entry-file", default="", help="ZIP 内入口相对路径，不做 URL 编码")
    parser.add_argument("--dry-run", action="store_true", help="仅本地检查，不读取凭据、不联网")
    args = parser.parse_args(argv)
    try:
        origin, content = read_input(args)
        end_date = args.range_end or args.report_date
        manifest = manifest_for(
            args.block_id,
            end_date,
            args.file.name,
            args.entry_file,
            content,
            args.range_start,
        )
        if args.dry_run:
            emit(
                {
                    "status": "validated",
                    "endpoint": origin + UPLOAD_PATH,
                    "bytes": len(content),
                    "manifest": manifest,
                }
            )
            return 0
        key, secret = credentials()
    except (ValueError, OSError) as error:
        emit({"status": "failed", "message": str(error), "outcomeUnknown": False})
        return 2

    signature = signature_for(manifest, secret)
    sensitive = (key, secret, signature)
    body = {key: value for key, value in manifest.items() if key != "contentSha256"}
    body["contentBase64"] = base64.b64encode(content).decode("ascii")
    request = urllib.request.Request(
        origin + UPLOAD_PATH,
        data=json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode(
            "utf-8"
        ),
        headers={
            "Content-Type": "application/json",
            "X-API-Key": key,
            "Authorization": signature,
        },
        method="POST",
    )
    try:
        # 标准系统 CA 验证；企业 CA 可通过 SSL_CERT_FILE 注入。
        opener = urllib.request.build_opener(
            RejectRedirects(),
            urllib.request.HTTPSHandler(context=ssl.create_default_context()),
        )
        with opener.open(request, timeout=120) as response:
            status, raw = response.status, response.read(65537)
    except urllib.error.HTTPError as error:
        try:
            raw = error.read(65537)
        except (OSError, http.client.HTTPException):
            raw = b""
        emit(response_error(error.code, raw), sensitive)
        return 1
    except (urllib.error.URLError, OSError, ValueError, http.client.HTTPException):
        emit(
            {
                "status": "failed",
                "message": "连接、TLS 或响应读取失败；核实目标日期是否已保存后再决定重传",
                "outcomeUnknown": True,
            },
            sensitive,
        )
        return 1
    try:
        response = json.loads(raw)
        if not isinstance(response, dict) or "code" not in response:
            raise ValueError("invalid response")
        if str(response.get("code")) != "200" or response.get("success") is False:
            emit(response_error(status, raw), sensitive)
            return 1
        edition = response.get("data")
        if (
            not isinstance(edition, dict)
            or not edition.get("id")
            or not edition.get("revision")
            or edition.get("reportDate") != end_date
        ):
            raise ValueError("missing report identity")
        period = edition.get("period")
        expected_period = {
            "type": "range" if args.range_start else "day",
            "start": args.range_start or end_date,
            "end": end_date,
        }
        if args.range_start or period is not None:
            if not isinstance(period, dict) or any(
                period.get(k) != v for k, v in expected_period.items()
            ):
                raise ValueError("unexpected report period")
    except (ValueError, UnicodeError):
        emit(
            {
                "status": "failed",
                "httpStatus": status,
                "message": "响应未包含有效的上传结果，需核实是否已保存",
                "outcomeUnknown": True,
            },
            sensitive,
        )
        return 1
    query = urllib.parse.urlencode(
        {
            "report": args.block_id,
            "date": edition["id"] if args.range_start else end_date,
        }
    )
    emit(
        {
            "status": "uploaded",
            "httpStatus": status,
            "origin": origin,
            "blockId": args.block_id,
            "period": expected_period,
            "reportId": edition["id"],
            "reportDate": edition["reportDate"],
            "revision": edition["revision"],
            "bytes": len(content),
            "sha256": manifest["contentSha256"],
            "viewUrl": origin + "/control/dashboard/dataReport/reportSquare?" + query,
        },
        sensitive,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
