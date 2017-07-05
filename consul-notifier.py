#!/usr/bin/python
import argparse
import json
import sys
import logging
import docker
import consul
import os

logger = logging.getLogger(__name__)
args = None

# docker run -v /var/run/docker.sock:/var/run/docker.sock consul-notifier


def setup_logging(verbose=False):
    '''
    Setup logging

    :param verbose: bool - Enable verbose debug mode
    '''

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

    def get_port(self, default):
        for nv in self.env:
            n, v = str(nv).split('=')
            if n == 'CONSUL_SERVICE_PORT':
                return v

        return default

    def get_id(self):
        return "{0}:{1}:{2}".format(
            self.hostname,
            self.container_name,
            self.port)

    def handle(self, action):
        if (action in self.status_map and
                hasattr(self, self.status_map[action])):

            self.container = self.docker_client.inspect_container(self.name)
            self.env = self.container['Config']['Env']
            self.hostname = self.container['Config']['Hostname']
            self.port = self.get_port(None)

            # Strip the leading slash
            self.container_name = self.container['Name'][1:]
            self.container_id = self.get_id()

            if args.verbose:
                print(json.dumps(self.container, sort_keys=True, indent=4))

            getattr(self, self.status_map[action])()
        else:
            logger.warning("Ignoring action {0}".format(action))

    def register(self):
        if not self.port:
            logger.info(
                "Skipping registration of {0} not port defined".format(
                    self.service))

        logger.info("Registering {0} {1} port {2}".format(
            self.service,
            self.container_id,
            self.port))

        for node in self.get_swarm_nodes():
            res = self.consul_instance.agent.service.register(
                self.service,
                address=node,
                check=self.get_service_health_check(node),
                service_id=self.container_id,
                port=int(self.port))

            if not res:
                logger.error("Failed to register service at node: {0}".format(node))
                sys.exit(1)

    def get_swarm_nodes(self):
        # self.docker_client.swarm_reload()
        nodes = self.docker_client.nodes.list()
        print(json.dumps(nodes, sort_keys=True, indent=4))
        return [node.get('ip', 'ip') for node in nodes]



    def deregister(self):
        if not self.port:
            logger.info(
                "Skipping de-registration of {0} not port defined".format(
                    self.service))
            return

        logger.info("De-registering {0} {1}".format(
            self.service,
            self.container_id))

        res = self.consul_instance.agent.service.deregister(service_id=self.container_id)

        if not res:
            logger.error("Failed to de-register service")
            sys.exit(1)


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

        Example start dict
        {
            'status': 'start',
            'timeNano': 1471291489261243761,
            'from': 'lucj/demo-www:1.0',
            'Actor': {
                'Attributes': {
                    'com.docker.swarm.task': '',
                    'name': 'www.8.e7nt85x8f6tic0tiow5wzv5z8',
                    'com.docker.swarm.node.id': '2wzelo3sj0oowbuo2jxc9jcje',
                    'image': 'lucj/demo-www:1.0',
                    'com.docker.swarm.service.id': '5m24m676zm1q6tjdte2o06ieb',
                    'com.docker.swarm.task.name': 'www.8',
                    'com.docker.swarm.service.name': 'www',
                    'com.docker.swarm.task.id': 'e7nt85x8f6tic0tiow5wzv5z8'
                },
                'ID': u'8217cb8565dd774f316c3e51b0f88551e3337edffb087f28db75fb7126160641'
            },
            'time': 1471291489,
            'Action': u'start',
            'Type': 'container',
            'id': '8217cb8565dd774f316c3e51b0f88551e3337edffb087f28db75fb7126160641'
        }

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
        s = Service(docker_client, consul_instance, args.name)
        s.handle(args.action)
    else:
        logger.error("Unknown action {0}".format(args.action))
        sys.exit(1)


if __name__ == '__main__':
    main()