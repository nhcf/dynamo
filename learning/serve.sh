#!/usr/bin/env bash
# 启动本地 HTTP 服务用于在浏览器查看 learning 目录下的 HTML 文件。
#
# 用法:
#   ./serve.sh                # 服务 learning 根目录, 端口 8080
#   ./serve.sh 9000           # 指定端口 9000
#   ./serve.sh 8080 daily-report   # 指定端口 + 子目录
#   PORT=9000 ./serve.sh      # 用环境变量指定端口
#
# 说明: 服务根目录默认是整个 learning/, 因此 index.html、各章节页和
#       daily-report/<date>/index.html 都能通过相对路径访问。

set -euo pipefail

# 脚本所在目录即 learning/ 根目录
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

PORT="${1:-${PORT:-8080}}"
SUBDIR="${2:-}"

# 拼接要服务的目录, 并校验存在
SERVE_DIR="$ROOT_DIR${SUBDIR:+/$SUBDIR}"
if [[ ! -d "$SERVE_DIR" ]]; then
  echo "错误: 目录不存在: $SERVE_DIR" >&2
  exit 1
fi

# 端口占用检测
if ss -tlnp 2>/dev/null | grep -q ":${PORT}[[:space:]]"; then
  echo "错误: 端口 ${PORT} 已被占用。请换一个端口, 例如: ./serve.sh 9000" >&2
  echo "占用进程: $(ss -tlnp 2>/dev/null | grep ":${PORT}[[:space:]]" | head -1)" >&2
  exit 1
fi

# 选择可用的 python 解释器
PY="$(command -v python3 || command -v python)"
if [[ -z "$PY" ]]; then
  echo "错误: 未找到 python3 / python" >&2
  exit 1
fi

echo "服务目录 : $SERVE_DIR"
echo "访问地址 : http://localhost:${PORT}/"
echo "局域网   : http://$(hostname -I 2>/dev/null | awk '{print $1}'):${PORT}/  (需端口可达/转发)"
echo "按 Ctrl+C 停止服务"
echo

cd "$SERVE_DIR"
exec "$PY" -m http.server "$PORT" --bind 0.0.0.0
