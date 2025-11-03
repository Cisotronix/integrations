#!/bin/bash

# Armory Vulnerability Processor - Azure Deployment Script
# This script deploys the complete infrastructure and function code using ARM template parameters

set -e  # Exit on any error

# Default values
RESOURCE_GROUP=""
PARAMETERS_FILE="infrastructure/azuredeploy.parameters.json"
TEMPLATE_FILE="infrastructure/azuredeploy.json"

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Helper functions
log_info() {
    echo -e "${BLUE}[INFO]${NC} $1"
}

log_success() {
    echo -e "${GREEN}[SUCCESS]${NC} $1"
}

log_warning() {
    echo -e "${YELLOW}[WARNING]${NC} $1"
}

log_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

# Function to display usage
usage() {
    cat << EOF
Usage: $0 -g RESOURCE_GROUP [OPTIONS]

Deploy Armory Vulnerability Processor to Azure Function App with Key Vault integration.
All configuration is read from the ARM template parameters file.

OPTIONS:
    -g, --resource-group RESOURCE_GROUP    Azure resource group name (required, must exist)
    -p, --parameters-file PARAMETERS_FILE  ARM template parameters file (default: infrastructure/azuredeploy.parameters.json)
    -t, --template-file TEMPLATE_FILE      ARM template file (default: infrastructure/azuredeploy.json)
    -h, --help                             Display this help message

EXAMPLES:
    # Basic deployment using default parameters file
    $0 -g my-resource-group
    
    # Deployment with custom parameters file
    $0 -g my-resource-group -p my-custom-parameters.json

PREREQUISITES:
    - Azure CLI installed and logged in
    - Azure Functions Core Tools v4 installed (REQUIRED)
    - Resource group must already exist
    - Update parameters in azuredeploy.parameters.json before deployment
    - Sufficient permissions to create resources

INSTALLATION:
    Azure Functions Core Tools:
    - macOS:   brew tap azure/functions && brew install azure-functions-core-tools@4
    - Windows: npm install -g azure-functions-core-tools@4 --unsafe-perm true
    - Linux:   https://docs.microsoft.com/en-us/azure/azure-functions/functions-run-local

CONFIGURATION:
    Edit the parameters file (infrastructure/azuredeploy.parameters.json) to configure:
    - Project name and location
    - Timer schedule
    - Log Analytics workspace ID and key
    - AWS credentials (access key, secret key, region, SQS queue name)
    - Processing parameters

SECURITY:
    - AWS credentials are stored securely in Azure Key Vault
    - Credentials are NOT exposed as plain text environment variables
    - Function App uses Managed Identity to access Key Vault
    - All secrets are encrypted at rest

EOF
}

# Parse command line arguments
while [[ $# -gt 0 ]]; do
    case $1 in
        -g|--resource-group)
            RESOURCE_GROUP="$2"
            shift 2
            ;;
        -p|--parameters-file)
            PARAMETERS_FILE="$2"
            shift 2
            ;;
        -t|--template-file)
            TEMPLATE_FILE="$2"
            shift 2
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        *)
            log_error "Unknown option: $1"
            usage
            exit 1
            ;;
    esac
done

# Validate required parameters
if [[ -z "$RESOURCE_GROUP" ]]; then
    log_error "Resource group is required. Use -g or --resource-group"
    exit 1
fi

# Validate files exist
if [[ ! -f "$TEMPLATE_FILE" ]]; then
    log_error "ARM template file not found: $TEMPLATE_FILE"
    exit 1
fi

if [[ ! -f "$PARAMETERS_FILE" ]]; then
    log_error "Parameters file not found: $PARAMETERS_FILE"
    exit 1
fi

# Check if Azure CLI is installed
if ! command -v az &> /dev/null; then
    log_error "Azure CLI is not installed. Please install it first."
    log_info "Install from: https://docs.microsoft.com/en-us/cli/azure/install-azure-cli"
    exit 1
fi

# Check if Azure Functions Core Tools is installed (REQUIRED)
if ! command -v func &> /dev/null; then
    log_error "Azure Functions Core Tools is REQUIRED for deployment."
    echo ""
    log_info "Install Azure Functions Core Tools:"
    echo "  macOS:   brew tap azure/functions && brew install azure-functions-core-tools@4"
    echo "  Windows: npm install -g azure-functions-core-tools@4 --unsafe-perm true"
    echo "  Linux:   See https://docs.microsoft.com/en-us/azure/azure-functions/functions-run-local"
    echo ""
    log_error "Deployment cannot continue without Azure Functions Core Tools."
    exit 1
fi

# Check if jq is installed (REQUIRED for JSON parsing)
if ! command -v jq &> /dev/null; then
    log_error "jq is REQUIRED for parsing deployment outputs."
    echo ""
    log_info "Install jq:"
    echo "  macOS:   brew install jq"
    echo "  Windows: choco install jq  OR  download from https://stedolan.github.io/jq/"
    echo "  Linux:   apt-get install jq  OR  yum install jq"
    echo ""
    log_error "Deployment cannot continue without jq."
    exit 1
fi

# Verify func CLI version
FUNC_VERSION=$(func --version | head -1)
log_info "Using Azure Functions Core Tools: $FUNC_VERSION"

# Check if user is logged in
if ! az account show &> /dev/null; then
    log_error "Not logged in to Azure. Please run 'az login' first."
    exit 1
fi

# Get current subscription info
CURRENT_SUBSCRIPTION=$(az account show --query name -o tsv)
log_info "Deploying to subscription: $CURRENT_SUBSCRIPTION"

# Check if resource group exists
log_info "Checking resource group: $RESOURCE_GROUP"
if ! az group show --name "$RESOURCE_GROUP" &> /dev/null; then
    log_error "Resource group '$RESOURCE_GROUP' does not exist. Please create it first."
    exit 1
fi

log_info "Resource group exists"

# Deploy infrastructure
log_info "Deploying infrastructure using ARM template..."
DEPLOYMENT_NAME="armory-deployment-$(date +%s)"

# Deploy ARM template with --no-wait
log_info "Starting ARM template deployment..."
az deployment group create \
    --resource-group "$RESOURCE_GROUP" \
    --name "$DEPLOYMENT_NAME" \
    --template-file "$TEMPLATE_FILE" \
    --parameters "@$PARAMETERS_FILE" \
    --no-wait

if [[ $? -eq 0 ]]; then
    log_info "Deployment submitted successfully with --no-wait"
else
    log_error "Failed to submit deployment"
    exit 1
fi

# Now manually poll for the deployment status
log_info "Polling for deployment status..."
COUNTER=0
MAX_ATTEMPTS=60  # 30 minutes (30 seconds * 60)

sleep 10

while [[ $COUNTER -lt $MAX_ATTEMPTS ]]; do
    log_info "Attempt $((COUNTER + 1))/$MAX_ATTEMPTS - Checking deployment status..."
    
    # Try to get deployment status
    DEPLOYMENT_STATUS=$(az deployment group show \
        --resource-group "$RESOURCE_GROUP" \
        --name "$DEPLOYMENT_NAME" \
        --query "properties.provisioningState" \
        --output tsv 2>/dev/null)
    
    if [[ $? -eq 0 ]]; then
        log_info "Deployment status: $DEPLOYMENT_STATUS"
        
        case "$DEPLOYMENT_STATUS" in
            "Succeeded")
                log_success "Deployment completed successfully!"
                break
                ;;
            "Failed")
                log_error "Deployment failed"
                # Show error details
                az deployment group show \
                    --resource-group "$RESOURCE_GROUP" \
                    --name "$DEPLOYMENT_NAME" \
                    --query "properties.error"
                exit 1
                ;;
            "Running"|"Accepted")
                log_info "Deployment is still in progress..."
                ;;
            *)
                log_info "Deployment status: $DEPLOYMENT_STATUS"
                ;;
        esac
    else
        log_warning "Could not retrieve deployment status (deployment may not be visible yet)"
    fi
    
    COUNTER=$((COUNTER + 1))
    if [[ $COUNTER -lt $MAX_ATTEMPTS ]]; then
        sleep 30
    fi
done

if [[ $COUNTER -ge $MAX_ATTEMPTS ]]; then
    log_error "Timed out waiting for deployment to complete"
    exit 1
fi

# Get deployment outputs
log_info "Retrieving deployment outputs..."
DEPLOYMENT_OUTPUT=$(az deployment group show \
    --resource-group "$RESOURCE_GROUP" \
    --name "$DEPLOYMENT_NAME" \
    --output json)

if [[ $? -eq 0 ]]; then
    # Extract outputs
    FUNCTION_APP_NAME=$(echo "$DEPLOYMENT_OUTPUT" | jq -r '.properties.outputs.functionAppName.value')
    KEY_VAULT_NAME=$(echo "$DEPLOYMENT_OUTPUT" | jq -r '.properties.outputs.keyVaultName.value')
    KEY_VAULT_URI=$(echo "$DEPLOYMENT_OUTPUT" | jq -r '.properties.outputs.keyVaultUri.value')
    
    log_info "Function App: $FUNCTION_APP_NAME"
    log_info "Key Vault: $KEY_VAULT_NAME"
    log_info "Key Vault URI: $KEY_VAULT_URI"
    
    # Runtime configuration is now handled by ARM template
    log_info "Verifying runtime configuration..."
    
    # Verify Python runtime is correctly set
    RUNTIME_VERSION=$(az functionapp config show \
        --resource-group "$RESOURCE_GROUP" \
        --name "$FUNCTION_APP_NAME" \
        --query "linuxFxVersion" \
        --output tsv)
    
    log_info "Function App runtime: $RUNTIME_VERSION"
else
    log_error "Infrastructure deployment failed"
    exit 1
fi

# Deploy function code using Azure Functions Core Tools (required)
log_info "Deploying function code using Azure Functions Core Tools..."
log_info "This provides better dependency management and deployment reliability for Azure Functions"

# Deploy using func CLI with remote build for Python
log_info "Deploying to Function App: $FUNCTION_APP_NAME"
func azure functionapp publish "$FUNCTION_APP_NAME" \
    --build remote \
    --no-bundler

if [[ $? -eq 0 ]]; then
    log_success "Function deployment completed successfully"
else
    log_error "Function deployment failed"
    exit 1
fi

# Verify deployment
log_info "Verifying Function App status..."
FUNCTION_STATUS=$(az functionapp show \
    --resource-group "$RESOURCE_GROUP" \
    --name "$FUNCTION_APP_NAME" \
    --query "state" \
    --output tsv)

if [[ "$FUNCTION_STATUS" == "Running" ]]; then
    log_success "Function App is running successfully"
    
    # List deployed functions
    log_info "Deployed functions:"
    az functionapp function list \
        --resource-group "$RESOURCE_GROUP" \
        --name "$FUNCTION_APP_NAME" \
        --query "[].{Name:name, TriggerType:config.bindings[0].type}" \
        --output table
else
    log_warning "Function App status: $FUNCTION_STATUS"
    log_info "The app may need a few moments to initialize..."
fi

# Display completion
log_success "Deployment completed successfully!"
echo ""
log_info "Resources deployed:"
log_info "- Function App: $FUNCTION_APP_NAME"
log_info "- Key Vault: $KEY_VAULT_NAME"
echo ""
log_info "All secrets (including AWS credentials) have been configured securely in Key Vault."
log_info "AWS credentials are NOT exposed as plain text environment variables."
log_info "The Function App uses Managed Identity to access Key Vault securely."
echo ""
log_info "Next steps:"
echo "1. Monitor function execution (timer runs every 15 minutes by default):"
echo "   az functionapp logs tail --resource-group '$RESOURCE_GROUP' --name '$FUNCTION_APP_NAME'"
echo ""
echo "2. Test timer trigger manually (if needed):"
echo "   # Timer trigger executes automatically - check logs for execution"
echo ""
echo "3. Monitor logs and data:"
echo "   - Azure Portal: Function App > Functions > ArmoryVulnerabilityProcessor > Monitor"
echo "   - Application Insights: Search for timer trigger logs"
echo "   - Log Analytics: Query ArmoryVulnerability_CL table for ingested data"
echo ""
echo "4. Code-only redeployment (faster for updates):"
echo "   func azure functionapp publish '$FUNCTION_APP_NAME' --build remote"
echo ""
echo "5. Local testing before deployment:"
echo "   func start  # Test locally first"
echo ""

