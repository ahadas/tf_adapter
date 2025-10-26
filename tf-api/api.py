from http.server import BaseHTTPRequestHandler, HTTPServer
import json
import logging
import uuid
from kubernetes import client, config
from kubernetes.client.rest import ApiException
import requests
import os

config.load_incluster_config()

POD_NAMESPACE = os.environ.get("POD_NAMESPACE")
EXPORTERS_NAMESPACE = os.environ.get("EXPORTERS_NAMESPACE", POD_NAMESPACE)

# Environment variables
BOARD = "BOARD"
BOARD_TYPE = "BOARD-TYPE"
PIPELINE = "PIPELINE"
TMT_IMAGE = "TMT_IMAGE"
SKIP_PROVISIONING = "SKIP_PROVISIONING"
TIMEOUT = "TIMEOUT"

# Board types
RCAR_S4_TYPE = "rcar_s4"
RIDE_SX4_TYPE = "qc8775"
J784S4EVM_TYPE = "j784s4evm"

class CustomError(Exception):
    def __init__(self, message, code):
        self.message = message
        self.code = code
        super().__init__(self.message)

class CustomHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        logging.info("received a GET request")
        path = self.path.split("/")
        if len(path) < 3:
            logging.error(f"received an invalid GET request: {self.path}")
            self.send_response(400)
            return
        match path[2]:
            case 'requests':
                if len(path) < 4:
                    logging.error(f"received an invalid GET request: {self.path} (missing request id)")
                    self.send_response(400)
                    return
                run_id = path[3]
                response = self.handle_get_request(run_id)
                self.send_response(200)
                self.send_header('Content-type', 'application/json')
                self.send_header('Access-Control-Allow-Origin', '*')
                self.end_headers()
                self.wfile.write(json.dumps(response).encode('utf-8'))
            case 'inventory':
                match path[1]:
                    case 'j784s4evm':
                        response = self.handle_get_ti_784()
                    case 'rcar_s4':
                        response = self.handle_get_rcar_s4()
                    case 'ridesx4':
                        response = self.handle_get_ridesx4()
                    case _:
                        logging.error(f"received an invalid board-type: {path[1]}")
                        self.send_response(400)
                        return
                self.send_response(200)
                self.send_header('Content-type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps(response).encode('utf-8'))
            case _:
                self.send_response(400)

    def do_POST(self):
        logging.info("received a POST request")
        content_length = int(self.headers['Content-Length'])
        post_data = self.rfile.read(content_length)
        data = json.loads(post_data)
        logging.info(self.path)
        logging.info(f"data:\n{json.dumps(data, indent=2)}")

        if self.path.split("/")[-1] == 'requests':
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
            self.send_response(404)

    def handle_get_ridesx4(self):
        return get_boards(RIDE_SX4_TYPE)

    def handle_get_rcar_s4(self):
        return get_boards(RCAR_S4_TYPE)

    def handle_get_ti_784(self):
        return get_boards(J784S4EVM_TYPE)

    def handle_get_request(self, run_id):
        response = {}
        response['state'], result = get_state_and_result(run_id)
        response['result'] = {'overall': result}
        response['environments_requested'] = []
        response['id'] = run_id
        response['run'] = {'artifacts': []}
        return response

    def handle_post_request(self, data):
        run_id = str(uuid.uuid4())
        run_name = get_run_name(run_id)
        git_url = data['environments'][0]['variables'].get('CUSTOM_DISCOVER_URL', data['test']['fmf']['url'])
        test_branch =  data['environments'][0]['variables'].get('CUSTOM_DISCOVER_BRANCH', 'main')
        test_name = data['environments'][0]['variables'].get('CUSTOM_DISCOVER_TESTS', data['test']['fmf'].get('test_name', ''))

        context_data = data["environments"][0]["tmt"]["context"]
        context_str = json.dumps(context_data, indent=2)
        environment_data = data["environments"][0]["tmt"]["environment"]
        environment_str = json.dumps(environment_data, indent=2)

        image_url = os.environ.get("IMAGE_URL")
        if not image_url:
            compose = (
                data.get("environments", [{}])[0]
                .get("os", {})
                .get("compose", "")
            )
            if not compose:
                return {"error": "'environments' or 'compose' not found"}

            parsed_compose = json.loads(compose)
            image_url = parsed_compose["disk_image"]

        hw_target = os.environ.get(BOARD_TYPE)
        if not hw_target:
            hw_target = data['environments'][0]['variables'].get('HW_TARGET', '')
        board = os.environ.get(BOARD)

        if board:
            exporter_labels = [
                f"device={board}",
            ]
        else:
            board_type = 'qc8775' if hw_target == 'ridesx4' else hw_target.removesuffix("-ocp")
            exporter_labels = [
                f"board-type={board_type}",
            ]

        pipelinerun = {
            'apiVersion': 'tekton.dev/v1',
            'kind': 'PipelineRun',
            'metadata': {'name':run_name, 'namespace': POD_NAMESPACE},
            'spec': {
                'params': [
                    {'name': 'plan-name', 'value': data['test']['fmf']['name']},
                    {'name': 'test-name', 'value': test_name},
                    {'name': 'hw-target', 'value': hw_target.removesuffix('-ocp')},
                    {'name': 'testRunId', 'value': run_id},
                    {'name': 'testsRepo', 'value': git_url},
                    {'name': 'exporter-labels', 'value': exporter_labels},
                    {'name': 'testBrunch', 'value': test_branch},
                    {'name': 'client-name', 'value': data['settings']['pipeline'].get('client', 'demo')}, 
                    {'name': 'existing-lease-id', 'value': '01999aaf-2d2e-73e0-b5e3-b22c55ef621d'},
                    {'name': 'timeout', 'value': data['settings']['pipeline'].get('timeout', '')},
                    {'name': 'ctx', 'value': context_str},
                    {'name': 'env', 'value': environment_str},
                    {'name': 'image-url', 'value': image_url},
                ],
                'pipelineRef': {'name': os.environ.get(PIPELINE)},
                'timeouts': {'pipeline': os.environ.get(TIMEOUT)},
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
        tmt_image = os.environ.get(TMT_IMAGE)
        if tmt_image:
            pipelinerun['spec']['params'].append({'name': 'tmt-image', 'value': tmt_image})

        skipProvisioning = os.environ.get(SKIP_PROVISIONING) or 'false'
        pipelinerun['spec']['params'].append({'name': 'skipProvisioning', 'value': skipProvisioning})

        api_instance = client.CustomObjectsApi()
        response = api_instance.create_namespaced_custom_object(
            group='tekton.dev',
            version='v1',
            namespace=POD_NAMESPACE,
            plural='pipelineruns',
            body=pipelinerun,
        )

        logging.info(f"created pipelinerun:\n{json.dumps(response, indent=2)}")

        # Adding the run UUID to follow the request
        pipelinerun['id'] = run_id
        return pipelinerun

def get_boards(board_type):
    exporters = []
    try:
        api_instance = client.CustomObjectsApi()
        exporters = api_instance.list_namespaced_custom_object(
            group='jumpstarter.dev',
            version='v1alpha1',
            namespace=EXPORTERS_NAMESPACE,
            plural='exporters',
            label_selector=f"board-type={board_type}",
        )
    except ApiException as e:
        logging.error("Exception when calling CustomObjectsApi->get_namespaced_custom_object: %s\n" % e)

    def to_board(exporter):
        exporter['name'] = exporter['metadata']['name']
        labels = exporter['metadata']['labels']
        exporter['enabled'] = labels.get('enabled', 'true') == 'true'
        exporter['borrowed'] = False
        return exporter

    logging.info(f"exporters:\n{json.dumps(exporters, indent=2)}")
    return list(map(to_board, exporters['items']))

def get_run_name(run_id):
    return f"test-{run_id}"

def fetch_run(run_name):
    try:
        api_instance = client.CustomObjectsApi()
        return api_instance.get_namespaced_custom_object(
            group='tekton.dev',
            version='v1',
            namespace=POD_NAMESPACE,
            plural='pipelineruns',
            name=run_name
        )
    except ApiException as e:
        print("Exception when calling CustomObjectsApi->get_namespaced_custom_object: %s\n" % e)

def get_state_and_result(run_id):
    runStatus = fetch_run(get_run_name(run_id))
    logging.info(f"runStatus for {run_id}: {runStatus}")
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
                case 'Succeeded':
                    return 'complete', 'passed'
                case 'Failed' | 'Cancelled' | 'Timeout' | 'PipelineValidationFailed' | 'ParameterTypeMismatch' | 'PipelineRunTimeout' | 'CouldntGetPipeline' | 'InvalidTaskRunSpecs':
                    return 'complete', 'failed'
    except:
        logging.info(f"failed to retrieve status of pipeline for run {run_id}")
        return 'complete', 'failed'

def run(server_class=HTTPServer, handler_class=CustomHandler, port=8080):
    server_address = ('', port)
    httpd = server_class(server_address, handler_class)
    logging.info(f'Starting httpd on port {port}...')
    httpd.serve_forever()

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    run()
