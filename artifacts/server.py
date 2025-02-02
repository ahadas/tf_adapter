from http.server import BaseHTTPRequestHandler, HTTPServer
import json
import logging
import uuid
import requests
import os

TF_API_URL = os.environ.get("TF_API_URL")
runs = {}

results = '''<?xml version="1.0" encoding="UTF-8"?>
 <testsuites overall-result="passed">
  <properties>
   <property name="baseosci.overall-result" value="passed"/>
  </properties>
  <testsuite name="/kernel-automotive/plans/sst_filesystems/procfs/plan" result="passed" tests="14" stage="complete">
   <logs>
    <log href="https://artifacts.osci.redhat.com/{0}" name="workdir"/>
   </logs>
  </testsuite>
 </testsuites>'''

class CustomError(Exception):
    def __init__(self, message, code):
        self.message = message
        self.code = code
        super().__init__(self.message)

class CustomHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        logging.info("received a GET request")
        path = self.path.split("/")
        run_id = path[3] if len(path) > 3 else None
        if run_id in runs:
            endpoint = path[2]
            match endpoint:
                case 'results':
                    self.send_response(200)
                    self.send_header('Content-type', 'application/xml')
                    self.end_headers()
                    self.wfile.write(json.dumps({}).encode('utf-8'))
                case 'testing-farm':
                    if path[-1] == 'results.xml':
                        self.send_response(200)
                        self.send_header('Content-type', 'application/xml')
                        self.end_headers()
                        out = results.format(run_id)
                        self.wfile.write(out.encode('utf-8'))
                    elif path[-1] == 'results-junit.xml':
                        with open(f"/results/{run_id}/junit.xml", 'rb') as f:
                            data = f.read()
                        self.send_response(200)
                        self.send_header('Content-type', 'application/xml')
                        self.end_headers()
                        self.wfile.write(data)
                case _:
                    self.send_response(400)
        else:
            response = self.forward_get()
            self.send_response(response.status_code)
            self.send_header('Content-type', 'application/json')
            self.end_headers()
            self.wfile.write(response.content)

    def forward_get(self):
        url = f"{TF_API_URL}{self.path}"
        logging.info(f"forwarding a GET request to {url}")
        return requests.get(url)

def run(server_class=HTTPServer, handler_class=CustomHandler, port=8080):
    server_address = ('', port)
    httpd = server_class(server_address, handler_class)
    logging.info(f'Starting httpd on port {port}...')
    httpd.serve_forever()

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    run()
