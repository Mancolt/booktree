FROM alpine:3.21

ARG UID=1000
ARG GID=1000
ARG USER=booktree
ARG GROUPNAME=booktree
ENV USER=${USER} GROUPNAME=${GROUPNAME} UID=${UID} GID=${GID}

COPY . /booktree/
WORKDIR /booktree
RUN echo "**** installing system packages ****" \
    && apk update \
    && apk upgrade --no-cache \
    # python3 ships ensurepip, so the venv gets its own pip; no system pip/setuptools are installed
    && apk add --no-cache python3 ffmpeg \
    && ln -sf python3 /usr/bin/python \
    && python3 -m venv /venv \
    && /venv/bin/pip install --no-cache-dir --upgrade pip \
    && /venv/bin/pip install --no-cache-dir --requirement requirements.txt \
    # the runtime image needs no installer; removing it also removes the scanner surface
    && /venv/bin/pip uninstall -y pip \
    && rm -rf /root/.cache \
    && if getent passwd ${UID} >/dev/null; then deluser $(getent passwd ${UID} | cut -d: -f1); fi \
    && if getent group ${GID} >/dev/null; then delgroup $(getent group ${GID} | cut -d: -f1); fi \
    && addgroup -S -g ${GID} ${GROUPNAME} \
    && adduser -S -u ${UID} -G ${GROUPNAME} -H -D ${USER} \
    && chown -R ${UID}:${GID} /booktree

USER ${USER}
VOLUME /config
VOLUME /logs
VOLUME /data
