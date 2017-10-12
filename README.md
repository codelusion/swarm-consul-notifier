Swarm Consul Notifier
----------------------

A docker image that keeps Consul notified of service-related [Docker Swarm](https://docs.docker.com/engine/swarm/) events.
It is the (partial) equivalent of [Registrator](http://gliderlabs.github.io/registrator/latest/) for Docker services.  

How To:
* Listens to Docker service-related events on `DOCKER_SOCKET` (default: `/var/run/docker.sock`)
* Connects to Consul on `CONSUL_ADDR` (default: `.127.0.0.1`).

Using docker:
```bash
docker run -d \
    --name=consul_notifier \
    --net=host \
    --volume=/var/run/docker.sock:/tmp/docker.sock \
    aisaac/swarm-consul-notifier:latest 
```

Recommended: using docker-compose along with a `consul` Agent on each node in Docker Swarm:
```yml
version: '3'
services:
    consul_agent:
        image: consul
        container_name: consul_agent
        ports:
            - 8300
            - 8301
            - 8301/udp
            - 8302
            - 8302/udp
            - 8400
            - 8500
        environment:
            - "CONSUL_LOCAL_CONFIG={\"leave_on_terminate\" : true}"
        entrypoint:
            - consul
            - agent
            - -node=<node_name>
            - -advertise=<host_ip>
            - -data-dir=<data_dir>
            - -encrypt=<consul_encryption_key>
            - -datacenter=<datacenter_name>
            - -retry-join=<consul_server_url/ip>
        network_mode: "host"

    consul_notifier:
        image: aisaac/swarm-consul-notifier
        container_name: consul_notifier
        depends_on:
            - consul_agent
        volumes:
            - /var/run/docker.sock:/var/run/docker.sock
        network_mode: "host"
        labels:
EOF
```

* When a service is started or stopped, it registers/de-registers the service with Consul.
* **Note**: Only services that have `CONSUL_SERVICE_PORT` set will be registered:
```bash
  docker service create \
    --name <service_name> \
    --publish <service_port>:8080 \
    --env CONSUL_SERVICE_PORT=<service_port> \ # required, service ignored if not specified 
    --env CONSUL_HEALTH_CHECK=/health \ # default: `/`
    --env CONSUL_HEALTH_INTERVAL=30s \ # default: `10s`
    --env CONSUL_HEALTH_SSL=True # default: `HTTP`
    <service_image>
```
* New in (>v1.1): Will attempt to register existing services before listening to new events

Credits
-------
* Original Consul Notifier is based on this blog post: http://jamesdmorgan.github.io/2016/docker-service-discovery-consul/
* Related GitHub: https://github.com/jamesdmorgan/vagrant-ansible-docker-swarm/blob/master/consul-notifier/consul-notifier.py


