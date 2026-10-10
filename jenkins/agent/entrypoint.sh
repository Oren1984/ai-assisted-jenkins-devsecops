#!/bin/sh
# Wait for the controller to publish the connection secret of this agent, then connect.
set -eu
SECRET_FILE=/run/agent-secret/build-agent.secret
until [ -s "$SECRET_FILE" ]; do
  echo "waiting for the agent secret from the Jenkins controller..."
  sleep 3
done
JENKINS_SECRET="$(cat "$SECRET_FILE")"
export JENKINS_SECRET
export JENKINS_WEB_SOCKET=true
exec /usr/local/bin/jenkins-agent
