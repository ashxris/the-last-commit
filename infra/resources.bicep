@description('Location for all resources.')
param location string = resourceGroup().location

@description('Base name for resources (from the azd environment).')
param name string

@description('App Service site name — becomes the public subdomain.')
param siteName string

@description('Groq API key, stored as an App Service setting.')
@secure()
param groqApiKey string

@description('Tags applied to all resources.')
param tags object = {}

var resourceToken = uniqueString(name)
var baseName = toLower(replace(name, '-', ''))
var containerRegistryName = toLower(take('${baseName}${resourceToken}acr', 50))
var planName = '${take(baseName, 20)}-plan-${resourceToken}'
var siteTags = union(tags, { 'azd-service-name': 'backend' })

resource containerRegistry 'Microsoft.ContainerRegistry/registries@2023-01-01-preview' = {
  name: containerRegistryName
  location: location
  sku: {
    name: 'Basic'
  }
  tags: tags
  properties: {
    adminUserEnabled: true
  }
}

resource serverFarm 'Microsoft.Web/serverfarms@2023-12-01' = {
  name: planName
  location: location
  kind: 'linux'
  reserved: true
  sku: {
    name: 'B1'
    capacity: 1
  }
  tags: tags
}

resource webSite 'Microsoft.Web/sites@2023-12-01' = {
  name: siteName
  location: location
  kind: 'linux'
  tags: siteTags
  properties: {
    serverFarmId: serverFarm.id
    httpsOnly: true
    siteConfig: {
      alwaysOn: true
      http20Enabled: true
      healthCheckPath: '/'
      linuxFxVersion: 'DOCKER|${containerRegistry.properties.loginServer}/the-last-commit:latest'
      appSettings: [
        {
          name: 'DOCKER_REGISTRY_SERVER_URL'
          value: 'https://${containerRegistry.properties.loginServer}'
        }
        {
          name: 'DOCKER_REGISTRY_SERVER_USERNAME'
          value: containerRegistry.listCredentials().username
        }
        {
          name: 'DOCKER_REGISTRY_SERVER_PASSWORD'
          value: containerRegistry.listCredentials().passwords[0].value
        }
        {
          name: 'WEBSITES_ENABLE_APP_SERVICE_STORAGE'
          value: 'false'
        }
        {
          name: 'GROQ_API_KEY'
          value: groqApiKey
        }
        {
          name: 'LASTCOMMIT_TRUST_PROXY'
          value: '1'
        }
        {
          name: 'LASTCOMMIT_DATA_DIR'
          value: '/home/data'
        }
      ]
    }
  }
}

output AZURE_CONTAINER_REGISTRY_ENDPOINT string = containerRegistry.properties.loginServer
output AZURE_CONTAINER_REGISTRY_NAME string = containerRegistry.name
output AZURE_LOCATION string = location
output WEB_SITE_URL string = 'https://${siteName}.azurewebsites.net'
