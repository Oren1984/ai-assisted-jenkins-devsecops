// Writes the build agent's connection secret to a volume shared (read-only) with the agent
// container, so the agent can connect without holding any Jenkins user credentials.
import jenkins.model.Jenkins

def computer = Jenkins.get().getComputer('build-agent')
if (computer == null) {
    println 'agent-secret: node build-agent is not defined yet'
    return
}
def file = new File('/run/agent-secret/build-agent.secret')
file.text = computer.getJnlpMac()
println 'agent-secret: wrote connection secret for build-agent'
