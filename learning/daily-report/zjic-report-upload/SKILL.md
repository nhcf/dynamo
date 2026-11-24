---
name: zjic-report-upload
description: 将生成的 HTML 或包含图片、CSS、JavaScript 的 ZIP 报表上传到智算开放服务平台报表广场的已有区块。用于发布报表或替换指定单日或区间的报表，使用 ZJIC_ACCESS_KEY 和 ZJIC_ACCESS_SECRET 环境变量自动签名。
---

# 报表广场上传

使用技能自带的 [scripts/upload_report.py](scripts/upload_report.py) 上传。只需 Python 3.9+ 标准库，可独立放到 agent-platform 执行环境，无须项目源码。

## 输入与凭据

使用下述生产地址，执行前明确文件路径、已有区块 `blockId` 和统计期次（单日日期或区间起止日期，格式均为 `YYYY-MM-DD`）。业务日期以报表数据所属日期为准；ZIP 包含多个 HTML 时指定入口的包内相对路径。缺失的信息先从当前任务上下文取得，仍缺失再询问。

通过运行进程环境注入以下凭据：

| 环境变量 | 含义 |
|---|---|
| `ZJIC_ACCESS_KEY` | API 账号的 Access Key（AK） |
| `ZJIC_ACCESS_SECRET` | 对应的 Access Secret（SK） |

脚本仅从这两个变量读取凭据；缺失即退出，不进行交互式输入。部署到 agent-platform 时，通过平台的环境变量或密钥注入机制传入脚本所在进程。只检查变量是否存在，不打印其值，也不将凭据写入命令参数、技能文件或报告。

API 账号须具备当前系统的门户管理员或超级管理员角色范围。区块须在当前系统预先创建；上传凭据不等同于网页登录态，不能用它调用网页登录型区块查询接口。

## 上传

用户已要求将该报表上传到指定目标时直接执行。仅生成或整理报表的请求不自动发布。

生产环境 origin 为 `https://zjic.zhejianglab.com`，上传接口为 `https://zjic.zhejianglab.com/apis/control-zjic/reportSquare/upload`。`--origin` 参数使用上述 origin，不带路径或尾部斜杠。

以下命令以**技能目录**为当前目录；从其他位置调用时将脚本路径替换为技能实际路径。将示例中的区块 ID、业务日期和文件路径替换为本次上传目标。区块 ID 可在生产报表广场的区块编辑界面查看和复制。

```bash
python3 scripts/upload_report.py \
  --origin https://zjic.zhejianglab.com \
  --block-id rsb-REPLACE_WITH_BLOCK_ID \
  --report-date YYYY-MM-DD \
  --file /path/to/report.html
```

区间报表同时传开始日和结束日，例如 `--range-start 2026-11-16 --range-end 2026-11-22`。起止相同也可以表示区间。单日报表只传 `--report-date`。区间上传需要目标环境已部署支持区间的接口；如服务端拒绝该字段，保留原参数并报告错误。

上传资源包时，将 `--file` 换成 ZIP 路径，并按需添加 `--entry-file report/index.html`。入口路径按 ZIP 内的真实文件名传入，支持中文，**不要预先 URL 编码**。资源引用使用相对路径，图片、CSS、JS 一起打包；浏览器不会替报表加载外部 CDN 或远端接口数据。

脚本接收 `.html`、`.htm`、`.zip`，默认文件上限 20 MiB；服务端和入口网关还会检查包结构及请求大小。HTTP 413 时先检查包大小，勿原样反复重试。

签名接口按区块、时间类型和完整统计日期确定期次。替换时使用原期次的日期参数并传入新文件；同一期上传成功会直接覆盖，无需传覆盖标志。不同区间可共享结束日，也可和该日的日报共存。对同一期的上传按顺序执行。脚本不自动重试；超时、连接中断或异常响应可能发生在服务端已保存之后，需核实该期次的结果再决定是否重传。

需要先检查输入时，在相同命令后追加 `--dry-run`：只读取本地文件、计算 SHA-256 和签名清单，不读取凭据、不联网，也不表示上传成功。

## 完成与失败

脚本输出 JSON。只有退出码 `0` 且 `status=uploaded` 才表示接口确认成功；`status=validated` 只是本地检查。

成功后向用户报告环境、区块、统计期次、`reportId`、`revision` 及返回的 `viewUrl`。页面仍需要登录和菜单权限；上传成功不能代替浏览器渲染验收。

失败时报告 HTTP 状态、业务错误码和 `requestId`（若有）。401 检查 AK/SK 与签名，403 检查账号角色范围，404 检查区块，400 检查日期、入口及文件内容。`outcomeUnknown=true` 表示结果待核实；保留源文件及其 SHA-256，不自动重传。