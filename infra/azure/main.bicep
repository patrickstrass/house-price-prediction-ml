targetScope = 'resourceGroup'

@description('Unique lower-case project prefix; review availability before deployment.')
param prefix string
param location string = resourceGroup().location
param postgresSku string
param postgresStorageGiB int
param inferenceImage string
param mlflowImage string
param trainerImage string
param databaseSecretUri string
param migrationSecretUri string
param optunaSecretUri string
param mlflowSecretUri string
@secure()
param postgresAdminPassword string
param postgresAdminLogin string = 'hprice_admin'
param githubRepository string
param githubEnvironment string = 'azure-production'
param keyVaultName string
param budgetAmount int
param budgetStartDate string
param alertEmail string


var tags = { project: 'house-price-mlops', feature: '003-azure-preparation' }
var storageAccountName = replace(toLower('${prefix}${uniqueString(resourceGroup().id)}'), '-', '')

resource network 'Microsoft.Network/virtualNetworks@2024-05-01' = {
  name: '${prefix}-vnet'
  location: location
  tags: tags
  properties: {
    addressSpace: { addressPrefixes: ['10.80.0.0/16'] }
    subnets: [
      { name: 'aca', properties: { addressPrefix: '10.80.0.0/23', delegations: [{ name: 'aca', properties: { serviceName: 'Microsoft.App/environments' } }] } }
      { name: 'postgres', properties: { addressPrefix: '10.80.2.0/28', delegations: [{ name: 'postgres', properties: { serviceName: 'Microsoft.DBforPostgreSQL/flexibleServers' } }] } }
      { name: 'storage', properties: { addressPrefix: '10.80.3.0/27', privateEndpointNetworkPolicies: 'Disabled' } }
    ]
  }
}

resource acaSubnet 'Microsoft.Network/virtualNetworks/subnets@2024-05-01' existing = {
  parent: network
  name: 'aca'
}
resource postgresSubnet 'Microsoft.Network/virtualNetworks/subnets@2024-05-01' existing = {
  parent: network
  name: 'postgres'
}
resource storageSubnet 'Microsoft.Network/virtualNetworks/subnets@2024-05-01' existing = {
  parent: network
  name: 'storage'
}

resource logs 'Microsoft.OperationalInsights/workspaces@2023-09-01' = {
  name: '${prefix}-logs'
  location: location
  tags: tags
  properties: { sku: { name: 'PerGB2018' }, retentionInDays: 30 }
}

resource environment 'Microsoft.App/managedEnvironments@2025-01-01' = {
  name: '${prefix}-aca'
  location: location
  tags: tags
  identity: { type: 'SystemAssigned' }
  properties: {
    vnetConfiguration: { infrastructureSubnetId: acaSubnet.id }
    appLogsConfiguration: {
      destination: 'log-analytics'
      logAnalyticsConfiguration: {
        customerId: logs.properties.customerId
        sharedKey: logs.listKeys().primarySharedKey
      }
    }
  }
}

resource registry 'Microsoft.ContainerRegistry/registries@2023-07-01' = {
  name: replace('${prefix}${uniqueString(resourceGroup().id)}', '-', '')
  location: location
  tags: tags
  sku: { name: 'Basic' }
  properties: { adminUserEnabled: false, publicNetworkAccess: 'Enabled' }
}

module identity './identity.bicep' = {
  name: 'identities'
  params: {
    prefix: prefix
    location: location
    githubRepository: githubRepository
    githubEnvironment: githubEnvironment
    registryId: registry.id
    keyVaultName: keyVaultName
    databaseSecretName: split(databaseSecretUri, '/')[4]
    migrationSecretName: split(migrationSecretUri, '/')[4]
    optunaSecretName: split(optunaSecretUri, '/')[4]
    mlflowSecretName: split(mlflowSecretUri, '/')[4]
  }
}

module storage './storage.bicep' = {
  name: 'storage'
  params: {
    prefix: prefix
    location: location
    storageAccountName: storageAccountName
    environmentName: environment.name
    vnetId: network.id
    privateEndpointSubnetId: storageSubnet.id
  }
}

module postgres './postgres.bicep' = {
  name: 'postgres'
  params: {
    prefix: prefix
    location: location
    skuName: postgresSku
    storageGiB: postgresStorageGiB
    adminLogin: postgresAdminLogin
    adminPassword: postgresAdminPassword
    subnetId: postgresSubnet.id
    vnetId: network.id
  }
}

module apps './container-apps.bicep' = {
  name: 'apps'
  params: {
    prefix: prefix
    location: location
    environmentId: environment.id
    inferenceIdentityId: identity.outputs.runtimeIdentityIds.inference
    mlflowIdentityId: identity.outputs.runtimeIdentityIds.mlflow
    registryServer: registry.properties.loginServer
    inferenceImage: inferenceImage
    mlflowImage: mlflowImage
    databaseSecretUri: databaseSecretUri
    mlflowSecretUri: mlflowSecretUri
  }
  dependsOn: [storage]
}

module jobs './jobs.bicep' = {
  name: 'jobs'
  params: {
    prefix: prefix
    location: location
    environmentId: environment.id
    identityIds: identity.outputs.runtimeIdentityIds
    registryServer: registry.properties.loginServer
    trainerImage: trainerImage
    mlflowImage: mlflowImage
    databaseSecretUri: databaseSecretUri
    migrationSecretUri: migrationSecretUri
    optunaSecretUri: optunaSecretUri
    mlflowSecretUri: mlflowSecretUri
  }
  dependsOn: [storage, postgres]
}

module monitoring './monitoring.bicep' = {
  name: 'monitoring'
  params: {
    prefix: prefix
    budgetAmount: budgetAmount
    budgetStartDate: budgetStartDate
    alertEmail: alertEmail
    logWorkspaceId: logs.id
  }
}

output environmentId string = environment.id
output registryId string = registry.id
output postgresId string = postgres.outputs.serverId
output storageAccountId string = storage.outputs.accountId
output inferenceAppId string = apps.outputs.inferenceId
