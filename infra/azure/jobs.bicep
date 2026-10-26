param prefix string
param location string
param environmentId string
param identityIds object
param registryServer string
param trainerImage string
param mlflowImage string
param databaseSecretUri string
param migrationSecretUri string
param optunaSecretUri string
param mlflowSecretUri string

var databaseSecret = { name: 'database-url', uri: databaseSecretUri, env: 'DATABASE_URL' }
var migrationSecret = { name: 'migration-url', uri: migrationSecretUri, env: 'MIGRATION_DATABASE_URL' }
var optunaSecret = { name: 'optuna-url', uri: optunaSecretUri, env: 'OPTUNA_STORAGE_URL' }
var mlflowSecret = { name: 'mlflow-backend-url', uri: mlflowSecretUri, env: 'MLFLOW_BACKEND_STORE_URI' }

var definitions = [
  { name: 'backup', image: trainerImage, command: ['/usr/local/bin/azure-backup-job'], args: [], secrets: [migrationSecret, optunaSecret, mlflowSecret] }
  { name: 'mlflow-migrate', image: mlflowImage, command: ['/usr/local/bin/migrate-mlflow'], args: [], secrets: [mlflowSecret] }
  { name: 'catalog-reconcile', image: trainerImage, command: ['/usr/local/bin/reconcile-catalog'], args: [], secrets: [databaseSecret, migrationSecret, optunaSecret] }
  { name: 'verify', image: trainerImage, command: ['model-catalog'], args: ['verify', '--probe-artifacts', '--json'], secrets: [databaseSecret, optunaSecret] }
  { name: 'trainer', image: trainerImage, command: ['train'], args: [], secrets: [databaseSecret, optunaSecret] }
  { name: 'release-sync', image: trainerImage, command: ['python', '-m', 'house_price.azure.release_sync'], args: [], secrets: [] }
  { name: 'release-rollback', image: trainerImage, command: ['python', '-m', 'house_price.azure.rollback_job'], args: [], secrets: [databaseSecret] }
]

resource jobs 'Microsoft.App/jobs@2025-01-01' = [for definition in definitions: {
  name: '${prefix}-${definition.name}'
  location: location
  identity: { type: 'UserAssigned', userAssignedIdentities: { '${identityIds[definition.name]}': {} } }
  properties: {
    environmentId: environmentId
    configuration: {
      triggerType: 'Manual'
      replicaTimeout: 3600
      replicaRetryLimit: 0
      manualTriggerConfig: { parallelism: 1, replicaCompletionCount: 1 }
      registries: [{ server: registryServer, identity: identityIds[definition.name] }]
      secrets: [for secret in definition.secrets: {
        name: secret.name
        keyVaultUrl: secret.uri
        identity: identityIds[definition.name]
      }]
    }
    template: {
      containers: [{
        name: definition.name
        image: definition.image
        resources: { cpu: json('0.5'), memory: '1Gi' }
        command: definition.command
        args: definition.args
        env: concat(map(definition.secrets, secret => { name: secret.env, secretRef: secret.name }), [
          { name: 'MLFLOW_MIGRATION_BACKUP_DIR', value: '/backup' }
          { name: 'CURRENT_RELEASE_PATH', value: '/backup/current-release.json' }
          { name: 'MLFLOW_TRACKING_URI', value: 'http://${prefix}-mlflow' }
          { name: 'MODEL_REGISTRY_PATH', value: '/model-registry' }
          { name: 'AZURE_DEPLOYMENT', value: 'true' }
        ])
        volumeMounts: definition.name == 'backup' ? [
          { volumeName: 'registry', mountPath: '/model-registry' }
          { volumeName: 'artifacts', mountPath: '/mlflow-artifacts' }
          { volumeName: 'backup', mountPath: '/backup' }
        ] : definition.name == 'release-rollback' ? [
          { volumeName: 'registry', mountPath: '/model-registry' }
          { volumeName: 'backup', mountPath: '/backup' }
        ] : definition.name == 'trainer' || definition.name == 'catalog-reconcile' ? [
          { volumeName: 'registry', mountPath: '/model-registry' }
        ] : definition.name == 'verify' ? [
          { volumeName: 'registry', mountPath: '/model-registry' }
        ] : [
          { volumeName: 'backup', mountPath: '/backup' }
        ]
      }]
      volumes: definition.name == 'backup' ? [
        { name: 'registry', storageType: 'AzureFile', storageName: 'model-registry-ro' }
        { name: 'artifacts', storageType: 'AzureFile', storageName: 'mlflow-artifacts-ro' }
        { name: 'backup', storageType: 'AzureFile', storageName: 'backup-evidence-rw' }
      ] : definition.name == 'release-rollback' ? [
        { name: 'registry', storageType: 'AzureFile', storageName: 'model-registry-rw' }
        { name: 'backup', storageType: 'AzureFile', storageName: 'backup-evidence-ro' }
      ] : definition.name == 'trainer' || definition.name == 'catalog-reconcile' ? [
        { name: 'registry', storageType: 'AzureFile', storageName: 'model-registry-rw' }
      ] : definition.name == 'verify' ? [
        { name: 'registry', storageType: 'AzureFile', storageName: 'model-registry-ro' }
      ] : [
        { name: 'backup', storageType: 'AzureFile', storageName: 'backup-evidence-rw' }
      ]
    }
  }
}]

output jobIds array = [for (definition, i) in definitions: jobs[i].id]
