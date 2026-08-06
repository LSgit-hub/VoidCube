FROM docker.io/library/python:3.14-alpine

# Runtime for VoidCube terminal and file tools. Keep package verification
# enabled while using the mirror that is reachable in the primary region.
RUN sed -i 's#https://dl-cdn.alpinelinux.org/alpine#https://mirrors.tuna.tsinghua.edu.cn/alpine#g' /etc/apk/repositories \
    && apk add --no-cache \
    bash \
    ca-certificates \
    coreutils \
    curl \
    findutils \
    git \
    grep \
    nodejs \
    npm \
    ripgrep \
    sed

WORKDIR /root

CMD ["sleep", "infinity"]
