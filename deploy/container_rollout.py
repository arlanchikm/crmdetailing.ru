"""Roll out an image using existing Docker configuration without opening an env file.

Requires the existing Docker socket permission. Runtime environment is copied only
in memory, never written to disk or logged. Old containers are retained for rollback
until the operator finishes post-deployment verification.
"""
import argparse
import http.client
import json
import socket
import time
from urllib.parse import quote


class DockerConnection(http.client.HTTPConnection):
    def connect(self):
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.settimeout(self.timeout)
        self.sock.connect('/var/run/docker.sock')


def api(method, path, data=None):
    connection = DockerConnection('localhost', timeout=40)
    body = json.dumps(data).encode() if data is not None else None
    connection.request(method, path, body, {'Content-Type': 'application/json'})
    response = connection.getresponse()
    raw = response.read()
    status = response.status
    connection.close()
    if status >= 400:
        # Docker error responses can echo configuration; never emit their body.
        raise RuntimeError(f'Docker {method} {path.split("?")[0]} returned {status}')
    return json.loads(raw) if raw else {}


def inspect(name):
    return api('GET', '/containers/' + quote(name, safe='') + '/json')


def stop(name):
    api('POST', f'/containers/{name}/stop?t=20')


def start(name):
    api('POST', f'/containers/{name}/start')


def disconnect(name, networks):
    for network in networks:
        api('POST', f'/networks/{quote(network, safe="")}/disconnect', {'Container': name, 'Force': True})


def endpoints(record):
    return {network: {'Aliases': [alias for alias in value.get('Aliases', []) if alias != record['Id'][:12]],
        **({'IPAMConfig': value['IPAMConfig']} if value.get('IPAMConfig') else {})}
        for network, value in record['NetworkSettings']['Networks'].items()}


def rename(name, destination):
    api('POST', f'/containers/{name}/rename?name={quote(destination, safe="")}')


def wait_running(name, health=False):
    deadline = time.monotonic() + 115
    while time.monotonic() < deadline:
        state = inspect(name)['State']
        if not state['Running']:
            raise RuntimeError(f'{name} stopped during rollout')
        if not health or state.get('Health', {}).get('Status') == 'healthy':
            return
        time.sleep(3)
    raise RuntimeError(f'{name} did not become healthy')


def rollout(image, stamp, *, env_updates=None, worker_beat=False):
    image_id = api('GET', '/images/' + quote(image, safe='') + '/json')['Id']
    names = ['crm-detailing-prod-web-1', 'crm-detailing-prod-worker-1']
    records = {name: inspect(name) for name in names}
    previous = {name: name + '.previous-' + stamp for name in names}
    for backup in previous.values():
        try:
            inspect(backup)
        except RuntimeError:
            continue
        raise RuntimeError('Previous rollout already exists; use a new release identifier')
    stopped, renamed, detached, created = [], [], [], []
    try:
        for name in reversed(names):
            print(f'Stopping {name}', flush=True)
            stop(name)
            stopped.append(name)
        for name in names:
            record = records[name]
            rename(name, previous[name])
            renamed.append(name)
            disconnect(previous[name], record['NetworkSettings']['Networks'])
            detached.append(name)
            config = dict(record['Config'])
            config.pop('Hostname', None)
            config['Image'] = image_id
            if env_updates:
                environment=dict(item.split('=',1) for item in config.get('Env',[]) if '=' in item)
                environment.update(env_updates)
                config['Env']=[key+'='+value for key,value in environment.items()]
            if worker_beat and name.endswith('worker-1'):
                config['Cmd']=['celery','-A','config','worker','-B','-l','INFO','--schedule','/tmp/crm-celerybeat-schedule']
            config['Labels'] = {**config.get('Labels', {}), 'com.docker.compose.image': image_id}
            config['HostConfig'] = record['HostConfig']
            config['NetworkingConfig'] = {'EndpointsConfig': endpoints(record)}
            api('POST', '/containers/create?name=' + quote(name, safe=''), config)
            created.append(name)
            start(name)
            print(f'Waiting for {name}', flush=True)
            wait_running(name, health=name == names[0])
        print('New web and worker are running; previous containers retained for verification.', flush=True)
    except Exception:
        print('Rollout failed; restoring previous containers.', flush=True)
        for name in reversed(created):
            try:
                stop(name)
                api('DELETE', f'/containers/{name}?force=true')
            except Exception:
                pass
        for name in names:
            try:
                if name in renamed:
                    rename(previous[name], name)
                if name in detached:
                    for network, endpoint in endpoints(records[name]).items():
                        api('POST', f'/networks/{quote(network, safe="")}/connect', {'Container': name, 'EndpointConfig': endpoint})
                if name in stopped:
                    start(name)
            except Exception:
                print(f'Recovery requires attention: {name}', flush=True)
        raise


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--image', required=True)
    parser.add_argument('--release', required=True)
    parser.add_argument('--with-beat',action='store_true')
    args = parser.parse_args()
    rollout(args.image, args.release, worker_beat=args.with_beat)
