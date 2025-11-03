import boto3
import gzip
import io
import json
import urllib.parse
import botocore.exceptions
from botocore.config import Config
import logging
import os
import yaml
import hashlib
import hmac
import base64
import requests
from pathlib import Path
from datetime import datetime, timezone
from helper import get_logger, build_config_from_keyvault, validate_config

# Get the logger from helper.py
logger = get_logger()

CONFIG_PATH = Path(__file__).parent / "config.yaml"


def load_config():
    """
    Load configuration from either Key Vault (production) or local YAML file (development).
    """
    try:
        # Check if running in Azure Function (production mode)
        if os.getenv('AZURE_FUNCTIONS_ENVIRONMENT') or os.getenv('WEBSITE_SITE_NAME'):
            logger.info("Running in Azure environment, loading config from Key Vault...")
            config = build_config_from_keyvault()
        else:
            # Local development mode
            logger.info("Running in local environment, loading config from YAML file...")
            with open(CONFIG_PATH, 'r') as f:
                config = yaml.safe_load(f)
        
        # Validate configuration
        validate_config(config)
        logger.info("Configuration loaded and validated successfully.")
        return config
        
    except Exception as e:
        logger.exception(f"Failed to load config: {e}")
        raise


def initialize_aws_clients(config):
    """
    Initialize AWS SQS and S3 clients with credentials from config.
    
    Args:
        config (dict): Configuration dictionary
    
    Returns:
        tuple: (sqs_client, s3_client, queue_url)
    """
    aws_key = config['aws']['access_key']
    aws_secret = config['aws']['secret_key']
    aws_region = config['aws']['region']
    sqs_queue_name = config['aws']['sqs_queue_name']
    
    # Configure boto with no proxy (can be extended if needed)
    boto_config = Config()
    
    # AWS Clients
    try:
        session = boto3.Session(
            aws_access_key_id=aws_key,
            aws_secret_access_key=aws_secret,
            region_name=aws_region
        )
        sqs = session.client('sqs', config=boto_config)
        s3 = session.client('s3', config=boto_config)
        
        logger.info(f"AWS clients initialized for region: {aws_region}")
        
    except botocore.exceptions.NoCredentialsError:
        logger.error("AWS credentials not provided or invalid.")
        raise
    except botocore.exceptions.PartialCredentialsError:
        logger.error("Incomplete AWS credentials provided.")
        raise
    except Exception as e:
        logger.error(f"Failed to initialize AWS clients: {e}")
        raise
    
    # Verify SQS queue exists and get URL
    try:
        queue_url = sqs.get_queue_url(QueueName=sqs_queue_name)['QueueUrl']
        logger.info(f"SQS queue found: {sqs_queue_name}")
        
    except botocore.exceptions.ClientError as e:
        code = e.response['Error'].get('Code', 'Unknown')
        
        if code == "AWS.SimpleQueueService.NonExistentQueue":
            try:
                queues = sqs.list_queues().get("QueueUrls", [])
                found = any(sqs_queue_name in q for q in queues)
                if found:
                    logger.error(f"Access denied for SQS queue '{sqs_queue_name}'.")
                else:
                    logger.error(f"SQS queue '{sqs_queue_name}' does not exist.")
            except botocore.exceptions.ClientError as le:
                if le.response['Error'].get('Code') == "AccessDenied":
                    logger.error(f"Access denied while checking existence of queue '{sqs_queue_name}'.")
                else:
                    logger.error(f"Unexpected error while verifying queue existence: {le}")
            raise
        
        elif code in ("InvalidClientTokenId", "SignatureDoesNotMatch"):
            logger.error("Invalid AWS credentials.")
            raise
        elif code == "AccessDenied":
            logger.error(f"Access denied for SQS queue '{sqs_queue_name}'.")
            raise
        else:
            logger.error(f"Failed to get SQS queue URL: {e}")
            raise
    
    except Exception as e:
        logger.error(f"Unexpected error while fetching SQS queue: {e}")
        raise
    
    return sqs, s3, queue_url


def fetch_and_process_s3_object(s3, bucket, key):
    """
    Fetch an S3 object, decompress if needed, and parse the content.
    
    Args:
        s3: Boto3 S3 client
        bucket (str): S3 bucket name
        key (str): S3 object key
    
    Returns:
        list: List of parsed events/records
    """
    logger.info(f"Processing S3 object: s3://{bucket}/{key}")
    
    try:
        obj = s3.get_object(Bucket=bucket, Key=key)
        raw_data = obj["Body"].read()
    except botocore.exceptions.ClientError as e:
        code = e.response['Error'].get('Code', 'Unknown')
        if code == "NoSuchBucket":
            logger.error(f"S3 bucket '{bucket}' not found.")
        elif code == "NoSuchKey":
            logger.error(f"S3 object '{key}' not found in bucket '{bucket}'.")
        elif code == "AccessDenied":
            logger.error(f"Access denied to s3://{bucket}/{key}.")
        else:
            logger.error(f"Error fetching S3 object: {e}")
        return []
    
    # Decompress if needed
    try:
        if key.endswith('.gz'):
            with gzip.GzipFile(fileobj=io.BytesIO(raw_data)) as gz:
                file_content = gz.read().decode('utf-8')
        else:
            file_content = raw_data.decode('utf-8')
    except Exception as e:
        logger.error(f"Failed to read/decompress S3 object {key}: {e}")
        return []
    
    # Parse the content
    events = []
    
    try:
        json_data = json.loads(file_content)
        
        # Handle different JSON structures
        if isinstance(json_data, dict) and "Records" in json_data:
            # JSON with Records array
            for rec in json_data["Records"]:
                rec['_source'] = key
                rec['_bucket'] = bucket
                events.append(rec)
        
        elif isinstance(json_data, list):
            # JSON array
            for rec in json_data:
                if isinstance(rec, dict):
                    rec['_source'] = key
                    rec['_bucket'] = bucket
                events.append(rec)
        
        else:
            # Single JSON object
            if isinstance(json_data, dict):
                json_data['_source'] = key
                json_data['_bucket'] = bucket
            events.append(json_data)
        
        logger.info(f"Parsed {len(events)} events from JSON file: {key}")
    
    except json.JSONDecodeError:
        # Not JSON, treat as line-delimited text
        for line in file_content.splitlines():
            if line.strip():
                try:
                    # Try to parse each line as JSON
                    line_data = json.loads(line)
                    if isinstance(line_data, dict):
                        line_data['_source'] = key
                        line_data['_bucket'] = bucket
                    events.append(line_data)
                except json.JSONDecodeError:
                    # Plain text line
                    events.append({
                        'raw_data': line,
                        '_source': key,
                        '_bucket': bucket
                    })
        
        logger.info(f"Parsed {len(events)} events from text file: {key}")
    
    except Exception as e:
        logger.error(f"Unexpected error parsing data from {key}: {e}")
        return []
    
    return events


def send_to_azure_sentinel(events, config):
    """
    Send events directly to Azure Log Analytics workspace using the Data Collector API.
    
    Args:
        events (list): List of event dictionaries
        config (dict): Configuration containing workspace credentials and settings
    
    Returns:
        dict: Statistics about sent/failed events
    """
    if not events:
        logger.info("No events to send to Azure Sentinel.")
        return {"sent": 0, "failed": 0}
    
    # Configuration
    workspace_id = config["azure"]["workspace_id"]
    workspace_key = config["azure"]["workspace_key"]
    log_type = config["azure"]["log_type"]
    
    max_retries = config.get("processing", {}).get("max_retries", 3)
    retry_delay = config.get("processing", {}).get("retry_delay", 5)
    timeout = config.get("processing", {}).get("http_timeout", 30)
    
    # Azure Log Analytics Data Collector API endpoint
    uri = f"https://{workspace_id}.ods.opinsights.azure.com/api/logs?api-version=2016-04-01"
    
    logger.info(f"Sending {len(events)} events to Azure Log Analytics workspace: {workspace_id}")
    
    def build_signature(workspace_id, workspace_key, date, content_length, method, content_type, resource):
        """Build the authorization signature for Azure Log Analytics Data Collector API."""
        x_headers = f"x-ms-date:{date}"
        string_to_hash = f"{method}\n{content_length}\n{content_type}\n{x_headers}\n{resource}"
        bytes_to_hash = bytes(string_to_hash, 'UTF-8')
        decoded_key = base64.b64decode(workspace_key)
        encoded_hash = base64.b64encode(hmac.new(decoded_key, bytes_to_hash, digestmod=hashlib.sha256).digest()).decode()
        authorization = f"SharedKey {workspace_id}:{encoded_hash}"
        return authorization
    
    def send_batch_to_workspace(batch, batch_idx):
        """Send a batch of events to Log Analytics workspace."""
        # Prepare the data
        body = json.dumps(batch)
        content_length = len(body)
        
        # Generate RFC 1123 timestamp
        rfc1123date = datetime.now(timezone.utc).strftime('%a, %d %b %Y %H:%M:%S GMT')
        
        # Build authorization signature
        signature = build_signature(
            workspace_id, 
            workspace_key, 
            rfc1123date, 
            content_length, 
            'POST', 
            'application/json', 
            '/api/logs'
        )
        
        # Headers for the request
        headers = {
            'content-type': 'application/json',
            'Authorization': signature,
            'Log-Type': log_type,
            'x-ms-date': rfc1123date,
            'time-generated-field': 'timestamp'
        }
        
        # Send the request with retries
        for attempt in range(max_retries):
            try:
                logger.debug(f"Sending batch {batch_idx} to Log Analytics, attempt {attempt + 1}")
                
                response = requests.post(
                    uri,
                    data=body,
                    headers=headers,
                    timeout=timeout
                )
                
                if response.status_code in (200, 202):
                    logger.info(f"Successfully sent batch {batch_idx} ({len(batch)} events) to workspace")
                    return True
                else:
                    logger.warning(f"Batch {batch_idx} attempt {attempt + 1} failed: HTTP {response.status_code}")
                    logger.debug(f"Response: {response.text}")
                    
            except requests.exceptions.Timeout:
                logger.warning(f"Batch {batch_idx} attempt {attempt + 1} timed out after {timeout}s")
            except requests.exceptions.ConnectionError as e:
                logger.warning(f"Batch {batch_idx} attempt {attempt + 1} connection error: {e}")
            except Exception as e:
                logger.warning(f"Batch {batch_idx} attempt {attempt + 1} unexpected error: {e}")
            
            # Wait before retry (except on last attempt)
            if attempt < max_retries - 1:
                logger.info(f"Retrying batch {batch_idx} in {retry_delay} seconds...")
                import time
                time.sleep(retry_delay)
        
        return False
    
    # Process events in batches (Log Analytics API has size limits)
    batch_size = 100  # Smaller batches for Log Analytics API
    total_sent = 0
    total_failed = 0
    
    for batch_idx, i in enumerate(range(0, len(events), batch_size), 1):
        batch = events[i:i+batch_size]
        
        # Add timestamp and metadata to each event
        processed_batch = []
        for event in batch:
            processed_event = dict(event) if isinstance(event, dict) else {'data': str(event)}
            
            # Ensure timestamp is present for Log Analytics
            if 'timestamp' not in processed_event:
                processed_event['timestamp'] = datetime.now(timezone.utc).isoformat()
            
            # Add processing metadata
            processed_event['_ingestion_metadata'] = {
                'processor_source': 'armory-vulnerability-processor',
                'processor_version': '1.0',
                'batch_id': f"batch_{batch_idx}",
                'ingested_at': datetime.now(timezone.utc).isoformat(),
                'workspace_id': workspace_id
            }
            
            processed_batch.append(processed_event)
        
        logger.info(f"Processing batch {batch_idx}: {len(batch)} events (items {i+1}-{i+len(batch)})")
        
        if send_batch_to_workspace(processed_batch, batch_idx):
            total_sent += len(batch)
        else:
            total_failed += len(batch)
            logger.error(f"Failed to send batch {batch_idx} after {max_retries} attempts")
    
    # Log final summary
    logger.info(f"Event transmission summary: {total_sent} sent, {total_failed} failed")
    
    if total_failed > 0:
        failure_rate = (total_failed / len(events)) * 100
        logger.warning(f"Event transmission failure rate: {failure_rate:.1f}%")
    
    return {"sent": total_sent, "failed": total_failed}


def main():
    """
    Main function to orchestrate vulnerability processing workflow.
    """
    start_time = datetime.now(timezone.utc)
    logger.info("=" * 60)
    logger.info("Starting Armory Vulnerability Processor")
    logger.info(f"Start time: {start_time.isoformat()}")
    logger.info("=" * 60)
    
    try:
        # Load configuration
        logger.info("Loading configuration...")
        config = load_config()
        logger.info("Configuration loaded successfully.")
        
        # Initialize AWS clients
        logger.info("Initializing AWS clients...")
        sqs, s3, queue_url = initialize_aws_clients(config)
        logger.info("AWS clients initialized successfully.")
        
        # Get processing limits from config
        max_messages = config.get('processing', {}).get('max_messages_per_run', 100)
        wait_time = config.get('processing', {}).get('sqs_wait_time_seconds', 10)
        max_messages_per_receive = config.get('processing', {}).get('sqs_max_messages', 10)
        
        # Process messages from SQS
        messages_processed = 0
        s3_objects_processed = 0
        all_events = []
        
        logger.info(f"Starting to poll SQS queue (max {max_messages} messages)...")
        
        while messages_processed < max_messages:
            try:
                resp = sqs.receive_message(
                    QueueUrl=queue_url,
                    MaxNumberOfMessages=min(max_messages_per_receive, max_messages - messages_processed),
                    WaitTimeSeconds=wait_time
                )
            except botocore.exceptions.EndpointConnectionError as e:
                logger.error(f"Network error while connecting to SQS: {e}")
                break
            except Exception as e:
                logger.error(f"Failed to receive messages from SQS: {e}")
                break
            
            messages = resp.get("Messages", [])
            if not messages:
                logger.info("No more SQS messages available.")
                break
            
            logger.info(f"Received {len(messages)} messages from SQS")
            
            for msg in messages:
                try:
                    # Parse SQS message body
                    body = json.loads(msg["Body"])
                    
                    # Handle SNS notification wrapper
                    if "Message" in body:
                        body = json.loads(body["Message"])
                    
                    records = body.get("Records", [])
                    
                    for record in records:
                        bucket = record["s3"]["bucket"]["name"]
                        key = urllib.parse.unquote_plus(record["s3"]["object"]["key"])
                        
                        # Fetch and process S3 object
                        events = fetch_and_process_s3_object(s3, bucket, key)
                        all_events.extend(events)
                        
                        if events:
                            s3_objects_processed += 1
                    
                    # Delete the processed message from SQS
                    try:
                        sqs.delete_message(
                            QueueUrl=queue_url,
                            ReceiptHandle=msg["ReceiptHandle"]
                        )
                        logger.debug(f"Deleted SQS message: {msg.get('MessageId', 'unknown')}")
                    except Exception as e:
                        logger.error(f"Failed to delete SQS message: {e}")
                    
                    messages_processed += 1
                
                except json.JSONDecodeError as e:
                    logger.error(f"Malformed SQS message: {e}")
                except Exception as e:
                    logger.error(f"Failed to process SQS message: {e}")
        
        logger.info(f"SQS processing complete: {messages_processed} messages, {s3_objects_processed} S3 objects")
        
        if not all_events:
            logger.info("No events to send. Processing complete.")
            return {
                "status": "success",
                "message": "No events to process",
                "messages_processed": messages_processed,
                "s3_objects_processed": s3_objects_processed,
                "events_sent": 0,
                "failed": 0
            }
        
        # Send events to Azure Sentinel
        logger.info(f"Sending {len(all_events)} events to Azure Sentinel...")
        transmission_result = send_to_azure_sentinel(all_events, config)
        
        # Calculate processing summary
        end_time = datetime.now(timezone.utc)
        duration = (end_time - start_time).total_seconds()
        
        logger.info("=" * 60)
        logger.info("Vulnerability processing completed successfully")
        logger.info(f"Duration: {duration:.2f} seconds")
        logger.info(f"SQS messages processed: {messages_processed}")
        logger.info(f"S3 objects processed: {s3_objects_processed}")
        logger.info(f"Events sent: {transmission_result['sent']}")
        logger.info(f"Events failed: {transmission_result['failed']}")
        logger.info("=" * 60)
        
        return {
            "status": "success",
            "messages_processed": messages_processed,
            "s3_objects_processed": s3_objects_processed,
            "events_sent": transmission_result["sent"],
            "failed": transmission_result["failed"],
            "duration_seconds": duration
        }
    
    except Exception as e:
        end_time = datetime.now(timezone.utc)
        duration = (end_time - start_time).total_seconds()
        
        logger.exception("Unhandled error during vulnerability processing")
        logger.error(f"Processing failed after {duration:.2f} seconds")
        
        return {
            "status": "error",
            "error": str(e),
            "duration_seconds": duration
        }


if __name__ == "__main__":
    print("Starting vulnerability processor...")
    main()

