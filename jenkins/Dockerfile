FROM jenkins/jenkins:2.580.1-lts-jdk21

# Shared with the build agent so it can read its own connection secret (see init.groovy.d).
USER root
RUN mkdir -p /run/agent-secret /var/jenkins_config \
    && chown jenkins:jenkins /run/agent-secret /var/jenkins_config
USER jenkins

COPY plugins.txt /usr/share/jenkins/ref/plugins.txt
RUN jenkins-plugin-cli --plugin-file /usr/share/jenkins/ref/plugins.txt

COPY configuration/jenkins.yaml /var/jenkins_config/jenkins.yaml
# The .override suffix makes Jenkins refresh these scripts in JENKINS_HOME on every start.
COPY configuration/init.groovy.d/agent-secret.groovy /usr/share/jenkins/ref/init.groovy.d/agent-secret.groovy.override
COPY configuration/init.groovy.d/pipeline-job.groovy /usr/share/jenkins/ref/init.groovy.d/pipeline-job.groovy.override

ENV CASC_JENKINS_CONFIG=/var/jenkins_config/jenkins.yaml \
    JAVA_OPTS="-Djenkins.install.runSetupWizard=false"
