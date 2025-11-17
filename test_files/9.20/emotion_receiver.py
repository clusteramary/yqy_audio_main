#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import argparse
import json
import socket
import sys
import time
from datetime import datetime


def parse_args():
    ap = argparse.ArgumentParser(
        description="接收 FacePromptDetector 持续推送的 UDP 情绪 JSON 并打印/保存。"
    )
    ap.add_argument("--host", default="0.0.0.0", help="监听地址（默认 0.0.0.0）")
    ap.add_argument("--port", type=int, default=5555, help="监听端口（默认 5555）")
    ap.add_argument("--buf", type=int, default=65535, help="UDP 接收缓冲区大小")
    ap.add_argument("--top", type=int, default=3, help="显示 Top-K 情绪（默认 3）")
    ap.add_argument("--csv", default="", help="保存到 CSV 文件路径（可选）")
    return ap.parse_args()

def ensure_csv_header(path: str):
    try:
        with open(path, "x", encoding="utf-8") as f:
            f.write("iso_time,epoch_ts,dominant_emotion,has_face,emotions_json\n")
    except FileExistsError:
        pass

def dump_csv(path: str, payload: dict):
    iso_time = datetime.fromtimestamp(payload.get("ts", time.time())).isoformat()
    row = [
        iso_time,
        str(payload.get("ts", "")),
        str(payload.get("dominant_emotion", "")),
        str(payload.get("has_face", "")),
        json.dumps(payload.get("emotion", {}), ensure_ascii=False),
    ]
    with open(path, "a", encoding="utf-8") as f:
        f.write(",".join(x.replace(",", "，")) + "\n")

def fmt_topk(emotion_dict: dict, k: int):
    if not emotion_dict:
        return "(无)"
    items = sorted(emotion_dict.items(), key=lambda kv: kv[1], reverse=True)[:k]
    return " | ".join(f"{k}:{v:.1f}%" for k, v in items)

def main():
    args = parse_args()
    addr = (args.host, args.port)

    if args.csv:
        ensure_csv_header(args.csv)

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    # 绑定监听地址
    try:
        sock.bind(addr)
    except OSError as e:
        print(f"[ERR] 绑定 {addr} 失败：{e}", file=sys.stderr)
        sys.exit(1)

    print(f"[OK] 监听 udp://{args.host}:{args.port}，Ctrl+C 退出。")
    try:
        while True:
            data, src = sock.recvfrom(args.buf)
            now = datetime.now().strftime("%H:%M:%S")
            try:
                payload = json.loads(data.decode("utf-8", errors="ignore"))
            except json.JSONDecodeError:
                print(f"[{now}] 来自 {src} 非法 JSON：{data[:80]!r} ...")
                continue

            ts = payload.get("ts", time.time())
            t_str = datetime.fromtimestamp(ts).strftime("%H:%M:%S")
            has_face = payload.get("has_face", False)
            dom = payload.get("dominant_emotion", None)
            emos = payload.get("emotion", {})

            print(
                f"[{t_str}] has_face={has_face}  dominant={dom}  top{args.top}: {fmt_topk(emos, args.top)}"
            )

            if args.csv:
                dump_csv(args.csv, payload)

    except KeyboardInterrupt:
        print("\n[EXIT] 用户中断，退出。")
    finally:
        try:
            sock.close()
        except Exception:
            pass

if __name__ == "__main__":
    main()