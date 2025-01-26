from http.server import BaseHTTPRequestHandler, HTTPServer
import json
import logging
import uuid
from kubernetes import client, config
import requests
import yaml

config.load_incluster_config()
#k8s_client = client.ApiClient()

TF_API_URL='https://api.dev.testing-farm.io'
runs = {}

results = '''<?xml version="1.0" encoding="UTF-8"?>
 <testsuites overall-result="passed">
  <properties>
   <property name="baseosci.overall-result" value="passed"/>
  </properties>
  <testsuite name="/kernel-automotive/plans/sst_filesystems/procfs/plan" result="passed" tests="14" stage="complete">
   <logs>
    <log href="http://tf-adapter-demo.apps.zmeya.rh-internal.ocm/v0.1/testing-farm/c7009be9-1b54-4b4a-bad3-be088c6d0b9a/ork-plant9awupgb/arik" name="test log"/>
    <log href="http://tf-adapter-demo.apps.zmeya.rh-internal.ocm/v0.1/testing-farm/c7009be9-1b54-4b4a-bad3-be088c6d0b9a/ork-plant9awupgb" name="workdir"/>
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
        logging.info("do_GET was called")
        path = self.path.split("/")
        run_id = path[3] if len(path) > 3 else None
        if run_id not in runs:
           logging.info("forwarding")
           url = f"{TF_API_URL}{self.path}"
           logging.info(url)
           response = requests.get(url)
           self.send_response(response.status_code)
           self.send_header('Content-type', 'application/json')
           self.end_headers()
           self.wfile.write(response.content)
           return
        endpoint = path[2]
        if endpoint == 'requests':
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
        elif endpoint == 'results':
            response = {}
            self.send_response(200)
            self.send_header('Content-type', 'application/xml')
            self.end_headers()
            self.wfile.write(json.dumps(response).encode('utf-8'))
        elif endpoint == 'testing-farm':
            if path[-1] == 'results.xml':
                self.send_response(200)
                self.send_header('Content-type', 'application/xml')
                self.end_headers()
                self.wfile.write(results.encode('utf-8'))
            elif path[-1] == 'results-junit.xml':
                with open(f"/results/{run_id}/junit.xml", 'rb') as f:
                    data = f.read()
                self.send_response(200)
                self.send_header('Content-type', 'application/xml')
                self.end_headers()
                self.wfile.write(data)
            elif path[-1] == 'arik':
                self.send_response(200)
                self.send_header('Content-type', 'plain/text')
                self.end_headers()
                self.wfile.write('automotive!'.encode('utf-8'))
        else:
            self.send_response(400)


    def do_POST(self):
        global runs
        logging.info("do_POST was called")
        content_length = int(self.headers['Content-Length'])
        post_data = self.rfile.read(content_length)
        data = json.loads(post_data)
        logging.info(self.path)
        #pretty_data = json.dumps(data, indent=4)
        #logging.info(pretty_data)

        if self.path.split("/")[-1] == 'requests' and not 'hardware' in data['environments'][0]:
            try:
                response = self.handleRequest(data)
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

        run_id = uuid.uuid4()
        run_name = 'test-' + str(run_id)
        global runs
        runs[str(run_id)] = 'demo/' + run_name
        gitUrl = data['environments'][0]['variables'].get('CUSTOM_DISCOVER_URL', data['test']['fmf']['url'])

        pipelinerun = {
            'apiVersion': 'tekton.dev/v1',
            'kind': 'PipelineRun',
            'metadata': {'name':run_name, 'namespace': 'demo'},
            'spec': {'params': [
                {'name': 'plan-name', 'value': '^/plans/one'},
                {'name': 'test-name', 'value': 'one'},
                {'name': 'hw-target', 'value': data['environments'][0]['variables']['HW_TARGET']},
                {'name': 'testRunId', 'value': str(run_id)},
                {'name': 'testsRepo', 'value': gitUrl},
                {'name': 'board', 'value': 'rcar-29'},
                {'name': 'skipProvisioning', 'value': 'true'},
                {'name': 'clientName', 'value': 'demo'},
                ],
                'pipelineRef': {'name': 'rcar-s4-test-pipeline'},
                'taskRunTemplate': {'serviceAccountName': 'pipeline'},
                'workspaces': [
                    {'name': 'jumpstarter-client-secret', 'secret': {'secretName': 'demo-config'}},
                    {'name': 'test-results', 'persistentVolumeClaim': {'claimName': 'tmt-results'}},
                ],
            },
        }

        '''
        if 'name' in data['test']['fmf'].keys():
            pipelinerun['spec']['params'].append({'name': 'plan-name', 'value': data['test']['fmf']['name']})
        if 'test_name' in data['test']['fmf'].keys():
            pipelinerun['spec']['params'].append({'name': 'test-name', 'value': data['test']['fmf']['test_name']})
        '''
        
        #output = yaml.dump(pipelinerun, sort_keys=False)
        #logging.info(output)

        api_instance = client.CustomObjectsApi()
        response = api_instance.create_namespaced_custom_object(
            group='tekton.dev',
            version='v1',
            namespace='demo',
            plural='pipelineruns',
            body=pipelinerun,
        )
        logging.info(response)

        # Adding the run UUID to follow the request
        pipelinerun['id'] = str(run_id)
        return pipelinerun

def run(server_class=HTTPServer, handler_class=CustomHandler, port=8080):
    server_address = ('', port)
    httpd = server_class(server_address, handler_class)
    logging.info(f'Starting httpd on port {port}...')
    httpd.serve_forever()

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    run()
