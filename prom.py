
from kubernetes import client, config
from prometheus_client import start_http_server, Gauge
import time

#kubectl describe nodes to Kubernetes API
#try:
#    config.load_incluster_config()
try:
    config.load_kube_config('/app/admin.conf')
except:
    confog.load_incluster_config()
# Create Kubernetes API client
v1 = client.CoreV1Api()

# 初始化 Prometheus 指标
cpu_allocatable_metric = Gauge('node_cpu_allocatable', 'Allocatable CPU cores per node', ['node'])
cpu_capacity_metric = Gauge('node_cpu_capacity', 'Total CPU cores per node', ['node'])
cpu_allocated_metric = Gauge('node_cpu_allocated', 'Total allocated CPU cores per node', ['node'])
gpu_allocatable_metric = Gauge('node_gpu_allocatable', 'Allocatable GPUs per node', ['node'])
gpu_capacity_metric = Gauge('node_gpu_capacity', 'Total GPUs per node', ['node'])
gpu_allocated_metric = Gauge('node_gpu_allocated', 'Total allocated GPU cores per node', ['node'])

def get_cpu_used(node_name):
    pods = v1.list_pod_for_all_namespaces().items
    cpu_used = 0

    for pod in pods:
        if pod.spec.node_name == node_name:
            for container in pod.spec.containers:
                resources = container.resources
                if resources and resources.requests and 'cpu' in resources.requests:
                    cpu_request = resources.requests['cpu']
                    #print(cpu_request)
                    if cpu_request[-1] == 'm':
                        cpu_used += int(cpu_request[:-1])/1000.0
                    else:
                        cpu_used += int(cpu_request)
    return cpu_used
def get_gpu_used(node_name):
    pods = v1.list_pod_for_all_namespaces().items
    gpu_used = 0

    for pod in pods:
        if pod.spec.node_name == node_name:
            for container in pod.spec.containers:
                resources = container.resources
                if resources and resources.requests and 'nvidia.com/gpu' in resources.requests:
                    gpu_request = resources.requests['nvidia.com/gpu']
                    #print(gpu_request)
                    gpu_used += int(gpu_request)
    return gpu_used


def update_metrics():
    # Load kubeconfig file

    nodes = v1.list_node().items

    for node in nodes:
        node_name = node.metadata.name
        cpu_allocated = get_cpu_used(node_name)
        gpu_allocated = get_gpu_used(node_name)

        # CPU 信息
        #print(node)
        cpu_allocatable = node.status.allocatable.get('cpu', '0')
        cpu_capacity = node.status.capacity.get('cpu', '0')
        cpu_allocatable_metric.labels(node=node_name).set(cpu_allocatable)
        cpu_allocated_metric.labels(node=node_name).set(cpu_allocated)
        cpu_capacity_metric.labels(node=node_name).set(cpu_capacity)
        print("cpu_allocated",node_name,cpu_allocated)
        # GPU 信息（如果有的话）
        gpu_allocatable = node.status.allocatable.get('nvidia.com/gpu', '0')
        gpu_capacity = node.status.capacity.get('nvidia.com/gpu', '0')
        gpu_allocatable_metric.labels(node=node_name).set(gpu_allocatable)
        gpu_allocated_metric.labels(node=node_name).set(gpu_allocated)
        gpu_capacity_metric.labels(node=node_name).set(gpu_capacity)
        print("gpu_allocated",node_name,gpu_allocated)


if __name__ == "__main__":
    # 启动 Prometheus 客户端服务器
    start_http_server(8080)  # 确保端口未被占用

    # 定期更新指标
    while True:
        update_metrics()
        print("running")
        time.sleep(60)  # 每 60 秒更新一次
        
