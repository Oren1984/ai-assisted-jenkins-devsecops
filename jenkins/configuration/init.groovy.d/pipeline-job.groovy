// Creates (or refreshes) the Pipeline job from the repository's Jenkinsfile.
// The Jenkinsfile is mounted read-only; restart Jenkins to pick up changes to it.
import hudson.model.ParametersDefinitionProperty
import hudson.model.StringParameterDefinition
import jenkins.model.Jenkins
import org.jenkinsci.plugins.workflow.cps.CpsFlowDefinition
import org.jenkinsci.plugins.workflow.job.WorkflowJob

def jenkins = Jenkins.get()
def name = 'devsecops-pipeline'
def jenkinsfile = new File('/var/jenkins_config/Jenkinsfile')
if (!jenkinsfile.isFile()) {
    println "pipeline-job: ${jenkinsfile} not found, job not created"
    return
}

def job = jenkins.getItem(name) ?: jenkins.createProject(WorkflowJob, name)
job.setDescription('Build, test, scan and validate the sample application, then hand ' +
    'evidence to the AI analysis service. The AI result is advisory only.')
// sandbox = true: the pipeline can only use approved steps.
job.setDefinition(new CpsFlowDefinition(jenkinsfile.getText('UTF-8'), true))
if (job.getProperty(ParametersDefinitionProperty) == null) {
    // Seed value so the first build can be parameterised. The parameters block in the
    // Jenkinsfile replaces this after the first run.
    job.addProperty(new ParametersDefinitionProperty(
        new StringParameterDefinition('SCENARIO', 'success', 'Demo scenario')))
}
job.save()
println "pipeline-job: ${name} loaded from ${jenkinsfile}"
