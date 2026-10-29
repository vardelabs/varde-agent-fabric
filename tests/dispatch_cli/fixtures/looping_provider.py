"""An OpenAI-compatible endpoint that answers every turn offered tools with
another tool call, so a review never stops exploring by itself. A turn with no
tools gets a work summary, as a model answers OpenCode's forced step-limit
turn, unless it asks for the review findings; in "stubborn" mode it never
writes findings. It logs each request's turn kind, one per line, to the file
named on the command line."""
import http.server
import json
import sys

FINDINGS_ASK = "review findings now"
FINDINGS = '[{"severity":"minor","title":"Example","path":"a.txt","line":"1"}]'
SUMMARY = "Maximum steps reached. Summary of work so far: read the files."


def turn_kind(body):
    if body.get("tools") and body.get("tool_choice") != "none":
        return "tools"
    users = [message for message in body.get("messages", []) if message.get("role") == "user"]
    asks = bool(users) and FINDINGS_ASK in json.dumps(users[-1])
    return "findings" if asks and sys.argv[2:] != ["stubborn"] else "summary"


class LoopingProvider(http.server.BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass

    def do_POST(self):
        kind = turn_kind(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
        with open(sys.argv[1], "a") as log:
            log.write(kind + "\n")
        if kind == "tools":
            call = {"index": 0, "id": "call", "type": "function",
                    "function": {"name": "glob", "arguments": json.dumps({"pattern": "*"})}}
            delta, finish = {"role": "assistant", "tool_calls": [call]}, "tool_calls"
        else:
            delta, finish = {"role": "assistant", "content": FINDINGS if kind == "findings" else SUMMARY}, "stop"
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.end_headers()
        for chunk_delta, chunk_finish in ((delta, None), ({}, finish)):
            chunk = {"id": "c", "object": "chat.completion.chunk", "created": 0, "model": "loop",
                     "choices": [{"index": 0, "delta": chunk_delta, "finish_reason": chunk_finish}]}
            self.wfile.write(b"data: " + json.dumps(chunk).encode() + b"\n\n")
        self.wfile.write(b"data: [DONE]\n\n")


server = http.server.HTTPServer(("127.0.0.1", 0), LoopingProvider)
print(server.server_address[1], flush=True)
server.serve_forever()
