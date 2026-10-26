param prefix string
param location string
param githubRepository string
param githubEnvironment string
param registryId string
param keyVaultName string
param databaseSecretName string
param migrationSecretName string
param optunaSecretName string
param mlflowSecretName string

var runtimeNames = [
  'inference'
  'mlflow'
  'backup'
  'mlflow-migrate'
  'catalog-reconcile'
  'verify'
  'trainer'
  'release-sync'
  'release-rollback'
]

resource runtimes 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' = [for name in runtimeNames: {
  name: '${prefix}-${name}-identity'
  location: location
}]

resource github 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' = {
  name: '${prefix}-github'
  location: location
}

resource federation 'Microsoft.ManagedIdentity/userAssignedIdentities/federatedIdentityCredentials@2023-01-31' = {
  parent: github
  name: 'github-environment'
  properties: {
    issuer: 'https://token.actions.githubusercontent.com'
    subject: 'repo:${githubRepository}:environment:${githubEnvironment}'
    audiences: ['api://AzureADTokenExchange']
  }
}

resource acr 'Microsoft.ContainerRegistry/registries@2023-07-01' existing = {
  name: last(split(registryId, '/'))
}

resource acrPull 'Microsoft.Authorization/roleAssignments@2022-04-01' = [for (name, i) in runtimeNames: {
  name: guid(acr.id, runtimes[i].id, 'AcrPull')
  scope: acr
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '7f951dda-4ed3-4680-a7ca-43fe172d538d')
    principalId: runtimes[i].properties.principalId
    principalType: 'ServicePrincipal'
  }
}]

resource acrPush 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(acr.id, github.id, 'AcrPush')
  scope: acr
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '8311e382-0749-4cb8-b61a-304f252e45ec')
    principalId: github.properties.principalId
    principalType: 'ServicePrincipal'
  }
}

resource vault 'Microsoft.KeyVault/vaults@2023-07-01' existing = {
  name: keyVaultName
}

resource databaseSecret 'Microsoft.KeyVault/vaults/secrets@2023-07-01' existing = {
  parent: vault
  name: databaseSecretName
}
resource migrationSecret 'Microsoft.KeyVault/vaults/secrets@2023-07-01' existing = {
  parent: vault
  name: migrationSecretName
}
resource optunaSecret 'Microsoft.KeyVault/vaults/secrets@2023-07-01' existing = {
  parent: vault
  name: optunaSecretName
}
resource mlflowSecret 'Microsoft.KeyVault/vaults/secrets@2023-07-01' existing = {
  parent: vault
  name: mlflowSecretName
}

var databaseReaders = [0, 4, 5, 6, 8]
var migrationReaders = [2, 4]
var optunaReaders = [2, 4, 5, 6]
var mlflowReaders = [1, 2, 3]
var secretsUserRole = subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '4633458b-17de-408a-b874-0445c86b69e6')

resource databaseGrants 'Microsoft.Authorization/roleAssignments@2022-04-01' = [for index in databaseReaders: {
  name: guid(databaseSecret.id, runtimes[index].id, 'SecretsUser')
  scope: databaseSecret
  properties: { roleDefinitionId: secretsUserRole, principalId: runtimes[index].properties.principalId, principalType: 'ServicePrincipal' }
}]
resource migrationGrants 'Microsoft.Authorization/roleAssignments@2022-04-01' = [for index in migrationReaders: {
  name: guid(migrationSecret.id, runtimes[index].id, 'SecretsUser')
  scope: migrationSecret
  properties: { roleDefinitionId: secretsUserRole, principalId: runtimes[index].properties.principalId, principalType: 'ServicePrincipal' }
}]
resource optunaGrants 'Microsoft.Authorization/roleAssignments@2022-04-01' = [for index in optunaReaders: {
  name: guid(optunaSecret.id, runtimes[index].id, 'SecretsUser')
  scope: optunaSecret
  properties: { roleDefinitionId: secretsUserRole, principalId: runtimes[index].properties.principalId, principalType: 'ServicePrincipal' }
}]
resource mlflowGrants 'Microsoft.Authorization/roleAssignments@2022-04-01' = [for index in mlflowReaders: {
  name: guid(mlflowSecret.id, runtimes[index].id, 'SecretsUser')
  scope: mlflowSecret
  properties: { roleDefinitionId: secretsUserRole, principalId: runtimes[index].properties.principalId, principalType: 'ServicePrincipal' }
}]

output runtimeIdentityIds object = {
  inference: runtimes[0].id
  mlflow: runtimes[1].id
  backup: runtimes[2].id
  'mlflow-migrate': runtimes[3].id
  'catalog-reconcile': runtimes[4].id
  verify: runtimes[5].id
  trainer: runtimes[6].id
  'release-sync': runtimes[7].id
  'release-rollback': runtimes[8].id
}
output githubIdentityClientId string = github.properties.clientId
