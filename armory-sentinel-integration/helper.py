import logging
import uuid
import os
import json
from datetime import datetime
from azure.keyvault.secrets import SecretClient
from azure.identity import DefaultAzureCredential


def get_logger(name="vulnerability-processor", log_level=logging.INFO):
    """
    Create a logger with consistent formatting and UUID for tracking.
    
    Args:
        name (str): Logger name
        log_level: Logging level (default: INFO)
    
    Returns:
        logging.Logger: Configured logger instance
    """
    logger = logging.getLogger(name)
    
    # Only configure if not already configured
    if not logger.handlers:
        logger.setLevel(log_level)
        
        # Generate unique UUID for this session
        session_uuid = str(uuid.uuid4())[:8]
        
        # Create formatter with UUID
        formatter = logging.Formatter(
            f'%(asctime)s [%(levelname)s] [{session_uuid}] %(message)s',
            datefmt='%Y-%m-%d %H:%M:%S'
        )
        
        # File handler for persistent logging
        try:
            log_dir = "/tmp/logs" if os.path.exists("/tmp") else "logs"
            os.makedirs(log_dir, exist_ok=True)
            
            file_handler = logging.FileHandler(
                f"{log_dir}/vulnerability-processor.log", 
                mode='a', 
                encoding='utf-8'
            )
            file_handler.setLevel(log_level)
            file_handler.setFormatter(formatter)
            logger.addHandler(file_handler)
        except Exception as e:
            print(f"Warning: Could not create file handler: {e}")
        
        # Console handler for immediate feedback
        console_handler = logging.StreamHandler()
        console_handler.setLevel(log_level)
        console_handler.setFormatter(formatter)
        logger.addHandler(console_handler)
        
        # Store UUID as logger attribute for access in other modules
        logger.uuid = session_uuid
    
    return logger


def get_keyvault_client(vault_url=None):
    """
    Create Azure Key Vault client using managed identity or service principal.
    
    Args:
        vault_url (str): Key Vault URL (if None, gets from environment)
    
    Returns:
        SecretClient: Azure Key Vault secret client
    """
    if not vault_url:
        vault_url = os.getenv('AZURE_KEYVAULT_URL')
        if not vault_url:
            raise ValueError("AZURE_KEYVAULT_URL environment variable is required")
    
    # Use DefaultAzureCredential which handles:
    # - Managed Identity (when running in Azure)
    # - Service Principal (when AZURE_CLIENT_ID, AZURE_CLIENT_SECRET, AZURE_TENANT_ID are set)
    # - Azure CLI (when running locally)
    credential = DefaultAzureCredential()
    
    return SecretClient(vault_url=vault_url, credential=credential)


def get_secret(secret_name):
    """
    Retrieve a secret from Azure Key Vault.
    
    Args:
        secret_name (str): Name of the secret to retrieve
    
    Returns:
        str: Secret value
    
    Raises:
        Exception: If secret cannot be retrieved
    """
    try:
        vault_client = get_keyvault_client()
        secret = vault_client.get_secret(secret_name)
        return secret.value
    except Exception as e:
        logger = get_logger()
        logger.error(f"Failed to retrieve secret '{secret_name}' from Key Vault: {e}")
        raise


def build_config_from_keyvault():
    """
    Build configuration dictionary by retrieving secrets from Key Vault.
    This replaces the need for a config.yaml file when running in Azure.
    
    Returns:
        dict: Configuration dictionary
    """
    logger = get_logger()
    logger.info("Building configuration from Azure Key Vault...")
    
    try:
        config = {
            'aws': {
                'access_key': get_secret('aws-access-key'),
                'secret_key': get_secret('aws-secret-key'),
                'region': get_secret('aws-region'),
                'sqs_queue_name': get_secret('aws-sqs-queue-name')
            },
            'azure': {
                'workspace_id': get_secret('azure-workspace-id'),
                'workspace_key': get_secret('azure-workspace-key'),
                'log_type': get_secret('azure-log-type'),
                'keyvault_url': os.getenv('AZURE_KEYVAULT_URL'),
                'function_name': os.getenv('AZURE_FUNCTIONS_ENVIRONMENT', 'local')
            },
            'processing': {
                'max_messages_per_run': int(get_secret('max-messages-per-run')),
                'sqs_wait_time_seconds': int(get_secret('sqs-wait-time-seconds')),
                'sqs_max_messages': int(get_secret('sqs-max-messages')),
                'http_timeout': 30,
                'max_retries': 3,
                'retry_delay': 5
            }
        }
        
        logger.info("Configuration successfully built from Key Vault")
        return config
        
    except Exception as e:
        logger.error(f"Failed to build configuration from Key Vault: {e}")
        raise


def validate_config(config):
    """
    Validate configuration dictionary to ensure all required fields are present.
    
    Args:
        config (dict): Configuration dictionary
    
    Returns:
        bool: True if valid, raises exception if invalid
    """
    required_fields = {
        'aws.access_key': ['aws', 'access_key'],
        'aws.secret_key': ['aws', 'secret_key'],
        'aws.region': ['aws', 'region'],
        'aws.sqs_queue_name': ['aws', 'sqs_queue_name'],
        'azure.workspace_id': ['azure', 'workspace_id'],
        'azure.workspace_key': ['azure', 'workspace_key'],
        'azure.log_type': ['azure', 'log_type']
    }
    
    missing_fields = []
    
    for field_name, field_path in required_fields.items():
        try:
            current = config
            for key in field_path:
                current = current[key]
            
            if not current:
                missing_fields.append(field_name)
                
        except (KeyError, TypeError):
            missing_fields.append(field_name)
    
    if missing_fields:
        raise ValueError(f"Missing required configuration fields: {', '.join(missing_fields)}")
    
    return True

