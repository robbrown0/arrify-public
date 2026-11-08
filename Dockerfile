# Refresh the base digest and Debian snapshot together in a tested dependency PR.
FROM python:3.13-slim-bookworm@sha256:a5a7017efc5969e00ae2b91c8eb0631049a569652aa4ffc4501769049ae45c72 AS runtime
ARG DEBIAN_SNAPSHOT=20261104T212339Z
RUN sed -i "s|http://deb.debian.org/debian-security|https://snapshot.debian.org/archive/debian-security/${DEBIAN_SNAPSHOT}|; s|http://deb.debian.org/debian$|https://snapshot.debian.org/archive/debian/${DEBIAN_SNAPSHOT}|" /etc/apt/sources.list.d/debian.sources \
    && apt-get -o Acquire::Check-Valid-Until=false update \
    && apt-get install -y --no-install-recommends ffmpeg openssl \
    && rm -rf /var/lib/apt/lists/*
COPY requirements.txt /tmp/requirements.txt
RUN python -m pip install --no-cache-dir --require-hashes -r /tmp/requirements.txt \
    && python -m pip uninstall -y pip && rm /tmp/requirements.txt
WORKDIR /opt/arrify
COPY vendor/music-playlists.json /opt/arrify/music-playlists.json
COPY app /opt/arrify/app
COPY web /opt/arrify/web
COPY VERSION /opt/arrify/VERSION
ARG VCS_REF=unknown
RUN printf '%s\n' "$VCS_REF" > /opt/arrify/REVISION
LABEL org.opencontainers.image.source="https://github.com/robbrown0/arrify" \
      org.opencontainers.image.revision="$VCS_REF"
ENV PYTHONPATH=/opt/arrify PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
USER 21012:21012
ENTRYPOINT ["python3", "-m", "app.server"]
CMD []

# Run against the same interpreter and dependencies shipped in the player.
FROM runtime AS test
USER root
WORKDIR /audit
COPY app /audit/app
COPY vendor /audit/vendor
COPY request-bridge /audit/request-bridge
COPY tests /audit/tests
COPY contracts /audit/contracts
COPY web /audit/web
COPY tools /audit/tools
COPY ops/catalog/artist-name-aliases-20261102.json /audit/ops/catalog/artist-name-aliases-20261102.json
COPY VERSION /audit/VERSION
ENV PYTHONPATH=/audit:/audit/vendor:/audit/request-bridge
ENTRYPOINT ["python3", "-m", "unittest", "discover", "-s", "tests", "-p", "test_*.py"]

FROM runtime AS release
