// Jenkins executes. AI investigates. Humans decide.
//
// Every check runs through ci/run_step.py, which keeps the log and records the outcome.
// catchError marks the build FAILURE but lets later stages run, so one build collects
// evidence from every check. Jenkins results only ever get worse, never better: the AI
// stage is advisory and cannot turn a failed build green.

pipeline {
    agent { label 'build-agent' }

    options {
        skipDefaultCheckout()
        disableConcurrentBuilds()
        timeout(time: 20, unit: 'MINUTES')
        buildDiscarder(logRotator(numToKeepStr: '30'))
    }

    parameters {
        choice(
            name: 'SCENARIO',
            choices: ['success', 'test-failure', 'security-finding', 'terraform-failure', 'prompt-injection'],
            description: 'Demo scenario. Anything except "success" overlays files from scenarios/<name>/overlay onto the workspace copy.'
        )
    }

    environment {
        SCENARIO = "${params.SCENARIO ?: 'success'}"
        VENV = "${WORKSPACE}@tmp/venv"
        RUN = 'python3 ci/run_step.py --id'
        AI_SERVICE_URL = 'http://ai:8000'
        PYTHONDONTWRITEBYTECODE = '1'
    }

    stages {
        stage('Source Preparation') {
            steps {
                deleteDir()
                sh 'rm -rf "$VENV"'
                sh '''python3 /src/ci/run_step.py --id source-preparation -- \
                      python3 /src/ci/prepare_source.py --source /src --dest "$WORKSPACE" --scenario "$SCENARIO"'''
            }
        }

        stage('Dependency Installation') {
            steps {
                catchError(buildResult: 'FAILURE', stageResult: 'FAILURE') {
                    sh '''$RUN dependency-install -- sh -ec '
                          python3 -m venv "$VENV"
                          "$VENV/bin/pip" install --quiet --disable-pip-version-check --upgrade pip
                          "$VENV/bin/pip" install --quiet --disable-pip-version-check -r requirements-dev.txt
                          "$VENV/bin/pip" list --format=freeze
                          ' '''
                    script { env.DEPS_READY = 'true' }
                }
            }
        }

        stage('Static Checks') {
            when { environment name: 'DEPS_READY', value: 'true' }
            steps {
                catchError(buildResult: 'FAILURE', stageResult: 'FAILURE') {
                    sh '$RUN static-checks -- "$VENV/bin/ruff" check --no-cache --output-format concise app ai ci'
                }
            }
        }

        stage('Unit Tests') {
            when { environment name: 'DEPS_READY', value: 'true' }
            steps {
                catchError(buildResult: 'FAILURE', stageResult: 'FAILURE') {
                    sh '''$RUN unit-tests -- "$VENV/bin/python" -m pytest app/tests ai/tests ci/tests \
                          -p no:cacheprovider --junitxml=evidence/reports/junit.xml'''
                }
            }
            post {
                always { junit allowEmptyResults: true, testResults: 'evidence/reports/junit.xml' }
            }
        }

        stage('Security Scanning') {
            when { environment name: 'DEPS_READY', value: 'true' }
            steps {
                sh 'mkdir -p evidence/reports'
                catchError(buildResult: 'FAILURE', stageResult: 'FAILURE') {
                    sh '''$RUN bandit -- "$VENV/bin/bandit" -c pyproject.toml -r app ai ci \
                          --severity-level medium -f json -o evidence/reports/bandit.json'''
                }
                catchError(buildResult: 'FAILURE', stageResult: 'FAILURE') {
                    sh '''$RUN pip-audit -- "$VENV/bin/pip-audit" --progress-spinner off \
                          -f json -o evidence/reports/pip-audit.json'''
                }
                catchError(buildResult: 'FAILURE', stageResult: 'FAILURE') {
                    sh '''$RUN gitleaks -- gitleaks dir . --config ci/gitleaks.toml --no-banner --redact \
                          --report-format json --report-path evidence/reports/gitleaks.json'''
                }
            }
        }

        // Validation only. ci/tf_safe.py refuses plan, apply, destroy and every other
        // state-changing subcommand, and strips cloud credentials from the environment.
        stage('Terraform Validation') {
            steps {
                catchError(buildResult: 'FAILURE', stageResult: 'FAILURE') {
                    sh '$RUN terraform-fmt -- python3 ci/tf_safe.py fmt -check -recursive -diff'
                }
                catchError(buildResult: 'FAILURE', stageResult: 'FAILURE') {
                    sh '$RUN terraform-init -- python3 ci/tf_safe.py init -backend=false -input=false -no-color'
                }
                catchError(buildResult: 'FAILURE', stageResult: 'FAILURE') {
                    sh '$RUN terraform-validate -- python3 ci/tf_safe.py validate -json'
                }
            }
        }

        stage('Evidence Packaging') {
            steps {
                // Uses the virtualenv when it exists (for defusedxml), otherwise system Python.
                sh """PY="\$VENV/bin/python"; [ -x "\$PY" ] || PY=python3
                      "\$PY" ci/package_evidence.py --result ${currentBuild.currentResult} \
                          --started-at-ms ${currentBuild.startTimeInMillis} --scenario "\$SCENARIO" """
                archiveArtifacts artifacts: 'evidence/evidence.json, evidence/logs/*, evidence/reports/*'
            }
        }

        stage('AI Failure Analysis') {
            steps {
                // Advisory: this script always exits 0 and reports an unreachable AI
                // service as "unavailable". The token only allows submitting evidence.
                withCredentials([string(credentialsId: 'ai-ingest-token', variable: 'AI_INGEST_TOKEN')]) {
                    sh 'python3 ci/submit_evidence.py'
                }
                archiveArtifacts artifacts: 'evidence/ai-analysis.json'
            }
        }

        stage('Pipeline Summary') {
            steps {
                script {
                    currentBuild.description = "scenario: ${env.SCENARIO}"
                }
                echo """
Build            : ${env.JOB_NAME} #${env.BUILD_NUMBER}
Scenario         : ${env.SCENARIO}
Jenkins result   : ${currentBuild.currentResult}
Evidence package : ${env.BUILD_URL}artifact/evidence/evidence.json
AI analysis      : ${env.BUILD_URL}artifact/evidence/ai-analysis.json
Human review     : http://localhost:8001/?build=${env.JOB_NAME}-${env.BUILD_NUMBER}
No remediation was executed. Any fix is a separate, human-operated change."""
            }
        }
    }
}
