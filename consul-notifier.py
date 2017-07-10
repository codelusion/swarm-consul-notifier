#!/usr/bin/python
import argparse
import json
import sys
import logging
import docker
from docker import errors
import consul
import os

logger = logging.getLogger(__name__)
args = None


# docker run -v /var/run/docker.sock:/var/run/docker.sock consul-notifier


def setup_logging(verbose=False):
    """
    Setup logging
    :param verbose: bool - Enable verbose debug mode
    """

    ch = logging.StreamHandler()
    ch.setFormatter(logging.Formatter('[%(asctime)s] %(message)s'))
    logger.addHandler(ch)
    logger.setLevel(logging.INFO)
    if verbose:
        logger.setLevel(logging.DEBUG)


class Service(object):
    def __init__(self, docker_client, consul_instance, name, service):

        # http://gliderlabs.com/blog/2015/04/14/docker-events-explained/
        self.status_map = {
            "kill": "deregister",
            "stop": "deregister",
            "die": "deregister",
            "start": "register",
            "register": "register",
            "deregister": "deregister"
        }

        self.consul_instance = consul_instance
        self.docker_client = docker_client
        self.name = name
        self.service = service
        self.container = None
        self.svc_spec = {}

    def get_env(self, env_key):
        for nv in self.svc_spec['env']:
            n, v = str(nv).split('=')
            if n == env_key:
                return v

    def get_id(self):
        return "{0}:{1}:{2}".format(
            self.svc_spec['hostname'],
            self.svc_spec['container_name'],
            self.svc_spec['port'])

    def handle(self, action):
        if (action in self.status_map and
                hasattr(self, self.status_map[action])):

            try:
                self.container = self.docker_client.inspect_container(self.name)
            except errors.NotFound:
                logger.warning("NotFound: Cannot handle {0} on Container {1}".format(self.name, action))
                return

            self.svc_spec['env'] = self.container['Config']['Env']
            self.svc_spec['hostname'] = self.container['Config']['Hostname']
            self.svc_spec['port'] = self.get_env('CONSUL_SERVICE_PORT') or None
            self.svc_spec['health_check'] = self.get_env('CONSUL_HEALTH_CHECK') or ''
            self.svc_spec['health_check_interval'] = self.get_env('CONSUL_HEALTH_INTERVAL') or 10
            self.svc_spec['health_check_ssl'] = self.get_env('CONSUL_HEALTH_SSL') or False

            # Strip the leading slash
            self.svc_spec['container_name'] = self.container['Name'][1:]
            self.svc_spec['container_id'] = self.get_id()

            if args.verbose:
                print(json.dumps(self.container, sort_keys=True, indent=4))

            getattr(self, self.status_map[action])()
        else:
            logger.warning("Ignoring action {0}".format(action))

    def register(self):
        if not self.svc_spec['port']:
            logger.info(
                "Skipping registration of {0} not port defined".format(
                    self.service))
            return

        logger.info("Registering {0} {1} port {2}".format(
            self.service,
            self.svc_spec['container_id'],
            self.svc_spec['port']))

        print(json.dumps(self.svc_spec, sort_keys=True, indent=4))

        for node_addr in self.get_swarm_nodes_addr():
            res = self.consul_instance.agent.service.register(
                self.service,
                address=node_addr,
                check=consul.Check.http(self.get_health_check_url(node_addr), self.svc_spec['health_check_interval']),
                service_id=self.svc_spec['container_id'],
                port=int(self.svc_spec['port']))

            if not res:
                logger.error("Failed to register service at node: {0}".format(node_addr))

    def get_swarm_nodes_addr(self):
        nodes = self.docker_client.nodes()
        print(json.dumps(nodes, sort_keys=True, indent=4))
        return [node['Status']['Addr'] for node in nodes]

    def get_health_check_url(self, node_addr):
        proto = 'https' if self.svc_spec['health_check_ssl'] else 'http'
        if not self.svc_spec['health_check'] or self.svc_spec['health_check'] == '/':
            return "%s://%s:%s/" % (proto, node_addr, self.svc_spec['port'])
        else:
            return "%s://%s:%s%s" % (proto, node_addr, self.svc_spec['port'], self.svc_spec['health_check'])

    def deregister(self):
        if not self.svc_spec['port']:
            logger.info(
                "Skipping de-registration of {0} not port defined".format(
                    self.service))
            return

        logger.info("De-registering {0} {1}".format(
            self.service,
            self.svc_spec['container_id']))

        res = self.consul_instance.agent.service.deregister(service_id=self.svc_spec['container_id'])

        if not res:
            logger.error("Failed to de-register service")



def handler_args():
    global args

    help_text = '''
Register / De-register services manually or via Docker Daemon event stream
    '''

    parser = argparse.ArgumentParser(
        description=help_text,
        formatter_class=argparse.RawTextHelpFormatter
    )

    parser.add_argument('--verbose', '-v', action="count", default=0,
                        help='Verbose Logging')

    parser.add_argument('--action', '-a', default='stream',
                        help='Notification action (stream, register, deregister)')

    parser.add_argument('--name', '-n', default=None,
                        help='Container Name')

    args = parser.parse_args()

    return args


def stream(docker_client, consul_instance):
    """
    Connect to the docker daemon and listen for events
    possible events are:
        attach, commit, copy, create, destroy, die
        exec_create, exec_start, export, kill, oom, pause,
        rename, resize, restart, start, stop, top, unpause, update
    """

    # start listening for new events
    for event in docker_client.events(decode=True):

        service_key = 'com.docker.swarm.service.name'

        if service_key not in event['Actor']['Attributes']:
            continue

        name = event['Actor']['Attributes']['name']
        service = event['Actor']['Attributes'][service_key]
        action = event['Action']

        print("-" * 80)
        print("Processing {0} event {1}".format(action, name))
        print (json.dumps(event, sort_keys=True, indent=4))
        print("-" * 80)

        s = Service(docker_client, consul_instance, name, service)
        s.handle(action)


def main():
    """
        Register / De-register containers that have
        CONSUL_SERVICE_PORT env variable defined
        if specified, CONSUL_ADDR refers to consul instance
    """
    args = handler_args()
    setup_logging(args.verbose)

    # create a docker client object that talks to the local docker daemon
    docker_client = docker.Client(base_url='unix://var/run/docker.sock')

    consul_host = os.environ.get('CONSUL_ADDR', '127.0.0.1')
    logger.info("Consul Host: {0}".format(consul_host))
    consul_instance = consul.Consul(host=consul_host)

    logger.info("Consul notifier processing {0}".format(args.action))

    if args.action == 'stream':
        stream(docker_client, consul_instance)
    elif args.action in ['register', 'deregister']:
        s = Service(docker_client, consul_instance, args.name, None)
        s.handle(args.action)
    else:
        logger.error("Unknown action {0}".format(args.action))


if __name__ == '__main__':
    main()
