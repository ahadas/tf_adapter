from http.server import BaseHTTPRequestHandler, HTTPServer
import json
import logging
import uuid
from kubernetes import client, config
from kubernetes.client.rest import ApiException
import requests
import os

config.load_incluster_config()

TF_API_URL = os.environ.get("TF_API_URL")
POD_NAMESPACE = os.environ.get("POD_NAMESPACE")
runs = {}

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
                case 'requests':
                    response = self.handle_get_request(run_id)
                    self.send_response(200)
                    self.send_header('Content-type', 'application/json')
                    self.end_headers()
                    self.wfile.write(json.dumps(response).encode('utf-8'))
                case _:
                    self.send_response(400)
        else:
            response = self.forward_get()
            self.send_response(response.status_code)
            self.send_header('Content-type', 'application/json')
            self.end_headers()
            self.wfile.write(response.content)


    def do_POST(self):
        global runs
        logging.info("received a POST request")
        content_length = int(self.headers['Content-Length'])
        post_data = self.rfile.read(content_length)
        data = json.loads(post_data)
        logging.info(self.path)
        #pretty_data = json.dumps(data, indent=4)
        #logging.info(pretty_data)

        if self.path.split("/")[-1] == 'requests' and not 'hardware' in data['environments'][0]:
            try:
                response = self.handle_post_request(data)
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
            response = self.forward_post(post_data)
            self.send_response(response.status_code)
            self.send_header('Content-type', 'application/json')
            self.end_headers()
            self.wfile.write(response.content)

    def forward_get(self):
        url = f"{TF_API_URL}{self.path}"
        logging.info(f"forwarding a GET request to {url}")
        return requests.get(url)

    def forward_post(self, post_data):
        url = f"{TF_API_URL}{self.path}"
        logging.info(f"forwarding a POST request to {url}")
        return requests.post(url, data=post_data, headers=self.headers)

    def handle_get_request(self, run_id):
        response = {}
        response['state'], result = get_state_and_result(run_id)
        response['result'] = {'overall': result}
        response['environments_requested'] = []
        response['id'] = run_id
        response['run'] = {'artifacts': []}
        return response

    def handle_post_request(self, data):
        logging.info('handling request')

        run_id = str(uuid.uuid4())
        run_name = get_run_name(run_id)
        global runs
        runs[run_id] = f"{POD_NAMESPACE}/{run_name}"
        git_url = data['environments'][0]['variables'].get('CUSTOM_DISCOVER_URL', data['test']['fmf']['url'])

        pipelinerun = {
            'apiVersion': 'tekton.dev/v1',
            'kind': 'PipelineRun',
            'metadata': {'name':run_name, 'namespace': POD_NAMESPACE},
            'spec': {
                'params': [
                    {'name': 'plan-name', 'value': '^/plans/' + data['test']['fmf']['name']},
                    {'name': 'test-name', 'value': data['test']['fmf']['name']},
                    {'name': 'hw-target', 'value': data['environments'][0]['arch']},
                    {'name': 'testRunId', 'value': run_id},
                    {'name': 'testsRepo', 'value': git_url},
                    #{'name': 'board', 'value': data['environments'][0]['variables'].get('HW_TARGET', '')},
                    {'name': 'board', 'value': 'rcar-29'},
                    {'name': 'skipProvisioning', 'value': 'true'}, #TODO 
                    {'name': 'clientName', 'value': f"tc-{run_id}"},
                    {'name': 'timeout', 'value': data['settings']['pipeline'].get('timeout', '')}
                    {'name': 'ctx', 'value': dict(data['environments'][0]['tmt']['context'])},
                    {'name': 'env', 'value': dict(data['environments'][0]['tmt']['environment'])},
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
        tmt_image = os.environ.get("TMT_IMAGE")
        if tmt_image:
            pipelinerun['spec']['params'].append({'name': 'tmt-image', 'value': tmt_image})
        
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
        pipelinerun['id'] = run_id
        return pipelinerun

def get_run_name(run_id):
    return f"test-{run_id}"

def fetch_run(run_name):
    try:
        api_instance = client.CustomObjectsApi()
        return api_instance.get_namespaced_custom_object(
            group='tekton.dev',
            version='v1',
            namespace='demo',
            plural='pipelineruns',
            name=run_name
        )
    except ApiException as e:
        print("Exception when calling CustomObjectsApi->get_namespaced_custom_object: %s\n" % e)

def get_state_and_result(run_id):
    runStatus = fetch_run(get_run_name(run_id))
    try:
        conds = runStatus['status'].get(
            'conditions')  # Succeeded -> reasons: PipelineRunPending, Running, Succeeded, Failed, Cancelled, Timeout. Status->True/False/Unknown
        if not conds:
            return 'new', 'unknown'
        else:
            conds = conds[0]
            match conds['reason']:
            # TODO check the exact mappings of the OCP to TF
                case 'PipelineRunPending':
                    return 'queued', 'unknown'
                case 'Running':
                    return 'running', 'unknown'
                case 'Completed':
                    return 'complete', 'passed' if conds['type'] == 'Succeeded' else 'failed'
                case 'Failed' | 'Cancelled' | 'Timeout':
                    return 'complete', 'failed'
    except:
        return 'new', 'unknown'

def run(server_class=HTTPServer, handler_class=CustomHandler, port=8080):
    server_address = ('', port)
    httpd = server_class(server_address, handler_class)
    logging.info(f'Starting httpd on port {port}...')
    httpd.serve_forever()

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    run()
