from http.server import BaseHTTPRequestHandler, HTTPServer
import logging
import os
import shutil
import subprocess
import xml.etree.ElementTree as ET

class CustomHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        logging.info(f"received a GET request ({self.path})")
        path = self.path.replace("//","/").split("/") # TODO: drop 'replace("//", "/")' when TC always includes 752c3740
        if len(path) < 2:
            self.send_response(404)
        elif len(path) == 2:
            url = f"{self.path}/"
            logging.info(f"forwarding a GET request to {url}")
            self.send_response(301)
            self.send_header('Location', url)
            self.end_headers()
        else:
            run_id = path[1]
            workdir = f"/srv/results/{run_id}"
            if not os.path.isdir(workdir):
                self.send_response(500)
                return
            if path[2] == '':
                if not os.path.exists(f"{workdir}/results.html"):
                    shutil.copyfile("/usr/local/results.html", f"{workdir}/results.html")
                with open(f"{workdir}/results.html", 'rb') as f:
                    data = f.read()
                self.send_response(200)
                self.send_header("content-type", "text/html")
                self.end_headers()
                self.wfile.write(data)
            else:
                match path[2]:
                    case 'results.xml':
                        results = handle_get_results(workdir, run_id)
                        self.send_response(200)
                        self.send_header('Content-type', 'application/xml')
                        self.end_headers()
                        self.wfile.write(results)
                    case 'results-junit.xml':
                        with open(f"{workdir}/results-junit.xml", 'rb') as f:
                            data = f.read()
                        self.send_response(200)
                        self.send_header('Content-type', 'application/xml')
                        self.end_headers()
                        self.wfile.write(data)
                    case 'pipeline.log':
                        try:
                            stdout = log(run_id)
                            self.send_response(200)
                            self.send_header('Content-type', 'text/plain')
                            self.end_headers()
                            self.wfile.write(stdout)
                        except subprocess.CalledProcessError as e:
                            logging.error(f"Command failed with error: {e}")
                            logging.error(f"Stderr: {e.stderr}")
                            self.send_response(500)
                    case 'artifacts':
                        with open(f"/srv/results/{'/'.join(path)}", 'rb') as f:
                            data = f.read()
                        self.send_response(200)
                        self.send_header("content-type", "text/plain")
                        self.end_headers()
                        self.wfile.write(data)
                    case _:
                        self.send_response(400)

    def do_HEAD(self):
        self.send_response(200)
        self.send_header("Content-type", "text/html")
        self.end_headers()

def log(run_id):
    result = tkn("pipelineruns", "list", "--label", f"run={run_id}", "--limit", "1", "--output",
                 "jsonpath={.items[0].metadata.name}", text=True)
    result = tkn('pipelineruns', 'logs', result.stdout.strip())
    return result.stdout

def tkn(*args, text=None):
    cmd = ['tkn', *args]
    logging.info(f"tkn running: {" ".join(cmd)}")
    try:
        return subprocess.run(cmd, capture_output=True, check=True, text=text)
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

def handle_get_results(workdir, run_id):
    tree = ET.parse(f"{workdir}/results-junit.xml")
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
            'stage': 'complete', # TODO: fix
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
        log.set('href', f'https://artifacts.osci.redhat.com/{run_id}/artifacts{name}')
        log = ET.SubElement(logs, "log")
        log.set('name', 'tmt-verbose-log')
        log.set('href', f'https://artifacts.osci.redhat.com/{run_id}/artifacts{name}/log.txt')
        log = ET.SubElement(logs, "log")
        log.set('name', 'tmt-reproducer')
        log.set('href', f'https://artifacts.osci.redhat.com/{run_id}/artifacts{name}/tmt-reproducer.sh')
        log = ET.SubElement(logs, "log")
        log.set('name', 'tmt-jmp-reproducer')
        log.set('href', f'https://artifacts.osci.redhat.com/{run_id}/artifacts{name}/tmt-jmp-reproducer.sh')
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
