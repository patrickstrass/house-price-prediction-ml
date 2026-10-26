param prefix string
param location string
param environmentId string
param inferenceIdentityId string
param mlflowIdentityId string
param registryServer string
param inferenceImage string
param mlflowImage string
param databaseSecretUri string
param mlflowSecretUri string

resource inference 'Microsoft.App/containerApps@2025-01-01' = {
  name: '${prefix}-inference'
  location: location
  identity: { type: 'UserAssigned', userAssignedIdentities: { '${inferenceIdentityId}': {} } }
  properties: {
    managedEnvironmentId: environmentId
    configuration: {
      activeRevisionsMode: 'Multiple'
      ingress: { external: true, targetPort: 8000, transport: 'http', allowInsecure: false }
      registries: [{ server: registryServer, identity: inferenceIdentityId }]
      secrets: [
        { name: 'database-url', keyVaultUrl: databaseSecretUri, identity: inferenceIdentityId }
      ]
    }
    template: {
      containers: [{
        name: 'inference'
        image: inferenceImage
        resources: { cpu: json('0.5'), memory: '1Gi' }
        env: [
          { name: 'DATABASE_URL', secretRef: 'database-url' }
          { name: 'MODEL_REGISTRY_PATH', value: '/model-registry' }
          { name: 'MLFLOW_TRACKING_URI', value: 'http://${prefix}-mlflow' }
          { name: 'AZURE_DEPLOYMENT', value: 'true' }
        ]
        volumeMounts: [{ volumeName: 'registry', mountPath: '/model-registry' }]
        probes: [
          { type: 'Readiness', httpGet: { path: '/health/ready', port: 8000 }, periodSeconds: 10 }
          { type: 'Liveness', httpGet: { path: '/health/live', port: 8000 }, periodSeconds: 30 }
        ]
      }]
      volumes: [{ name: 'registry', storageType: 'AzureFile', storageName: 'model-registry-ro' }]
      scale: { minReplicas: 1, maxReplicas: 1 }
    }
  }
}

resource mlflow 'Microsoft.App/containerApps@2025-01-01' = {
  name: '${prefix}-mlflow'
  location: location
  identity: { type: 'UserAssigned', userAssignedIdentities: { '${mlflowIdentityId}': {} } }
  properties: {
    managedEnvironmentId: environmentId
    configuration: {
      activeRevisionsMode: 'Single'
      ingress: { external: false, targetPort: 5000, transport: 'http', allowInsecure: false }
      registries: [{ server: registryServer, identity: mlflowIdentityId }]
      secrets: [{ name: 'mlflow-backend', keyVaultUrl: mlflowSecretUri, identity: mlflowIdentityId }]
    }
    template: {
      containers: [{
        name: 'mlflow'
        image: mlflowImage
        resources: { cpu: json('0.5'), memory: '2Gi' }
        command: ['/usr/local/bin/start-mlflow-azure']
        env: [
          { name: 'MLFLOW_BACKEND_STORE_URI', secretRef: 'mlflow-backend' }
          { name: 'MLFLOW_ALLOWED_HOSTS', value: '${prefix}-mlflow,${prefix}-mlflow:5000' }
        ]
        volumeMounts: [{ volumeName: 'artifacts', mountPath: '/mlflow-artifacts' }]
        probes: [{ type: 'Readiness', httpGet: { path: '/health', port: 5000 }, periodSeconds: 10 }]
      }]
      volumes: [{ name: 'artifacts', storageType: 'AzureFile', storageName: 'mlflow-artifacts-rw' }]
      scale: { minReplicas: 1, maxReplicas: 1 }
    }
  }
}

output inferenceId string = inference.id
output mlflowId string = mlflow.id
