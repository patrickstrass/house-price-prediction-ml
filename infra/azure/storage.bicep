param prefix string
param location string
param storageAccountName string
param environmentName string
param vnetId string
param privateEndpointSubnetId string

resource account 'Microsoft.Storage/storageAccounts@2023-05-01' = {
  name: storageAccountName
  location: location
  sku: { name: 'Standard_LRS' }
  kind: 'StorageV2'
  properties: {
    allowBlobPublicAccess: false
    supportsHttpsTrafficOnly: true
    minimumTlsVersion: 'TLS1_2'
    allowSharedKeyAccess: true // ACA SMB mount support must be validated before deploy.
    publicNetworkAccess: 'Disabled'
  }
}

resource fileDns 'Microsoft.Network/privateDnsZones@2020-06-01' = {
  name: 'privatelink.file.${az.environment().suffixes.storage}'
  location: 'global'
}
resource fileDnsLink 'Microsoft.Network/privateDnsZones/virtualNetworkLinks@2020-06-01' = {
  parent: fileDns
  name: '${prefix}-file-link'
  location: 'global'
  properties: { registrationEnabled: false, virtualNetwork: { id: vnetId } }
}
resource fileEndpoint 'Microsoft.Network/privateEndpoints@2024-05-01' = {
  name: '${prefix}-file-pe'
  location: location
  properties: {
    subnet: { id: privateEndpointSubnetId }
    privateLinkServiceConnections: [{
      name: 'file'
      properties: { privateLinkServiceId: account.id, groupIds: ['file'] }
    }]
  }
}
resource fileEndpointDns 'Microsoft.Network/privateEndpoints/privateDnsZoneGroups@2024-05-01' = {
  parent: fileEndpoint
  name: 'file-zone'
  properties: { privateDnsZoneConfigs: [{ name: 'file', properties: { privateDnsZoneId: fileDns.id } }] }
  dependsOn: [fileDnsLink]
}

resource files 'Microsoft.Storage/storageAccounts/fileServices@2023-05-01' = {
  parent: account
  name: 'default'
}

resource artifactShare 'Microsoft.Storage/storageAccounts/fileServices/shares@2023-05-01' = {
  parent: files
  name: 'mlflow-artifacts'
  properties: { shareQuota: 20 }
}
resource registryShare 'Microsoft.Storage/storageAccounts/fileServices/shares@2023-05-01' = {
  parent: files
  name: 'model-registry'
  properties: { shareQuota: 20 }
}
resource backupShare 'Microsoft.Storage/storageAccounts/fileServices/shares@2023-05-01' = {
  parent: files
  name: 'backup-evidence'
  properties: { shareQuota: 20 }
}

resource environment 'Microsoft.App/managedEnvironments@2025-01-01' existing = {
  name: environmentName
}

resource artifactsMount 'Microsoft.App/managedEnvironments/storages@2025-01-01' = {
  parent: environment
  name: 'mlflow-artifacts-rw'
  properties: {
    azureFile: {
      accountName: account.name
      shareName: artifactShare.name
      accessMode: 'ReadWrite'
      accountKey: account.listKeys().keys[0].value
    }
  }
}
resource artifactsMountRo 'Microsoft.App/managedEnvironments/storages@2025-01-01' = {
  parent: environment
  name: 'mlflow-artifacts-ro'
  properties: {
    azureFile: {
      accountName: account.name
      shareName: artifactShare.name
      accessMode: 'ReadOnly'
      accountKey: account.listKeys().keys[0].value
    }
  }
}
resource registryMountRw 'Microsoft.App/managedEnvironments/storages@2025-01-01' = {
  parent: environment
  name: 'model-registry-rw'
  properties: {
    azureFile: {
      accountName: account.name
      shareName: registryShare.name
      accessMode: 'ReadWrite'
      accountKey: account.listKeys().keys[0].value
    }
  }
}
resource registryMountRo 'Microsoft.App/managedEnvironments/storages@2025-01-01' = {
  parent: environment
  name: 'model-registry-ro'
  properties: {
    azureFile: {
      accountName: account.name
      shareName: registryShare.name
      accessMode: 'ReadOnly'
      accountKey: account.listKeys().keys[0].value
    }
  }
}
resource backupMount 'Microsoft.App/managedEnvironments/storages@2025-01-01' = {
  parent: environment
  name: 'backup-evidence-rw'
  properties: {
    azureFile: {
      accountName: account.name
      shareName: backupShare.name
      accessMode: 'ReadWrite'
      accountKey: account.listKeys().keys[0].value
    }
  }
}
resource backupMountRo 'Microsoft.App/managedEnvironments/storages@2025-01-01' = {
  parent: environment
  name: 'backup-evidence-ro'
  properties: {
    azureFile: {
      accountName: account.name
      shareName: backupShare.name
      accessMode: 'ReadOnly'
      accountKey: account.listKeys().keys[0].value
    }
  }
}

output accountId string = account.id
output artifactShareId string = artifactShare.id
output registryShareId string = registryShare.id
output backupShareId string = backupShare.id
