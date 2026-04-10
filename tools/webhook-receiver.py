#!/usr/bin/env python3
"""Simple webhook receiver for Kaizen form imports.
Receives JSON POST, saves to form-imports/ directory.
Run: python3 webhook-receiver.py (listens on port 8765)
"""
import json
import os
from datetime import datetime
from http.server import HTTPServer, BaseHTTPRequestHandler

IMPORT_DIR = os.path.join(os.path.dirname(__file__), "form-imports")
os.makedirs(IMPORT_DIR, exist_ok=True)

class Handler(BaseHTTPRequestHandler):
    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length)
        try:
            data = json.loads(body)
            form_count = len(data)
            ts = datetime.now().strftime("%Y-%m-%d-%H%M%S")
            filename = os.path.join(IMPORT_DIR, f"import-{ts}.json")
            with open(filename, "w") as f:
                json.dump(data, f, indent=2)
            print(f"✅ Received {form_count} forms → {filename}")
            response = json.dumps({"success": True, "message": f"Received {form_count} forms. Thank you!"})
            self.send_response(200)
        except Exception as e:
            response = json.dumps({"success": False, "error": str(e)})
            self.send_response(400)
        
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(response.encode())

    def log_message(self, format, *args):
        pass  # Suppress default logging

if __name__ == "__main__":
    port = 8765
    server = HTTPServer(("0.0.0.0", port), Handler)
    print(f"🎯 Form import webhook listening on port {port}")
    print(f"   POST http://localhost:{port}/")
    print(f"   Saving to: {IMPORT_DIR}/")
    server.serve_forever()
