from http.server import BaseHTTPRequestHandler, HTTPServer
import json
import shlex
import argparse
from urllib.parse import urlparse
import logging
import uuid
from kubernetes import client, config

#config.load_kube_config()
v1 = client.CoreV1Api()

class CustomError(Exception):
    def __init__(self, message, code):
        self.message = message
        self.code = code
        super().__init__(self.message)

class CustomHandler(BaseHTTPRequestHandler):
    def do_POST(self):
        logging.info("do_POST was called")
        run_id = uuid.uuid4()
        body = []
        while True:
            line = self.rfile.readline().decode('utf-8').strip()
            if line == '':  # End of chunk headers
                break
            chunk_size = int(line, 16)
            if chunk_size == 0:
                break
            chunk = self.rfile.read(chunk_size).decode('utf-8')
            body.append(chunk)
            self.rfile.readline()

        data = json.loads(''.join(body))
        logging.info(data)
        logging.info(str(run_id))

        try:
            if data[0] == 'request':
                data = self.handleRequest(data)

            self.send_response(200)
            self.send_header('Content-type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps(data).encode())
        except CustomError as e:
            self.send_response(e.code)
            self.send_header('Content-type', 'text/plain')
            self.end_headers()
            self.wfile.write(e.message.encode('utf-8'))

    def handleRequest(self, data):
        # need to deal with the request args and set post pipelinerun
        note = data['object_attributes']['note']
        args = shlex.split(note)
        if args[0] != 'request':
            raise CustomError('Note doesn\'t start with request', 400)

        parser = argparse.ArgumentParser(prog='/build')
        parser.add_argument('--arch', nargs='*', help='specify architecture(s) to build on')

        response = {}
        #parsed_url = urlparse(data['object_attributes']['url'])

        if '-h' in args or '--help' in args:
            response['mode'] = 'help'
            response['message'] = parser.format_help()
        else:
            args = parser.parse_args(args[1:])

            if args.arch is not None and args.arch:
                response['archs'] = {}
                for arch in ['x86_64', 'aarch64']:
                    response['archs'][arch] = 'true' if arch in args.arch else 'false'

        return response

def run(server_class=HTTPServer, handler_class=CustomHandler, port=8080):
    server_address = ('', port)
    httpd = server_class(server_address, handler_class)
    logging.info(f'Starting httpd on port {port}...')
    httpd.serve_forever()

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    run()
