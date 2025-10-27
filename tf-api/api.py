from http.server import BaseHTTPRequestHandler, HTTPServer
import json
import logging
import uuid
from kubernetes import client, config
from kubernetes.client.rest import ApiException
import os
import subprocess

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
        git_url = data['environments'][0]['variables'].get('CUSTOM_DISCOVER_URL', data['test']['fmf']['url'])
        test_branch =  data['environments'][0]['variables'].get('CUSTOM_DISCOVER_BRANCH', 'main')
        test_name = data['environments'][0]['variables'].get('CUSTOM_DISCOVER_TESTS', data['test']['fmf'].get('test_name', ''))

        context_data = data["environments"][0]["tmt"]["context"]
        context_str = json.dumps(context_data)
        environment_data = data["environments"][0]["tmt"]["environment"]
        environment_str = json.dumps(environment_data)

        aboot_image_url = ''
        rootfs_image_url = ''
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
            if "disk_image" in parsed_compose:
                image_url = parsed_compose["disk_image"]
            elif "boot_image" in parsed_compose and "root_image" in parsed_compose:
                aboot_image_url = parsed_compose["boot_image"]
                rootfs_image_url = parsed_compose["root_image"]

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

        cmd = ["tkn", "pipeline", "start", os.environ.get(PIPELINE),
               "--labels", f"run={run_id}",
               f"--param=plan-name={data['test']['fmf']['name']}",
               f"--param=test-name={test_name}",
               f"--param=hw-target={hw_target.removesuffix('-ocp')}",
               f"--param=testRunId={run_id}",
               f"--param=testsRepo={git_url}",
               f"--param=exporter-labels={exporter_labels}",
               f"--param=testBranch={test_branch}",
               f"--param=client-name={data['settings']['pipeline'].get('client', 'demo')}",
               f"--param=existing-lease-id=019a2121-b92a-7280-acc3-dbafa6a66987",
               #f"--param=timeout={data['settings']['pipeline'].get('timeout', '')}",
               f"--param=ctx={context_str}",
               f"--param=env={environment_str}",
               "--workspace", "name=jumpstarter-client-secret,secret=demo-config",
               "--workspace", "name=test-results,claimName=tmt-results",
               f"--pipeline-timeout={os.environ.get(TIMEOUT)}",
               "--serviceaccount", "pipeline",
               "--use-param-defaults",
                ]

        '''
        if 'name' in data['test']['fmf'].keys():
            pipelinerun['spec']['params'].append({'name': 'plan-name', 'value': data['test']['fmf']['name']})
        if 'test_name' in data['test']['fmf'].keys():
            pipelinerun['spec']['params'].append({'name': 'test-name', 'value': data['test']['fmf']['test_name']})
        '''
        if image_url:
            cmd.append(f"--param=image-url={image_url}")
        if aboot_image_url:
            cmd.append(f"--param=aboot-image-url={aboot_image_url}")
        if rootfs_image_url:
            cmd.append(f"--param=rootfs-image-url={rootfs_image_url}")

        tmt_image = os.environ.get(TMT_IMAGE)
        if tmt_image:
            cmd.append(f"--param=tmt-image={tmt_image}")

        skipProvisioning = os.environ.get(SKIP_PROVISIONING) or 'false'
        cmd.append(f"--param=skipProvisioning={skipProvisioning}")

        logging.info(f"running: {" ".join(cmd)}")
        try:
            result = subprocess.run(cmd, capture_output=True, check=True)
        except subprocess.CalledProcessError as e:
            logging.error("--- TKN PIPELINERUN START ---")
            logging.error(f"1. Command: {e.cmd}")
            logging.error(f"2. Return Code: {e.returncode}")

            # 3. Check and print STDERR (where errors go)
            # Note: e.stderr is a string because we used text=True in the run() call.
            if e.stderr:
                logging.error("\n3. Standard Error (STDERR):\n" + "=" * 25)
                logging.error(e.stderr.strip())

            # 4. Check and print STDOUT (might contain context)
            if e.stdout:
                logging.error("\n4. Standard Output (STDOUT):\n" + "=" * 25)
                logging.error(e.stdout.strip())

        pipelinerun = {}
        try:
            cmd = ["tkn", "pipelinerun", "describe", get_run_name(run_id), "-o", "json"]
            logging.info(f"running: {" ".join(cmd)}")
            result = subprocess.run(cmd, capture_output=True, check=True)
            pipelinerun = json.loads(result.stdout)
            # logging.info(f"created pipelinerun:\n{json.dumps(response, indent=2)}")
        except subprocess.CalledProcessError as e:
            logging.error("--- TKN PIPELINERUN START ---")
            logging.error(f"1. Command: {e.cmd}")
            logging.error(f"2. Return Code: {e.returncode}")

            # 3. Check and print STDERR (where errors go)
            # Note: e.stderr is a string because we used text=True in the run() call.
            if e.stderr:
                logging.error("\n3. Standard Error (STDERR):\n" + "=" * 25)
                logging.error(e.stderr.strip())

            # 4. Check and print STDOUT (might contain context)
            if e.stdout:
                logging.error("\n4. Standard Output (STDOUT):\n" + "=" * 25)
                logging.error(e.stdout.strip())

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

    #logging.info(f"exporters:\n{json.dumps(exporters, indent=2)}")
    return list(map(to_board, exporters['items']))

def get_run_name(run_id):
    cmd = ["tkn", "pipelineruns", "list", "--label", f"run={run_id}", "--output", "name"]
    logging.info(f"running: {" ".join(cmd)}")
    try:
        result = subprocess.run(cmd, capture_output=True, check=True, text=True)
        cmd = ["cut", "-d/", "-f2"]
        result = subprocess.run(cmd, capture_output=True, check=True, text=True, input=result.stdout)
        cmd = ["tr", "-d", "\n"]
        result = subprocess.run(cmd, capture_output=True, check=True, text=True, input=result.stdout)
        return result.stdout.strip()
    except subprocess.CalledProcessError as e:
        logging.error("--- DEBUG TKN ---")
        logging.error(f"1. Command: {e.cmd}")
        logging.error(f"2. Return Code: {e.returncode}")

        # 3. Check and print STDERR (where errors go)
        # Note: e.stderr is a string because we used text=True in the run() call.
        if e.stderr:
            logging.error("\n3. Standard Error (STDERR):\n" + "=" * 25)
            logging.error(e.stderr.strip())

        # 4. Check and print STDOUT (might contain context)
        if e.stdout:
            logging.error("\n4. Standard Output (STDOUT):\n" + "=" * 25)
            logging.error(e.stdout.strip())

def get_state_and_result(run_id):
    run_name = get_run_name(run_id)
    runStatus = 'Unknown'
    try:
        cmd = ["tkn", "pipelinerun", "describe", run_name, "-o", 'jsonpath={.status.conditions[?(@.type=="Succeeded")].status}']
        logging.info(f"running: {" ".join(cmd)}")
        result = subprocess.run(cmd, capture_output=True, check=True, text=True)
        runStatus = result.stdout
    except subprocess.CalledProcessError as e:
        logging.error("--- TKN PIPELINERUN START ---")
        logging.error(f"1. Command: {e.cmd}")
        logging.error(f"2. Return Code: {e.returncode}")

        # 3. Check and print STDERR (where errors go)
        # Note: e.stderr is a string because we used text=True in the run() call.
        if e.stderr:
            logging.error("\n3. Standard Error (STDERR):\n" + "=" * 25)
            logging.error(e.stderr.strip())

        # 4. Check and print STDOUT (might contain context)
        if e.stdout:
            logging.error("\n4. Standard Output (STDOUT):\n" + "=" * 25)
            logging.error(e.stdout.strip())

    logging.info(f"runStatus for {run_id}: {runStatus}")
    if runStatus == 'True':
        return 'complete', 'passed'
    elif runStatus == 'False':
        return 'complete', 'failed'
    else:
        return 'running', 'unknown'

def run(server_class=HTTPServer, handler_class=CustomHandler, port=8080):
    server_address = ('', port)
    httpd = server_class(server_address, handler_class)
    logging.info(f'Starting httpd on port {port}...')
    httpd.serve_forever()

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    run()
