"""An OpenAI-compatible endpoint that answers every turn offered tools with
another tool call, so a review never stops exploring by itself. It logs each
request's turn kind, one line each, to the file named on the command line."""
import http.server
import json
import sys


class LoopingProvider(http.server.BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        offers_tools = bool(body.get("tools")) and body.get("tool_choice") != "none"
        with open(sys.argv[1], "a") as log:
            log.write(("tools" if offers_tools else "text") + "\n")
        if offers_tools:
            call = {"index": 0, "id": "call", "type": "function",
                    "function": {"name": "glob", "arguments": json.dumps({"pattern": "*"})}}
            chunks = [{"role": "assistant", "tool_calls": [call]}], "tool_calls"
        else:
            chunks = [{"role": "assistant", "content": "[]"}], "stop"
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.end_headers()
        for delta, finish in ((chunks[0][0], None), ({}, chunks[1])):
            chunk = {"id": "c", "object": "chat.completion.chunk", "created": 0, "model": "loop",
                     "choices": [{"index": 0, "delta": delta, "finish_reason": finish}]}
            self.wfile.write(b"data: " + json.dumps(chunk).encode() + b"\n\n")
        self.wfile.write(b"data: [DONE]\n\n")


server = http.server.HTTPServer(("127.0.0.1", 0), LoopingProvider)
print(server.server_address[1], flush=True)
server.serve_forever()
