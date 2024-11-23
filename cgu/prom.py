import re
import time
import threading
from kubernetes import client, config
from prometheus_client import start_http_server, Gauge
from kubernetes.client.rest import ApiException

try:
    config.load_incluster_config()
except:
    config.load_kube_config()

v1 = client.CoreV1Api()

# 定义全局 Gauge
cpu_allocatable_metric = Gauge('node_cpu_allocatable', 'Allocatable CPU cores per node', ['node'])
gpu_allocatable_metric = Gauge('node_gpu_allocatable', 'Allocatable GPUs per node', ['node'])
node_usage_cpu_metric = Gauge('node_usage_cpu', 'CPU usage per node', ['node'])
node_usage_gpu_metric = Gauge('node_usage_gpu', 'GPU usage per node', ['node'])
namespace_usage_cpu_metric = Gauge('namespace_usage_cpu', 'CPU usage per namespace', ['namespace'])
namespace_usage_gpu_metric = Gauge('namespace_usage_gpu', 'GPU usage per namespace', ['namespace'])
notebook_usage_cpu = Gauge('notebook_cpu_usage', 'CPU usage of notebooks', ['node', 'namespace', 'notebook'])
notebook_usage_memory = Gauge('notebook_memory_usage', 'Memory usage of notebooks', ['node', 'namespace', 'notebook'])
notebook_usage_gpu = Gauge('notebook_gpu_usage', 'GPU usage of notebooks', ['node', 'namespace', 'notebook'])
memory_allocatable_metric = Gauge('node_memory_allocatable', 'Allocatable memory per node', ['node'])

# 新的独立成本指标
namespace_cpu_cost_metric = Gauge('namespace_cpu_cost', 'Cumulative CPU cost per namespace', ['namespace'])
namespace_gpu_cost_metric = Gauge('namespace_gpu_cost', 'Cumulative GPU cost per namespace', ['namespace'])

# 上一次的缓存数据
previous_data = {
    'node_usage_cpu': {},
    'node_usage_gpu': {},
    'namespace_usage_cpu': {},
    'namespace_usage_gpu': {},
    'notebook_usage_cpu_data': {},
    'notebook_usage_gpu_data': {},
    'notebook_usage_memory_data': {},
    'namespace_cpu_cost': {},  # CPU 成本缓存
    'namespace_gpu_cost': {}   # GPU 成本缓存
}

def parse_memory_string(memory_str):
    if memory_str.endswith('Ki'):
        return float(memory_str[:-2]) / (1024 * 1024)
    elif memory_str.endswith('Mi'):
        return float(memory_str[:-2]) / 1024.0
    elif memory_str.endswith('Gi'):
        return float(memory_str[:-2])
    elif memory_str.endswith('M'):
        return float(memory_str[:-1]) / 1024.0
    elif memory_str.endswith('K'):
        return float(memory_str[:-1]) / (1024 * 1024)
    elif memory_str.endswith('G'):
        return float(memory_str[:-1])
    else:
        return float(memory_str) / (1024 * 1024 * 1024)

def get_cpu_used(pod):
    cpu_used = 0
    for container in pod.spec.containers:
        resources = container.resources
        if resources and resources.requests and 'cpu' in resources.requests:
            cpu_request = resources.requests['cpu']
            if cpu_request[-1] == 'm':
                cpu_used += int(cpu_request[:-1]) / 1000.0
            else:
                cpu_used += int(cpu_request)
    return cpu_used

def get_gpu_used(pod):
    gpu_used = 0
    for container in pod.spec.containers:
        resources = container.resources
        if resources and resources.requests:
            for resource_name, resource_quantity in resources.requests.items():
                if 'gpu' in resource_name.lower():
                    gpu_used += int(resource_quantity)
    return gpu_used

def get_memory_used(pod):
    memory_used = 0
    for container in pod.spec.containers:
        resources = container.resources
        if resources and resources.requests and 'memory' in resources.requests:
            memory_request = resources.requests['memory']
            try:
                memory_used += parse_memory_string(memory_request)
            except ValueError:
                print(f"Unknown memory format: {memory_request}")
    return memory_used

def get_notebook_name(pod):
    match = re.search(r'-\d+$', pod.metadata.name)
    if match:
        return pod.metadata.name
    return None

def update_metrics():
    global previous_data
    current_data = {
        'node_usage_cpu': {},
        'node_usage_gpu': {},
        'namespace_usage_cpu': {},
        'namespace_usage_gpu': {},
        'notebook_usage_cpu_data': {},
        'notebook_usage_gpu_data': {},
        'notebook_usage_memory_data': {},
        'namespace_cpu_cost': previous_data.get('namespace_cpu_cost', {}),  # 继承之前的 CPU 成本数据
        'namespace_gpu_cost': previous_data.get('namespace_gpu_cost', {})   # 继承之前的 GPU 成本数据
    }

    try:
        nodes = v1.list_node().items
        pods = v1.list_pod_for_all_namespaces().items
        namespaces = v1.list_namespace().items

        # 节点级别的 CPU、GPU、内存可分配量
        for node in nodes:
            node_name = node.metadata.name
            cpu_allocatable_metric.labels(node=node_name).set(node.status.allocatable.get('cpu', '0'))
            gpu_allocatable_metric.labels(node=node_name).set(node.status.allocatable.get('nvidia.com/gpu', '0'))
            memory_allocatable_metric.labels(node=node_name).set(parse_memory_string(node.status.allocatable.get('memory', '0')))

            current_data['node_usage_cpu'][node_name] = 0
            current_data['node_usage_gpu'][node_name] = 0

        # 初始化命名空间使用量
        for namespace in namespaces:
            namespace_name = namespace.metadata.name
            current_data['namespace_usage_cpu'][namespace_name] = 0
            current_data['namespace_usage_gpu'][namespace_name] = 0

        # Pod 级别的 CPU、GPU 和内存使用量
        for pod in pods:
            node_name = pod.spec.node_name
            namespace_name = pod.metadata.namespace
            notebook_name = get_notebook_name(pod)

            if node_name and notebook_name:
                cpu_allocated = get_cpu_used(pod)
                gpu_allocated = get_gpu_used(pod)
                memory_allocated = get_memory_used(pod)

                current_data['namespace_usage_cpu'][namespace_name] += cpu_allocated
                current_data['namespace_usage_gpu'][namespace_name] += gpu_allocated
                current_data['node_usage_cpu'][node_name] += cpu_allocated
                current_data['node_usage_gpu'][node_name] += gpu_allocated

                current_data['notebook_usage_cpu_data'][(node_name, namespace_name, notebook_name)] = cpu_allocated
                current_data['notebook_usage_memory_data'][(node_name, namespace_name, notebook_name)] = memory_allocated
                current_data['notebook_usage_gpu_data'][(node_name, namespace_name, notebook_name)] = gpu_allocated

                notebook_usage_cpu.labels(node=node_name, namespace=namespace_name, notebook=notebook_name).set(cpu_allocated)
                notebook_usage_memory.labels(node=node_name, namespace=namespace_name, notebook=notebook_name).set(memory_allocated)
                notebook_usage_gpu.labels(node=node_name, namespace=namespace_name, notebook=notebook_name).set(gpu_allocated)

        # 更新成本指标（每个命名空间只要有使用量就累加）
        for namespace_name, cpu_usage in current_data['namespace_usage_cpu'].items():
            if cpu_usage > 0:
                # 获取现有的 CPU 成本或初始化为 0
                current_cpu_cost = current_data['namespace_cpu_cost'].get(namespace_name, 0)
                # 增加当前使用量
                current_cpu_cost += cpu_usage
                # 更新成本数据
                current_data['namespace_cpu_cost'][namespace_name] = current_cpu_cost
                # 设置指标值
                namespace_cpu_cost_metric.labels(namespace=namespace_name).set(current_cpu_cost)

        for namespace_name, gpu_usage in current_data['namespace_usage_gpu'].items():
            if gpu_usage > 0:
                # 获取现有的 GPU 成本或初始化为 0
                current_gpu_cost = current_data['namespace_gpu_cost'].get(namespace_name, 0)
                # 增加当前使用量
                current_gpu_cost += gpu_usage
                # 更新成本数据
                current_data['namespace_gpu_cost'][namespace_name] = current_gpu_cost
                # 设置指标值
                namespace_gpu_cost_metric.labels(namespace=namespace_name).set(current_gpu_cost)

    except ApiException as e:
        print(f"API 请求失败：{e}")
        return

    # 清理未更新的过期数据（不包括成本数据）
    def cleanup_previous_data(current, previous, gauge):
        for key in list(previous.keys()):
            if key not in current:
                gauge.labels(*key).set(0)
                del previous[key]

    cleanup_previous_data(current_data['node_usage_cpu'], previous_data['node_usage_cpu'], node_usage_cpu_metric)
    cleanup_previous_data(current_data['node_usage_gpu'], previous_data['node_usage_gpu'], node_usage_gpu_metric)
    cleanup_previous_data(current_data['namespace_usage_cpu'], previous_data['namespace_usage_cpu'], namespace_usage_cpu_metric)
    cleanup_previous_data(current_data['namespace_usage_gpu'], previous_data['namespace_usage_gpu'], namespace_usage_gpu_metric)
    cleanup_previous_data(current_data['notebook_usage_cpu_data'], previous_data['notebook_usage_cpu_data'], notebook_usage_cpu)
    cleanup_previous_data(current_data['notebook_usage_gpu_data'], previous_data['notebook_usage_gpu_data'], notebook_usage_gpu)
    cleanup_previous_data(current_data['notebook_usage_memory_data'], previous_data['notebook_usage_memory_data'], notebook_usage_memory)

    previous_data = current_data

if __name__ == "__main__":
    start_http_server(8080)
    while True:
        update_metrics()
        print("running")
        time.sleep(60)