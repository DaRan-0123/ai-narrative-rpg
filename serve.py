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
        description="AI Narrative RPG service (headless, HTTP API)")
    parser.add_argument("--host", default="127.0.0.1",
                        help="Bind address. Defaults to 127.0.0.1 (only this machine can "
                             "connect); use 0.0.0.0 to let other machines connect")
    parser.add_argument("--port", type=int, default=8765, help="Port to listen on. Defaults to 8765")
    parser.add_argument("--token", default=os.environ.get("AI_RPG_TOKEN", ""),
                        help="Access token. Once set, every request must carry "
                             "Authorization: Bearer <token> (can also use the AI_RPG_TOKEN "
                             "environment variable)")
    args = parser.parse_args()
    serve(host=args.host, port=args.port, token=args.token or None)


if __name__ == "__main__":
    main()
