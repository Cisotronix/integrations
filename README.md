# Armory Integrations

This directory contains integrations for Armory vulnerability data with various security platforms.

## Available Integrations

### 1. Splunk Add-on (`armory-add-on-for-splunk/`)

**Purpose**: Splunk modular input that fetches Armory vulnerability data from AWS S3 (via SQS) and indexes it in Splunk.

**Deployment**: Installed as a Splunk add-on on Splunk servers

**Documentation**: See `armory-add-on-for-splunk/README.txt`

---

### 2. Azure Sentinel Integration (`armory-sentinel-integration/`) ⭐ NEW

**Purpose**: Azure Function App that fetches Armory vulnerability data from AWS S3 (via SQS) and sends it to Azure Sentinel via Log Analytics.

**Key Features**:
- ✅ Timer-based execution (scheduled, default: every 15 minutes)
- ✅ **AWS credentials in Azure Key Vault** (NOT plain text environment variables)
- ✅ Managed Identity for secure Key Vault access
- ✅ Serverless architecture (pay-as-you-go)
- ✅ Auto-scaling and high availability
- ✅ Infrastructure as Code (ARM templates)

**Deployment**: Azure Function App (Consumption Plan)

**Documentation**: See `armory-sentinel-integration/README.md`

**Quick Start**:
```bash
cd armory-sentinel-integration
# Edit infrastructure/azuredeploy.parameters.json with your values
./deploy.sh -g <resource-group>
```

## Comparison

| Feature | Splunk Add-on | Azure Sentinel Integration |
|---------|--------------|---------------------------|
| **Platform** | Splunk | Azure Sentinel |
| **Architecture** | Modular Input | Azure Function (Serverless) |
| **Trigger** | Continuous polling | Timer-based (cron) |
| **Credentials** | Splunk config files | Azure Key Vault ✅ |
| **Scaling** | Manual | Automatic ✅ |
| **Cost Model** | Splunk license | Pay-per-execution |
| **Security** | Config file credentials | Key Vault + Managed Identity ✅ |

## Which Integration to Use?

### Use Splunk Add-on if:
- You already use Splunk for security monitoring
- You need continuous, real-time data ingestion
- You have existing Splunk infrastructure

### Use Azure Sentinel Integration if:
- You use Azure Sentinel for security monitoring
- You prefer serverless, cloud-native architecture
- You want better credential security (Key Vault)
- You need automatic scaling
- You prefer Infrastructure as Code

## Common AWS Setup

Both integrations require:

1. **S3 Bucket** with Armory vulnerability data
2. **SQS Queue** configured to receive S3 event notifications
3. **IAM Credentials** with permissions:
   - `sqs:ReceiveMessage`, `sqs:DeleteMessage`, `sqs:GetQueueUrl`
   - `s3:GetObject`

## Security Recommendation

⭐ The **Azure Sentinel integration** provides significantly better security for AWS credentials by storing them in Azure Key Vault with Managed Identity access, rather than in configuration files.

---

**Last Updated**: November 2, 2025
