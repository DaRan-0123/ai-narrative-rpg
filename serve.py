"""
AI 叙事 RPG · 服务版入口

没有界面，只有后台和一个端口。别人可以把这个世界接进自己的程序里：
网页、Bot、自己的客户端——发 JSON，收 JSON。

    python serve.py                      # 只监听本机，http://127.0.0.1:8765
    python serve.py --port 9000
    python serve.py --host 0.0.0.0 --token 一个只有你知道的字符串

端点清单见 docs/API.md。
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from src.server import serve


def main():
    parser = argparse.ArgumentParser(
        description="AI 叙事 RPG 服务版（无界面，HTTP 接口）")
    parser.add_argument("--host", default="127.0.0.1",
                        help="监听地址。默认 127.0.0.1（只有本机能连）；"
                             "要让别的机器连就填 0.0.0.0")
    parser.add_argument("--port", type=int, default=8765, help="监听端口，默认 8765")
    parser.add_argument("--token", default=os.environ.get("AI_RPG_TOKEN", ""),
                        help="访问令牌。设了之后所有请求都要带 "
                             "Authorization: Bearer <token>（也可用环境变量 AI_RPG_TOKEN）")
    args = parser.parse_args()
    serve(host=args.host, port=args.port, token=args.token or None)


if __name__ == "__main__":
    main()
