param prefix string
param budgetAmount int
param budgetStartDate string
param alertEmail string
param logWorkspaceId string

resource budget 'Microsoft.Consumption/budgets@2024-08-01' = {
  name: '${prefix}-monthly'
  properties: {
    category: 'Cost'
    amount: budgetAmount
    timeGrain: 'Monthly'
    timePeriod: { startDate: budgetStartDate }
    notifications: {
      actual80: {
        enabled: true
        operator: 'GreaterThanOrEqualTo'
        threshold: 80
        thresholdType: 'Actual'
        contactEmails: [alertEmail]
      }
      actual100: {
        enabled: true
        operator: 'GreaterThanOrEqualTo'
        threshold: 100
        thresholdType: 'Actual'
        contactEmails: [alertEmail]
      }
    }
  }
}

resource actionGroup 'Microsoft.Insights/actionGroups@2023-01-01' = {
  name: '${prefix}-alerts'
  location: 'global'
  properties: {
    enabled: true
    groupShortName: 'hprice'
    emailReceivers: [{ name: 'operator', emailAddress: alertEmail, useCommonAlertSchema: true }]
  }
}

// Container console/system logs already flow from the ACA environment into this workspace.
// Grafana is the required operational alert engine over PostgreSQL observability:
// see grafana/alerts/house-price-alerts.yaml.
// Provision Grafana over private PostgreSQL connectivity after the storage/release gates.
output budgetId string = budget.id
output actionGroupId string = actionGroup.id
output logWorkspaceId string = logWorkspaceId
output operationalAlertUids array = [
  'house-price-weekly-stale'
  'house-price-inference-heartbeat'
  'house-price-quality-gate'
]
output correlationFields array = [
  'workflow_run_id'
  'training_run_id'
  'job_execution_id'
  'release_id'
  'model_version'
]
