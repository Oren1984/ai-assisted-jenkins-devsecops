# Build agent: the only place pipeline workloads run. It has the toolchain the pipeline
# needs and nothing else: no Docker client, no Docker socket, no cloud credentials.
FROM jenkins/inbound-agent:latest-jdk21

ARG TERRAFORM_VERSION=1.9.8
ARG GITLEAKS_VERSION=8.21.2

USER root
RUN apt-get update \
    && apt-get install -y --no-install-recommends python3 python3-venv unzip ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Each download is verified against the checksum file of its publisher.
RUN cd /tmp \
    && curl -fsSLO "https://releases.hashicorp.com/terraform/${TERRAFORM_VERSION}/terraform_${TERRAFORM_VERSION}_linux_amd64.zip" \
    && curl -fsSLO "https://releases.hashicorp.com/terraform/${TERRAFORM_VERSION}/terraform_${TERRAFORM_VERSION}_SHA256SUMS" \
    && grep " terraform_${TERRAFORM_VERSION}_linux_amd64.zip\$" "terraform_${TERRAFORM_VERSION}_SHA256SUMS" | sha256sum -c - \
    && unzip -q "terraform_${TERRAFORM_VERSION}_linux_amd64.zip" terraform -d /usr/local/bin \
    && curl -fsSLO "https://github.com/gitleaks/gitleaks/releases/download/v${GITLEAKS_VERSION}/gitleaks_${GITLEAKS_VERSION}_linux_x64.tar.gz" \
    && curl -fsSLO "https://github.com/gitleaks/gitleaks/releases/download/v${GITLEAKS_VERSION}/gitleaks_${GITLEAKS_VERSION}_checksums.txt" \
    && grep " gitleaks_${GITLEAKS_VERSION}_linux_x64.tar.gz\$" "gitleaks_${GITLEAKS_VERSION}_checksums.txt" | sha256sum -c - \
    && tar -xzf "gitleaks_${GITLEAKS_VERSION}_linux_x64.tar.gz" -C /usr/local/bin gitleaks \
    && rm -rf /tmp/*

# Pre-fetch the pinned AWS provider so `terraform init -backend=false` works offline and
# without credentials. This init reads the version constraint only: no backend, no state,
# no plan.
COPY terraform/versions.tf /tmp/providers/versions.tf
RUN mkdir -p /opt/terraform/providers \
    && cd /tmp/providers \
    && TF_PLUGIN_CACHE_DIR=/opt/terraform/providers CHECKPOINT_DISABLE=1 \
       terraform init -backend=false -input=false -no-color \
    && rm -rf /tmp/providers
COPY jenkins/agent/terraformrc /etc/terraformrc
ENV TF_CLI_CONFIG_FILE=/etc/terraformrc CHECKPOINT_DISABLE=1

COPY --chmod=755 jenkins/agent/entrypoint.sh /usr/local/bin/agent-entrypoint.sh
USER jenkins
ENTRYPOINT ["/usr/local/bin/agent-entrypoint.sh"]
