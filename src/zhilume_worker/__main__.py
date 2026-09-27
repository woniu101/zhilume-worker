"""Worker service entry point. Never calls back into Server."""
import argparse
import logging
import os
import platform
from pathlib import Path
import uvicorn
from .gateway import create_app, identity


def main():
    parser = argparse.ArgumentParser(description="Zhilume Worker 接入服务")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=4320)
    parser.add_argument("--name", default=platform.node())
    parser.add_argument("--state", default=".state")
    parser.add_argument("--show-token", action="store_true", help="显示本机 Worker 接入密钥并退出")
    parser.add_argument("--delay", type=float, default=3)
    parser.add_argument("--upload-limit", type=int, default=1024 ** 3)
    parser.add_argument("--comfy-config")
    parser.add_argument("--enable-image-execution", action="store_true")
    parser.add_argument("--speech-config")
    parser.add_argument("--enable-speech-execution", action="store_true")
    parser.add_argument("--video-config")
    parser.add_argument("--enable-video-execution", action="store_true")
    args = parser.parse_args()
    if args.enable_video_execution and not args.video_config:
        parser.error("启用视频执行需要 --video-config")
    if args.enable_speech_execution and not args.speech_config:
        parser.error("启用语音执行需要 --speech-config")
    if args.enable_image_execution and not args.comfy_config:
        parser.error("启用图片执行需要 --comfy-config")
    if args.delay < 0 or not 0 <= args.port <= 65535 or args.upload_limit <= 0:
        parser.error("端口、delay 或上传限制无效")
    os.umask(0o077)
    if args.show_token:
        print(identity(Path(args.state))["credential"])
        return
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    uvicorn.run(create_app(args), host=args.host, port=args.port, access_log=False, ws_max_size=256 * 1024, timeout_graceful_shutdown=15)


if __name__ == "__main__":
    main()
