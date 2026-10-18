// Add this `post` block to each pipeline you want triaged (or to a shared library step).
// Requires a Jenkins "Secret text" credential with id `jaa-webhook-token` holding JAA_WEBHOOK_TOKEN.
pipeline {
    agent any
    stages {
        stage('Build') { steps { sh 'make build' } }
    }
    post {
        failure  { notifyAnalyser() }
        unstable { notifyAnalyser() }
    }
}

def notifyAnalyser() {
    withEnv(["JAA_RESULT=${currentBuild.currentResult}"]) {
        withCredentials([string(credentialsId: 'jaa-webhook-token', variable: 'JAA_TOKEN')]) {
            // Fire-and-forget: the analyser returns 202 immediately and works in the background.
            // Single-quoted so the token is expanded by the shell, not interpolated into Groovy.
            sh '''
              curl -sS -m 10 -X POST "http://jaa.internal.example.com:8000/webhook/jenkins" \
                -H "Content-Type: application/json" \
                -H "X-JAA-Token: $JAA_TOKEN" \
                -d "{\\"job\\": \\"$JOB_NAME\\", \\"build\\": $BUILD_NUMBER, \\"result\\": \\"$JAA_RESULT\\"}" \
                || echo "AI analyser unreachable (non-fatal)"
            '''
        }
    }
}
