param prefix string
param location string
param skuName string
param storageGiB int
param adminLogin string
@secure()
param adminPassword string
param subnetId string
param vnetId string

resource zone 'Microsoft.Network/privateDnsZones@2020-06-01' = {
  name: '${prefix}.private.postgres.database.azure.com'
  location: 'global'
}

resource dnsLink 'Microsoft.Network/privateDnsZones/virtualNetworkLinks@2020-06-01' = {
  parent: zone
  name: '${prefix}-link'
  location: 'global'
  properties: {
    registrationEnabled: false
    virtualNetwork: { id: vnetId }
  }
}

resource server 'Microsoft.DBforPostgreSQL/flexibleServers@2024-08-01' = {
  name: '${prefix}-pg'
  location: location
  sku: { name: skuName, tier: 'Burstable' }
  properties: {
    version: '17'
    administratorLogin: adminLogin
    administratorLoginPassword: adminPassword
    network: {
      delegatedSubnetResourceId: subnetId
      privateDnsZoneArmResourceId: zone.id
      publicNetworkAccess: 'Disabled'
    }
    storage: { storageSizeGB: storageGiB }
    backup: { backupRetentionDays: 7, geoRedundantBackup: 'Disabled' }
    highAvailability: { mode: 'Disabled' }
  }
  dependsOn: [dnsLink]
}

resource housePriceDb 'Microsoft.DBforPostgreSQL/flexibleServers/databases@2024-08-01' = {
  parent: server
  name: 'house_price'
}
resource mlflowDb 'Microsoft.DBforPostgreSQL/flexibleServers/databases@2024-08-01' = {
  parent: server
  name: 'mlflow'
}
resource optunaDb 'Microsoft.DBforPostgreSQL/flexibleServers/databases@2024-08-01' = {
  parent: server
  name: 'optuna'
}

output serverId string = server.id
output serverFqdn string = server.properties.fullyQualifiedDomainName
