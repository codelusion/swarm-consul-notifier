# Docker swarm consul notifier
# Auto-registration/de-registration of Docker Swarm services in Consul
# by hooking into Docker daemon event stream
FROM frolvlad/alpine-python2

USER root
RUN apk add --update \
  && pip install virtualenv docker-py python-consul

WORKDIR /app

COPY . /app

ENTRYPOINT ["python", "consul-notifier.py"]