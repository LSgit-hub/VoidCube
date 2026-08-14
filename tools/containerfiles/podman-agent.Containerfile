FROM docker.io/library/node:22.22.0-bookworm-slim AS node_runtime

FROM docker.io/library/python:3.14-slim

# sqlite-vec publishes glibc wheels but not musl wheels for Python 3.14, so the
# project image uses Debian slim instead of Alpine.
RUN sed -i 's#http://deb.debian.org#https://mirrors.tuna.tsinghua.edu.cn#g' /etc/apt/sources.list.d/debian.sources \
    && apt-get update \
    && apt-get install -y --no-install-recommends \
    bash \
    ca-certificates \
    coreutils \
    curl \
    findutils \
    git \
    grep \
    ripgrep \
    sed \
    && rm -rf /var/lib/apt/lists/*

COPY --from=node_runtime /usr/local/bin/node /usr/local/bin/node
COPY --from=node_runtime /usr/local/lib/node_modules /usr/local/lib/node_modules
RUN ln -s ../lib/node_modules/npm/bin/npm-cli.js /usr/local/bin/npm \
    && ln -s ../lib/node_modules/npm/bin/npx-cli.js /usr/local/bin/npx

WORKDIR /root

# Install exactly the project's declared Python and desktop dependencies. The
# build context is the repository root; the root .dockerignore excludes host
# virtualenvs and generated artifacts from this copy.
COPY pyproject.toml requirements.txt README.md /opt/voidcube/
COPY desktop/package.json desktop/package-lock.json /opt/voidcube/desktop/
COPY agent /opt/voidcube/agent
COPY tools /opt/voidcube/tools
COPY systems /opt/voidcube/systems
COPY VoidCube_app /opt/voidcube/VoidCube_app
COPY VoidCube_cli /opt/voidcube/VoidCube_cli
COPY VoidCube_core /opt/voidcube/VoidCube_core
COPY plugins /opt/voidcube/plugins
COPY Mem/src/memai /opt/voidcube/Mem/src/memai
COPY voidcube.py cli.py run_agent.py /opt/voidcube/

ENV PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple
ENV npm_config_registry=https://registry.npmmirror.com

RUN python -m pip install --no-cache-dir --upgrade pip \
    && python -m pip install --no-cache-dir "/opt/voidcube[dev]" \
    && cd /opt/voidcube/desktop \
    && npm ci --ignore-scripts --no-audit --no-fund

ENV VOIDCUBE_PROJECT_ROOT=/opt/voidcube

CMD ["sleep", "infinity"]
