targetScope = 'resourceGroup'

@description('Name of the azd environment (used for resource naming).')
param name string

@description('Primary location for all resources.')
param location string = resourceGroup().location

@description('Groq API key, stored as an App Service setting.')
@secure()
param groqApiKey string

@description('App Service site name — becomes the public subdomain.')
param siteName string

module resources 'resources.bicep' = {
  name: 'resources'
  params: {
    name: name
    location: location
    siteName: siteName
    groqApiKey: groqApiKey
  }
}

output AZURE_CONTAINER_REGISTRY_ENDPOINT string = resources.outputs.AZURE_CONTAINER_REGISTRY_ENDPOINT
output AZURE_CONTAINER_REGISTRY_NAME string = resources.outputs.AZURE_CONTAINER_REGISTRY_NAME
output AZURE_LOCATION string = location
output AZURE_TENANT_ID string = tenant().tenantId
output WEB_SITE_URL string = resources.outputs.WEB_SITE_URL
