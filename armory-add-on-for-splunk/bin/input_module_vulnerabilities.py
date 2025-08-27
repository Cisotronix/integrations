import boto3
import gzip
import io
import json
import urllib.parse
import botocore.exceptions
from botocore.config import Config


def validate_input(helper, definition):
    pass  # No input validation needed


def collect_events(helper, ew):
    try:
        aws_key = helper.get_arg('account_key')
        aws_secret = helper.get_arg('secret_key')
        aws_region = helper.get_arg('aws_region')
        sqs_queue_name = helper.get_arg('sqs_queue_name')
    except Exception as e:
        helper.log_error(f"Missing or invalid input parameters: {e}")
        return

    # Get proxy configuration
    proxies = None
    try:
        proxy = helper.get_proxy()
        if proxy:
            proxy_url = proxy.get("proxy_url")
            proxy_port = proxy.get("proxy_port")
            proxy_username = proxy.get("proxy_username")
            proxy_password = proxy.get("proxy_password")
            if proxy_url and proxy_port:
                if proxy_username and proxy_password:
                    auth = f"{proxy_username}:{proxy_password}@"
                else:
                    auth = ""
                full_proxy_url = f"http://{auth}{proxy_url}:{proxy_port}"
                proxies = {
                    'http': full_proxy_url,
                    'https': full_proxy_url
                }
    except Exception as e:
        helper.log_error(f"Invalid proxy configuration: {e}")
        return

    boto_config = Config(proxies=proxies)

    # AWS Clients
    try:
        session = boto3.Session(
            aws_access_key_id=aws_key,
            aws_secret_access_key=aws_secret,
            region_name=aws_region
        )
        sqs = session.client('sqs', config=boto_config)
        s3 = session.client('s3', config=boto_config)
    except botocore.exceptions.NoCredentialsError:
        helper.log_error("AWS credentials not provided or invalid.")
        return
    except botocore.exceptions.PartialCredentialsError:
        helper.log_error("Incomplete AWS credentials provided.")
        return
    except Exception as e:
        helper.log_error(f"Failed to initialize AWS clients: {e}")
        return

    # --- Enhanced SQS Queue Check (Approach 2) ---
    try:
        queue_url = sqs.get_queue_url(QueueName=sqs_queue_name)['QueueUrl']
    except botocore.exceptions.ClientError as e:
        code = e.response['Error'].get('Code', 'Unknown')

        if code == "AWS.SimpleQueueService.NonExistentQueue":
            try:
                queues = sqs.list_queues().get("QueueUrls", [])
                found = any(sqs_queue_name in q for q in queues)
                if found:
                    helper.log_error(f"Access denied for SQS queue '{sqs_queue_name}'.")
                else:
                    helper.log_error(f"SQS queue '{sqs_queue_name}' does not exist.")
            except botocore.exceptions.ClientError as le:
                if le.response['Error'].get('Code') == "AccessDenied":
                    helper.log_error(f"Access denied while checking existence of queue '{sqs_queue_name}'.")
                else:
                    helper.log_error(f"Unexpected error while verifying queue existence: {le}")
            return

        elif code in ("InvalidClientTokenId", "SignatureDoesNotMatch"):
            helper.log_error("Invalid AWS credentials.")
            return
        elif code == "AccessDenied":
            helper.log_error(f"Access denied for SQS queue '{sqs_queue_name}'.")
            return
        else:
            helper.log_error(f"Failed to get SQS queue URL: {e}")
            return
    except Exception as e:
        helper.log_error(f"Unexpected error while fetching SQS queue: {e}")
        return

    # --- Main loop for processing messages ---
    while True:
        try:
            resp = sqs.receive_message(
                QueueUrl=queue_url,
                MaxNumberOfMessages=10,
                WaitTimeSeconds=10
            )
        except botocore.exceptions.EndpointConnectionError as e:
            helper.log_error(f"Network error while connecting to SQS: {e}")
            return
        except Exception as e:
            helper.log_error(f"Failed to receive messages from SQS: {e}")
            return

        messages = resp.get("Messages", [])
        if not messages:
            helper.log_info("No SQS messages received.")
            return

        for msg in messages:
            try:
                body = json.loads(msg["Body"])
                if "Message" in body:
                    body = json.loads(body["Message"])
                records = body.get("Records", [])

                for record in records:
                    bucket = record["s3"]["bucket"]["name"]
                    key = urllib.parse.unquote_plus(record["s3"]["object"]["key"])
                    helper.log_info(f"Processing S3 object: s3://{bucket}/{key}")

                    try:
                        obj = s3.get_object(Bucket=bucket, Key=key)
                        raw_data = obj["Body"].read()
                    except botocore.exceptions.ClientError as e:
                        code = e.response['Error'].get('Code', 'Unknown')
                        if code == "NoSuchBucket":
                            helper.log_error(f"S3 bucket '{bucket}' not found.")
                        elif code == "NoSuchKey":
                            helper.log_error(f"S3 object '{key}' not found in bucket '{bucket}'.")
                        elif code == "AccessDenied":
                            helper.log_error(f"Access denied to s3://{bucket}/{key}.")
                        else:
                            helper.log_error(f"Error fetching S3 object: {e}")
                        continue

                    # Decompress if needed
                    try:
                        if key.endswith('.gz'):
                            with gzip.GzipFile(fileobj=io.BytesIO(raw_data)) as gz:
                                file_content = gz.read().decode('utf-8')
                        else:
                            file_content = raw_data.decode('utf-8')
                    except Exception as e:
                        helper.log_error(f"Failed to read/decompress S3 object {key}: {e}")
                        continue

                    # Try to parse as JSON
                    try:
                        json_data = json.loads(file_content)

                        if isinstance(json_data, dict) and "Records" in json_data:
                            for rec in json_data["Records"]:
                                event = helper.new_event(
                                    data=json.dumps(rec),
                                    source=key,
                                    sourcetype=helper.get_sourcetype(),
                                    index=helper.get_output_index()
                                )
                                ew.write_event(event)

                        elif isinstance(json_data, list):
                            for rec in json_data:
                                event = helper.new_event(
                                    data=json.dumps(rec),
                                    source=key,
                                    sourcetype=helper.get_sourcetype(),
                                    index=helper.get_output_index()
                                )
                                ew.write_event(event)

                        else:
                            event = helper.new_event(
                                data=json.dumps(json_data),
                                source=key,
                                sourcetype=helper.get_sourcetype(),
                                index=helper.get_output_index()
                            )
                            ew.write_event(event)

                    except json.JSONDecodeError:
                        for line in file_content.splitlines():
                            if line.strip():
                                event = helper.new_event(
                                    data=line,
                                    source=key,
                                    sourcetype=helper.get_sourcetype(),
                                    index=helper.get_output_index()
                                )
                                ew.write_event(event)
                    except Exception as e:
                        helper.log_error(f"Unexpected error parsing JSON from {key}: {e}")
                        continue

                    # Delete the processed message from SQS
                    try:
                        sqs.delete_message(
                            QueueUrl=queue_url,
                            ReceiptHandle=msg["ReceiptHandle"]
                        )
                    except Exception as e:
                        helper.log_error(f"Failed to delete SQS message: {e}")

            except json.JSONDecodeError as e:
                helper.log_error(f"Malformed SQS message: {e}")
            except Exception as e:
                helper.log_error(f"Failed to process SQS message: {e}")
