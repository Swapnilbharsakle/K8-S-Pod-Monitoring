/**
 * Jenkinsfile — Kubernetes Pod Health Monitor Pipeline
 * ====================================================
 * Triggers a multi-environment, multi-application pod health scan,
 * generates CSV + HTML reports, and emails a dashboard to the team.
 *
 * Prerequisites
 * -------------
 *   • Jenkins Active Choices plugin  (for ENV / APP parameter checkboxes)
 *   • Jenkins Email Extension plugin (emailext step)
 *   • Jenkins HTML Publisher plugin  (publishHTML step)
 *   • A Git credential stored in Jenkins with id matching KUBECONFIG_REPO_CREDENTIALS_ID
 *   • Python 3 available on the build agent
 *   • health_check.py present in the workspace application directory
 *
 * Required Jenkins Credentials
 * ----------------------------
 *   KUBECONFIG_REPO_CREDENTIALS_ID  — SSH key or username/password for the
 *                                     kubeconfig Git repository
 *
 * Configuration — update the "Pipeline Configuration" block below before use.
 */

// ── Pipeline Configuration ────────────────────────────────────────────────
// Update these values to match your environment before running.

def AGENT_LABEL             = 'your-jenkins-agent-label'      // Jenkins node label
def KUBECONFIG_REPO_URL     = 'https://github.com/your-org/your-kubeconfig-repo.git'
def KUBECONFIG_REPO_BRANCH  = '*/main'
def KUBECONFIG_REPO_CRED_ID = 'your-jenkins-credential-id'   // Jenkins credentials ID
def KUBECONFIG_REPO_DIR     = 'eks-kubeconfig'                // local checkout folder name
def KUBECONFIG_APPS_SUBPATH = 'APPS'                          // subfolder inside the repo that holds *.config files
def APP_BASE_DIR            = 'Nonprod'                       // workspace subdirectory for the health check script
def HEALTH_SCRIPT           = 'health_check.py'               // Python script filename
def EMAIL_FROM              = 'sre-alerts@your-org.com'       // sender address
def EMAIL_TO                = 'team-dl@your-org.com'          // recipient address(es)
def DASHBOARD_REPORT_NAME   = 'SRE Failure Dashboard'         // HTML publisher report label

// Available environments — keep in sync with health_check.py's ALL_ENVS list.
def AVAILABLE_ENVS = [
    'all',
    'env1', 'env1-bg',
    'env2', 'env2-bg',
    'env3', 'env3-bg',
]

// Available application profiles — extend this list with your real app names.
def AVAILABLE_APPS = [
    'all',
    'APP_CORE',
    'APP_AUTH',
    'APP_GATEWAY',
    'APP_BILLING',
    'APP_DATA',
]

// ── Pipeline Definition ───────────────────────────────────────────────────

pipeline {
    agent { label AGENT_LABEL }

    // ── Parameters ────────────────────────────────────────────────────────
    parameters {
        // Multi-select checkbox: target environments
        activeChoice(
            name: 'ENV',
            choiceType: 'PT_CHECKBOX',
            description: 'Select one or more target environments to scan (or choose "all").',
            script: groovyScript(
                script: [
                    script: "return ${groovy.json.JsonOutput.toJson(AVAILABLE_ENVS)}",
                    sandbox: true,
                ],
                fallbackScript: [script: "return ['all']", sandbox: true]
            )
        )

        // Multi-select checkbox: target application profiles
        activeChoice(
            name: 'APP',
            choiceType: 'PT_CHECKBOX',
            description: 'Select one or more application profiles to scan (or choose "all").',
            script: groovyScript(
                script: [
                    script: "return ${groovy.json.JsonOutput.toJson(AVAILABLE_APPS)}",
                    sandbox: true,
                ],
                fallbackScript: [script: "return ['all']", sandbox: true]
            )
        )
    }

    // ── Environment Variables ─────────────────────────────────────────────
    environment {
        BASE_DIR    = "${APP_BASE_DIR}"
        REPORT_NAME = "pod_health_report_${env.BUILD_NUMBER}.csv"
    }

    // ── Stages ────────────────────────────────────────────────────────────
    stages {

        stage('Checkout Kubeconfig Repository') {
            steps {
                dir(KUBECONFIG_REPO_DIR) {
                    checkout([
                        $class: 'GitSCM',
                        branches: [[name: KUBECONFIG_REPO_BRANCH]],
                        extensions: [],
                        userRemoteConfigs: [[
                            credentialsId: KUBECONFIG_REPO_CRED_ID,
                            url: KUBECONFIG_REPO_URL,
                        ]]
                    ])
                }
            }
        }

        stage('Global Health Check') {
            steps {
                dir("${env.BASE_DIR}") {
                    script {
                        // Remove stale outputs from previous runs
                        sh """
                            rm -f ${env.REPORT_NAME} \
                                  email_table.html \
                                  subject_line.txt \
                                  full_failure_report.html
                        """

                        // Harden config file permissions
                        sh """
                            find ../${KUBECONFIG_REPO_DIR}/${KUBECONFIG_APPS_SUBPATH} \
                                -type f -name '*.config' \
                                -exec chmod 644 {} +
                        """

                        // Strip Windows carriage returns that can corrupt YAML
                        sh """
                            find ../${KUBECONFIG_REPO_DIR}/${KUBECONFIG_APPS_SUBPATH} \
                                -type f -name '*.config' \
                                -exec sed -i 's/\r//g' {} +
                        """

                        // Run the health check
                        sh "python3 ${HEALTH_SCRIPT} '${params.ENV}' '${params.APP}'"

                        // Build a human-readable email subject line from CSV output
                        sh """
python3 - <<'PYEOF'
import csv, datetime, os

total_failures = 0
unique_envs    = set()
report_name    = os.environ.get('REPORT_NAME', 'pod_health_report.csv')

WORKLOAD_TYPES = {'DEPLOYMENT', 'STATEFULSET', 'DAEMONSET', 'ROLLOUT'}

try:
    with open(report_name, 'r') as f:
        for row in csv.DictReader(f):
            if row.get('Resource_Type') in WORKLOAD_TYPES and 'PASS' not in str(row.get('Status', '')):
                total_failures += 1
            env_val = row.get('Env', '')
            if env_val and env_val != 'N/A':
                unique_envs.add(env_val.split()[0].upper())
except Exception:
    pass

env_string    = ', '.join(sorted(unique_envs)) if unique_envs else '${params.ENV}'
build_no      = os.environ.get('BUILD_NUMBER', 'N/A')
ist_now       = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(hours=5, minutes=30)
timestamp_ist = ist_now.strftime('%Y-%m-%d %H:%M:%S IST')

subject = (
    f'SRE Pod Health Report [Build #{build_no}]: '
    f'{total_failures} Failure(s) across {env_string} @ {timestamp_ist}'
)

with open('subject_line.txt', 'w') as sf:
    sf.write(subject)
PYEOF
                        """

                        // Read subject into a pipeline env var for the Notification stage
                        env.EMAIL_SUBJECT = fileExists('subject_line.txt')
                            ? readFile('subject_line.txt').trim()
                            : "SRE Pod Health Report [Build #${env.BUILD_NUMBER}]: Execution Completed"
                    }
                }
            }
        }

        stage('Publish & Notify') {
            steps {
                dir("${env.BASE_DIR}") {
                    script {
                        // Publish the visual failure drill-down as a Jenkins HTML report
                        if (fileExists('full_failure_report.html')) {
                            publishHTML([
                                allowMissing: false,
                                alwaysLinkToLastBuild: true,
                                keepAll: true,
                                reportDir: '.',
                                reportFiles: 'full_failure_report.html',
                                reportName: DASHBOARD_REPORT_NAME,
                            ])
                        }

                        // Send email with the inline HTML dashboard table
                        def tableContent = readFile('email_table.html')
                        emailext(
                            subject: "${env.EMAIL_SUBJECT}",
                            body: """
                                <html><body>
                                <p>Team,</p>
                                <p>
                                    Below is the pod health summary for your selected
                                    environments and application profiles.
                                    Please review any CRITICAL entries and take action
                                    as needed.
                                </p>
                                ${tableContent}
                                </body></html>
                            """,
                            from: EMAIL_FROM,
                            to: EMAIL_TO,
                            attachmentsPattern: "${env.REPORT_NAME}",
                            mimeType: 'text/html',
                        )
                    }
                }
            }
        }
    }

    // ── Post Actions ──────────────────────────────────────────────────────
    post {
        always {
            dir("${env.BASE_DIR}") {
                archiveArtifacts(
                    artifacts: "${env.REPORT_NAME}, full_failure_report.html",
                    allowEmptyArchive: true,
                )
            }
        }

        failure {
            emailext(
                subject: "Pipeline FAILED — SRE Pod Health Monitor [Build #${env.BUILD_NUMBER}]",
                body: """
                    <html><body>
                    <p>
                        The SRE Pod Health Monitor pipeline failed at build
                        <b>#${env.BUILD_NUMBER}</b>.<br>
                        Please check the
                        <a href="${env.BUILD_URL}console">console output</a>
                        for details.
                    </p>
                    </body></html>
                """,
                from: EMAIL_FROM,
                to: EMAIL_TO,
                mimeType: 'text/html',
            )
        }
    }
}
