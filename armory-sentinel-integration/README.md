# Armory Vulnerability Processor for Azure Sentinel

Azure Function App integration that fetches Armory vulnerability data from AWS S3 (via SQS notifications) and sends it to Azure Sentinel via Log Analytics workspace.

## Overview

This serverless solution:
- Polls AWS SQS queue for S3 event notifications
- Fetches vulnerability data from S3 objects (supports JSON and gzipped files)
- Processes and transforms the data
- Sends data to Azure Sentinel via Log Analytics Data Collector API
- Runs on a configurable schedule (default: every 15 minutes)
- **Stores AWS credentials securely in Azure Key Vault** (not as plain text environment variables)

## Architecture

```
AWS S3 (Armory Data) → AWS SQS Queue → Azure Function (Timer) → Azure Key Vault (Credentials)
                                              ↓
                                    Log Analytics Workspace (Azure Sentinel)
```

## Key Security Features

✅ **AWS credentials stored in Azure Key Vault** (NOT as environment variables)  
✅ **Managed Identity** for Key Vault access (no credential management)  
✅ **RBAC authorization** with least-privilege access  
✅ **All secrets encrypted at rest**  
✅ **HTTPS-only communication**  
✅ **Comprehensive audit logging**

## Prerequisites

### Required Tools

```bash
# Azure CLI
brew install azure-cli  # macOS
# Or download from: https://docs.microsoft.com/en-us/cli/azure/install-azure-cli

# Azure Functions Core Tools v4 (REQUIRED)
brew tap azure/functions
brew install azure-functions-core-tools@4  # macOS
# Windows: npm install -g azure-functions-core-tools@4 --unsafe-perm true

# jq (JSON processor)
brew install jq  # macOS

# Verify installations
az --version
func --version  # Should show 4.x.x
jq --version
```

### Azure Requirements

- Azure subscription with permissions to create resources
- Resource group (will be created if doesn't exist)
- Log Analytics workspace for Azure Sentinel
  - Workspace ID
  - Primary or Secondary Key

### AWS Requirements

- S3 bucket containing Armory vulnerability data
- SQS queue configured to receive S3 event notifications
- IAM credentials with permissions:
  - `sqs:ReceiveMessage`, `sqs:DeleteMessage`, `sqs:GetQueueUrl`
  - `s3:GetObject`

## Deployment

### Step 1: Configure Parameters

Edit `infrastructure/azuredeploy.parameters.json` with your values:

```json
{
  "parameters": {
    "projectName": {
      "value": "armory-vuln-processor"
    },
    "location": {
      "value": "East US"
    },
    "timerSchedule": {
      "value": "0 */15 * * * *"  // Every 15 minutes
    },
    "logAnalyticsWorkspaceId": {
      "value": "YOUR_WORKSPACE_ID"  // REQUIRED
    },
    "logAnalyticsWorkspaceKey": {
      "value": "YOUR_WORKSPACE_KEY"  // REQUIRED
    },
    "awsAccessKey": {
      "value": "YOUR_AWS_ACCESS_KEY"  // REQUIRED
    },
    "awsSecretKey": {
      "value": "YOUR_AWS_SECRET_KEY"  // REQUIRED
    },
    "awsRegion": {
      "value": "us-east-1"
    },
    "awsSqsQueueName": {
      "value": "armory-vulnerabilities-queue"
    }
  }
}
```

**Get Log Analytics credentials:**

```bash
# Get Workspace ID
az monitor log-analytics workspace show \
  --resource-group <sentinel-rg> \
  --workspace-name <workspace-name> \
  --query customerId -o tsv

# Get Workspace Key
az monitor log-analytics workspace get-shared-keys \
  --resource-group <sentinel-rg> \
  --workspace-name <workspace-name> \
  --query primarySharedKey -o tsv
```

### Step 2: Deploy

```bash
# Login to Azure
az login

# Create resource group (if needed)
az group create --name armory-rg --location "East US"

# Run deployment script
./deploy.sh -g armory-rg
```

The deployment script will:
1. Deploy all Azure infrastructure (Function App, Key Vault, Storage, App Insights)
2. Store all secrets securely in Key Vault
3. Deploy the function code
4. Configure timer trigger

**Deployment time:** ~7-13 minutes

### Step 3: Verify Deployment

```bash
# Check function status
az functionapp show \
  --resource-group armory-rg \
  --name <function-app-name> \
  --query "state"
# Should output: Running

# Stream logs to watch execution
az functionapp logs tail \
  --resource-group armory-rg \
  --name <function-app-name>

# Wait for timer trigger (up to 15 minutes)
# Look for: "Armory Vulnerability Processor started"
```

### Step 4: Verify Data in Sentinel

In Azure Portal → Log Analytics Workspace → Logs, run:

```kql
ArmoryVulnerability_CL
| take 10
| project TimeGenerated, _source_s, _bucket_s
```

**Note:** Data may take 5-10 minutes to appear after first ingestion.

## Configuration

### Timer Schedule

Modify `timerSchedule` in `infrastructure/azuredeploy.parameters.json`:

| Schedule | Cron Expression |
|----------|----------------|
| Every 15 minutes | `0 */15 * * * *` |
| Every 30 minutes | `0 */30 * * * *` |
| Every hour | `0 0 * * * *` |
| Daily at 2 AM | `0 0 2 * * *` |

Format: `{second} {minute} {hour} {day} {month} {day-of-week}`

### Processing Limits

Configure in `infrastructure/azuredeploy.parameters.json`:

```json
"maxMessagesPerRun": {
  "value": 100  // Max SQS messages per execution
},
"sqsWaitTimeSeconds": {
  "value": 10   // Long polling wait time (0-20)
},
"sqsMaxMessages": {
  "value": 10   // Messages per SQS receive call (1-10)
}
```

## Monitoring

### View Logs

```bash
# Stream live logs
az functionapp logs tail --resource-group <rg> --name <app-name>

# View in Azure Portal
# Function App > Functions > ArmoryVulnerabilityProcessor > Monitor
```

### Query Data in Sentinel

```kql
// View all vulnerability data
ArmoryVulnerability_CL
| take 100

// Count by source
ArmoryVulnerability_CL
| summarize count() by _source_s
| order by count_ desc

// Recent ingestions
ArmoryVulnerability_CL
| where TimeGenerated > ago(1h)
| order by TimeGenerated desc
```

### Common Queries

```bash
# Check function app status
az functionapp show --resource-group <rg> --name <app-name> --query "state"

# List deployed functions
az functionapp function list --resource-group <rg> --name <app-name> --output table

# List Key Vault secrets
az keyvault secret list --vault-name <kv-name> --query "[].name" -o table

# Update a secret
az keyvault secret set --vault-name <kv-name> --name aws-access-key --value <new-value>

# Restart function app
az functionapp restart --resource-group <rg> --name <app-name>
```

## Troubleshooting

### Function Not Running

```bash
# Check status
az functionapp show --resource-group <rg> --name <app-name> --query "state"

# Check timer schedule
az functionapp config appsettings list \
  --resource-group <rg> \
  --name <app-name> \
  --query "[?name=='TIMER_SCHEDULE']"

# Restart function
az functionapp restart --resource-group <rg> --name <app-name>
```

### AWS Credentials Error

```bash
# Verify secrets exist in Key Vault
az keyvault secret list --vault-name <kv-name> --query "[?contains(name, 'aws')]"

# Update credentials
az keyvault secret set --vault-name <kv-name> --name aws-access-key --value <new-key>
az keyvault secret set --vault-name <kv-name> --name aws-secret-key --value <new-key>
```

### No Data in Sentinel

1. Check function logs for errors
2. Verify Log Analytics credentials in Key Vault
3. Check SQS queue has messages:
   ```bash
   aws sqs get-queue-attributes \
     --queue-url <queue-url> \
     --attribute-names ApproximateNumberOfMessages
   ```
4. Wait 5-10 minutes for data to appear (ingestion delay)

## Updates

### Code-only Update (Fast)

```bash
func azure functionapp publish <function-app-name> --build remote
```

### Full Redeployment

```bash
./deploy.sh -g <resource-group>
```

### Update Secrets

```bash
az keyvault secret set \
  --vault-name <keyvault-name> \
  --name <secret-name> \
  --value <new-value>

# Restart function app to pick up changes
az functionapp restart --resource-group <rg> --name <app-name>
```

## Security

### Credential Storage

All sensitive credentials are stored in Azure Key Vault:

| Credential | Key Vault Secret Name |
|------------|----------------------|
| AWS Access Key | `aws-access-key` |
| AWS Secret Key | `aws-secret-key` |
| AWS Region | `aws-region` |
| SQS Queue Name | `aws-sqs-queue-name` |
| Workspace ID | `azure-workspace-id` |
| Workspace Key | `azure-workspace-key` |

### Access Control

- Function App uses **System-Assigned Managed Identity**
- Managed Identity has **Key Vault Secrets Officer** role
- No credentials needed to access Key Vault
- All access is logged and auditable

### Credential Rotation

```bash
# 1. Create new AWS credentials in IAM
# 2. Update Key Vault
az keyvault secret set --vault-name <kv> --name aws-access-key --value <new>
az keyvault secret set --vault-name <kv> --name aws-secret-key --value <new>
# 3. Restart function app
az functionapp restart --resource-group <rg> --name <app>
# 4. Verify function execution
# 5. Deactivate old AWS credentials
```

## Cost Estimate

Based on Azure Consumption Plan (pay-as-you-go):

| Resource | Monthly Cost (Est.) |
|----------|---------------------|
| Function App (Consumption) | $5-10 |
| Storage Account | $1-2 |
| Key Vault | $1 |
| Application Insights | Free (< 5GB) |
| **Total** | **$7-13** |

**Assumptions:**
- 2,880 executions/month (every 15 minutes)
- 100 messages per execution
- 1 second average execution time

## Project Structure

```
armory-sentinel-integration/
├── function_app.py              # Azure Function entry point
├── vulnerabilities.py           # Main processing logic
├── helper.py                    # Key Vault and logging utilities
├── config.yaml                  # Local development config
├── requirements.txt             # Python dependencies
├── host.json                    # Function app configuration
├── local.settings.json          # Local development settings
├── deploy.sh                    # Deployment script
├── infrastructure/
│   ├── azuredeploy.json         # ARM template
│   └── azuredeploy.parameters.json  # ARM parameters
└── README.md                    # This file
```

## Local Development

### Setup

1. Configure `local.settings.json`:

```json
{
  "Values": {
    "AzureWebJobsStorage": "UseDevelopmentStorage=true",
    "AZURE_KEYVAULT_URL": "https://your-keyvault.vault.azure.net/"
  }
}
```

2. Configure `config.yaml` with your credentials

3. Run locally:

```bash
func start
```

### Testing

```bash
# Install dependencies
pip install -r requirements.txt

# Run the processor directly
python vulnerabilities.py
```

## Maintenance

### Weekly
- Review function execution logs for errors
- Check data ingestion in Sentinel

### Monthly
- Review Azure costs
- Check for updates

### Quarterly
- Rotate AWS credentials
- Update Python dependencies
- Security review

## Support

For issues or questions:
- **IT Operations**: [contact info]
- **Security Team**: [contact info]
- **Cloud Team**: [contact info]

## Resources

- [Azure Functions Documentation](https://docs.microsoft.com/en-us/azure/azure-functions/)
- [Azure Key Vault Documentation](https://docs.microsoft.com/en-us/azure/key-vault/)
- [Azure Sentinel Documentation](https://docs.microsoft.com/en-us/azure/sentinel/)
- [AWS SQS Documentation](https://docs.aws.amazon.com/sqs/)
- [AWS S3 Documentation](https://docs.aws.amazon.com/s3/)

---

**Version**: 1.0  
**Last Updated**: November 2, 2025
