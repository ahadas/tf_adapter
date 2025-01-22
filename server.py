from http.server import BaseHTTPRequestHandler, HTTPServer
import json
import shlex
import argparse
from urllib.parse import urlparse
import logging
import uuid
from kubernetes import client, config
import requests

#config.load_kube_config()
v1 = client.CoreV1Api()

TF_API_URL='https://api.dev.testing-farm.io'
runs = {}

class CustomError(Exception):
    def __init__(self, message, code):
        self.message = message
        self.code = code
        super().__init__(self.message)

class CustomHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        logging.info("do_GET was called")
        run_id = self.path.split("/")[-1]
        if run_id not in runs:
           self.send_response(404)
           self.send_header('Content-type', 'application/json')
           self.end_headers()
           return
        response = {}
        response['state'] = 'complete'
        response['environments_requested'] = []
        response['id'] = run_id
        response['run'] = { 'artifacts': []}
        response['result'] = { 'overall': 'passed' }
        self.send_response(200)
        self.send_header('Content-type', 'application/json')
        self.end_headers()
        self.wfile.write(json.dumps(response).encode('utf-8'))

    def do_POST(self):
        global runs
        logging.info("do_POST was called")
        run_id = uuid.uuid4()
        runs[str(run_id)] = "running"
        content_length = int(self.headers['Content-Length'])
        post_data = self.rfile.read(content_length)
        data = json.loads(post_data)
        logging.info(str(run_id))
        logging.info(self.path)
        #pretty_data = json.dumps(data, indent=4)
        #logging.info(pretty_data)

        if self.path.split("/")[-1] == 'requests' and not 'hardware' in data['environments'][0]:
            try:
                response = self.handleRequest(data)
                response['id'] = str(run_id)
                self.send_response(200)
                self.send_header('Content-type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps(response).encode('utf-8'))
            except CustomError as e:
                self.send_response(e.code)
                self.send_header('Content-type', 'text/plain')
                self.end_headers()
                self.wfile.write(e.message.encode('utf-8'))
        else:
            logging.info("forwarding")
            url = f"{TF_API_URL}{self.path}"
            logging.info(url)
            response = requests.post(url, data=post_data, headers=self.headers)
            self.send_response(response.status_code)
            self.send_header('Content-type', 'application/json')
            self.end_headers()
            self.wfile.write(response.content)

    def handleRequest(self, data):
        logging.info('handling request')
        # need to deal with the request args and set post pipelinerun

        response = {}
        #parsed_url = urlparse(data['object_attributes']['url'])

        return response

def run(server_class=HTTPServer, handler_class=CustomHandler, port=8080):
    server_address = ('', port)
    httpd = server_class(server_address, handler_class)
    logging.info(f'Starting httpd on port {port}...')
    httpd.serve_forever()

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    run()
