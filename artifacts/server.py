from http.server import BaseHTTPRequestHandler, HTTPServer
import json
import logging
import uuid
import requests
import os
import xml.etree.ElementTree as ET

TF_RESULTS_URL = os.environ.get("TF_RESULTS_URL")

class CustomHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        logging.info(f"received a GET request ({self.path})")
        path = self.path.replace("//","/").split("/")
        run_id = path[1] if len(path) > 2 else None
        workdir = f"/srv/results/{run_id}"
        if os.path.isdir(workdir):
            match path[2]:
                case 'results.xml':
                    results = handle_get_results(workdir, run_id)
                    self.send_response(200)
                    self.send_header('Content-type', 'application/xml')
                    self.end_headers()
                    self.wfile.write(results)
                case 'results-junit.xml':
                    with open(f"{workdir}/junit.xml", 'rb') as f:
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
        url = f"{TF_RESULTS_URL}{self.path}"
        logging.info(f"forwarding a GET request to {url}")
        return requests.get(url)

def handle_get_results(workdir, run_id):
    tree = ET.parse(f"{workdir}/junit.xml")
    overall_result = 'passed'
    root = tree.getroot()
    suites = {}
    for testsuite in root.findall(".//testsuite"):
        errors = int(testsuite.get("errors"))
        failures = int(testsuite.get("failures"))
        suite_result = 'passed' if errors + failures == 0 else 'failed'
        overall_result = 'failed' if suite_result == 'failed' else overall_result
        suites[testsuite.get("name")] = {
            'result': suite_result,
            'tests': testsuite.get("tests"),
            'stage': 'complete',
        }
    testsuites = ET.Element("testsuites")
    testsuites.set('overall-result', overall_result)
    properties = ET.SubElement(testsuites, "properties")
    property = ET.SubElement(properties, "property")
    property.set("name", "baseosci.overall-result")
    property.set("value", overall_result)
    for name, attributes in suites.items():
        item = ET.SubElement(testsuites, "testsuite")
        item.set('name', name)
        item.set('result', attributes['result'])
        item.set('tests', attributes['tests'])
        item.set('stage', attributes['stage'])
        logs = ET.SubElement(item, "logs")
        log = ET.SubElement(logs, "log")
        log.set('name', 'workdir')
        log.set('href', f"https://artifacts.osci.redhat.com/{run_id}") # TODO: fix
    xml = ET.tostring(testsuites, encoding='utf-8')
    logging.info('result: ' + xml.decode('utf-8'))
    return xml

def run(server_class=HTTPServer, handler_class=CustomHandler, port=8080):
    server_address = ('', port)
    httpd = server_class(server_address, handler_class)
    logging.info(f'Starting httpd on port {port}...')
    httpd.serve_forever()

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    run()
