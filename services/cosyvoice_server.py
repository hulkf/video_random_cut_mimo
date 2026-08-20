# -*- coding: utf-8 -*-
import argparse
import json
import os
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class State:
    model = None
    lock = threading.Lock()


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        sys.stdout.write((fmt % args) + "\n")

    def _reply(self, status, payload):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == "/health":
            self._reply(200, {"ready": State.model is not None, "device": str(State.model.model.device)})
        else:
            self._reply(404, {"error": "not found"})

    def do_POST(self):
        if self.path != "/synthesize":
            self._reply(404, {"error": "not found"})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
            output_path = os.path.abspath(payload["output_path"])
            os.makedirs(os.path.dirname(output_path), exist_ok=True)
            chunks = []
            with State.lock:
                for item in State.model.inference_zero_shot(
                    payload["text"], payload["prompt_text"], payload["prompt_wav"],
                    stream=False, speed=float(payload.get("speed", 1.0))
                ):
                    chunks.append(item["tts_speech"].cpu())
            if not chunks:
                raise RuntimeError("模型没有返回音频")
            import torch
            import torchaudio
            torchaudio.save(output_path, torch.cat(chunks, dim=1), State.model.sample_rate)
            self._reply(200, {"success": True, "output_path": output_path})
        except Exception as exc:
            self._reply(500, {"success": False, "error": f"{type(exc).__name__}: {exc}"})


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--model-dir", required=True)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=50051)
    args = parser.parse_args()

    project_dir = os.path.dirname(os.path.dirname(os.path.abspath(args.model_dir)))
    if os.path.isdir(os.path.join(project_dir, "cosyvoice")):
        sys.path.insert(0, project_dir)
        sys.path.insert(0, os.path.join(project_dir, "third_party", "Matcha-TTS"))
    from cosyvoice.cli.cosyvoice import CosyVoice3
    State.model = CosyVoice3(args.model_dir, load_trt=False, load_vllm=False, fp16=False)
    ThreadingHTTPServer((args.host, args.port), Handler).serve_forever()


if __name__ == "__main__":
    main()
