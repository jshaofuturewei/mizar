import time
import yaml
from teste2e.common.helper import *
from cli.mizarapi import *
from kubernetes import client, config
from kubernetes.stream import stream
from teste2e.common.k8spod import *
from teste2e.common.k8sservice import *
from kubernetes.stream.ws_client import ERROR_CHANNEL, STDOUT_CHANNEL, STDERR_CHANNEL
from mizar.common.constants import *


class k8sCluster:
    _instance = None

    def __new__(cls, **kwargs):
        if cls._instance is None:
            cls._instance = super(k8sCluster, cls).__new__(cls)
            cls._init(cls, **kwargs)
        return cls._instance

    def _init(self):
        logger.info("Check or start existing cluster")
        if not self.is_running(self):
            logger.info("Starting Kind cluster!")
            self.start_kind_cluster(self)
        else:
            logger.info("Test cluster is already running")

    def start_kind_cluster(self):
        logger.info("Start test cluster")
        run_cmd("./kind-setup.sh dev 2")

    def is_running(self):
        ret, val = run_cmd("kubectl cluster-info | grep -c 'running'")
        return int(val.strip()) == 2

    def delete_kind_cluster(self):
        logger.info("Delete test cluster")
        run_cmd("kind delete cluster")


class k8sApi:

    def __init__(self):
        self.api = MizarApi()
        config.load_kube_config()
        self.k8sapi = client.CoreV1Api()
        self.operator_logs_cmd = "kubectl get pods | grep mizar-operator | awk '{print $1}' | xargs -i kubectl logs {}"
        self.operator_pod_name = run_cmd_text(
            "kubectl get pods | grep mizar-operator | awk '{print $1}'")

    def create_vpc(self, name, ip, prefix, dividers=1, vni=None):
        self.api.create_vpc(name, ip, prefix, dividers, vni)

    def create_net(self, name, ip, prefix, vpc, vni, bouncers=1, virtual=False):
        self.api.create_net(name, ip, prefix, vpc, vni, bouncers, virtual)

    def get_vpc(self, name):
        vpc = self.api.get_vpc(name)
        while vpc["status"] != OBJ_STATUS.obj_provisioned:
            vpc = self.api.get_vpc(name)
        return vpc

    def get_vpc_with_status(self, name, status=OBJ_STATUS.obj_provisioned, timeout=60):
        timeout_start = time.time()
        while  time.time() < timeout_start + timeout:
            vpc = self.api.get_vpc(name)
            if vpc["status"] == status:
                return vpc
        return vpc

    def get_net(self, name):
        net = self.api.get_net(name)
        while net["status"] != OBJ_STATUS.obj_provisioned:
            net = self.api.get_net(name)
        return net

    def delete_vpc(self, name):
        self.api.delete_vpc(name)

    def delete_net(self, name):
        self.api.delete_net(name)

    def create_pod(self, name, vpc="vpc0", subnet="net0", scaledep=''):
        pod_manifest = {
            'apiVersion': 'v1',
            'kind': 'Pod',
            'metadata': {
                    'name': name,
                    'annotations': {
                        OBJ_DEFAULTS.mizar_pod_vpc_annotation: vpc,
                        OBJ_DEFAULTS.mizar_pod_subnet_annotation: subnet,
                    },
                'labels': {
                        'scaledep': scaledep
                        }
            },
            'spec': {
                'containers': [{
                    'image': 'mizarnet/testpod:latest',
                    'name': 'box1'
                }]
            }
        }
        if not subnet:
            del pod_manifest["metadata"]["annotations"][OBJ_DEFAULTS.mizar_pod_subnet_annotation]
        resp = self.k8sapi.create_namespaced_pod(
            body=pod_manifest, namespace='default')

        status = resp.status.phase
        while status != 'Running':
            resp = self.k8sapi.read_namespaced_pod(
                name=name, namespace='default')
            status = resp.status.phase

        pod = k8sPod(self, name, resp.status.pod_ip)
        logger.info("Pod {} IP {}".format(pod.name, pod.ip))
        return pod

    def delete_pod(self, name):
        self.k8sapi.delete_namespaced_pod(name=name, namespace='default',
                                          grace_period_seconds=0)

        deleted = False
        while deleted:
            try:
                self.k8sapi.read_namespaced_pod(name=name, namespace='default')
            except:
                deleted = True
        logger.info("Deleted {}".format(name))

    def pod_exec(self, name, cmd):
        exec_command = cmd.split()
        resp = stream(self.k8sapi.connect_post_namespaced_pod_exec,
                      name,
                      'default',
                      container='box1',
                      command=exec_command,
                      stderr=True, stdin=False,
                      stdout=True, tty=True,
                      _preload_content=False)
        while resp.is_open():
            resp.update(timeout=1)
            err = resp.read_channel(ERROR_CHANNEL)
            if err:
                return yaml.safe_load(err)

    def pod_exec_stdout(self, name, cmd):
        exec_command = cmd.split()
        resp = stream(self.k8sapi.connect_get_namespaced_pod_exec,
                      name,
                      'default',
                      command=exec_command,
                      stderr=True, stdin=False,
                      stdout=True, tty=False)
        return resp

    def create_service(self, name):
        service_manifest = {
            'apiVersion': 'v1',
            'kind': 'Service',
            'metadata': {
                'labels': {
                    'name': name
                },
                'name': name,
                'resourceversion': 'v1',
                'annotations': {
                    'service.beta.kubernetes.io/mizar-scaled-endpoint-type': "scaled-endpoint"
                }
            },
            'spec': {
                'ports': [
                    {'name': 'http',
                     'port': 8000,
                     'targetPort': 7000,
                     'protocol': 'TCP'},
                    {'name': 'tcp',
                     'port': 8001,
                     'targetPort': 9001,
                     'protocol': 'TCP'},
                    {'name': 'udp',
                     'port': 8002,
                     'targetPort': 5001,
                     'protocol': 'UDP'}],
                'selector': {'scaledep': name}
            }
        }

        resp = self.k8sapi.create_namespaced_service(
            body=service_manifest, namespace='default')

        ip = None
        while not ip:
            resp = self.k8sapi.read_namespaced_service(
                name=name, namespace='default')
            ip = resp.spec.cluster_ip

        svc = k8sService(self, name, ip)
        logger.info("Service {} IP {}".format(svc.name, svc.ip))
        return svc

    def delete_service(self, name):
        self.k8sapi.delete_namespaced_service(name=name, namespace='default',
                                              grace_period_seconds=0)

        deleted = False
        while deleted:
            try:
                self.k8sapi.read_namespaced_service(
                    name=name, namespace='default')
            except:
                deleted = True
        logger.info("Deleted {}".format(name))

    def count_operator_permanent_errors(self):
        cmd = "{} | grep 'failed permanently' -c".format(
            self.operator_logs_cmd)
        return run_cmd_text(cmd)

    def print_operator_errors(self):
        logger.info("Printing errors if any!")
        cmd = "{} | grep -E '^[A-Z][a-z].*Error:'".format(
            self.operator_logs_cmd)
        logger.info(run_cmd_text(cmd))

    def count_operator_errors(self):
        cmd = "{} | grep -E '^[A-Z][a-z].*Error:' -c".format(
            self.operator_logs_cmd)
        return run_cmd_text(cmd)

    def check_errors(self):
        self.print_operator_errors()
        return int(self.count_operator_permanent_errors()) + int(self.count_operator_errors())
