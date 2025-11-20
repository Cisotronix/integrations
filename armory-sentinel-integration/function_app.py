"""
Azure Function entry point for Armory Vulnerability Processor.

This function is triggered by a timer to periodically fetch vulnerabilities from AWS S3
(via SQS notifications) and send them to Azure Sentinel via Azure Log Analytics API.

Uses Azure Functions Python v2 programming model with decorators.
"""

import datetime
import logging
import os
import azure.functions as func
from vulnerabilities import main as process_vulnerabilities

# Create the function app instance
app = func.FunctionApp()

@app.function_name(name="ArmoryVulnerabilityProcessor")
@app.timer_trigger(
    schedule=os.getenv("TIMER_SCHEDULE", "0 */15 * * * *"),  # Default: every 15 minutes
    arg_name="mytimer",
    run_on_startup=False,
    use_monitor=True
)
def armory_vulnerability_processor(mytimer: func.TimerRequest) -> None:
    """
    Azure Function timer trigger for processing Armory vulnerabilities.
    
    This function:
    1. Checks AWS SQS queue for S3 event notifications
    2. Fetches vulnerability data from S3 objects
    3. Processes and transforms the data
    4. Sends data directly to Azure Log Analytics workspace
    5. Deletes processed SQS messages
    
    Args:
        mytimer: Timer trigger request object with schedule information
    """
    utc_timestamp = datetime.datetime.now(datetime.timezone.utc).isoformat()
    
    # Configure logging for this function
    function_logger = logging.getLogger("ArmoryVulnerabilityProcessor")
    function_logger.setLevel(logging.INFO)
    
    # Check if timer is past due (running late)
    if mytimer.past_due:
        function_logger.warning('Timer trigger is past due - function is running late!')
    
    # Log timer information for debugging
    function_logger.info(f"Armory Vulnerability Processor started at {utc_timestamp}")
    function_logger.info(f"Timer schedule: {os.getenv('TIMER_SCHEDULE', '0 */15 * * * *')}")
    function_logger.info(f"Timer past due: {mytimer.past_due}")
    function_logger.info(f"Timer schedule status: {mytimer.schedule_status if hasattr(mytimer, 'schedule_status') else 'N/A'}")
    
    # Log environment variables for debugging
    function_logger.info(f"Azure Functions Environment: {os.getenv('AZURE_FUNCTIONS_ENVIRONMENT', 'Not Set')}")
    function_logger.info(f"Website Site Name: {os.getenv('WEBSITE_SITE_NAME', 'Not Set')}")
    
    try:
        # Execute the main vulnerability processing logic
        result = process_vulnerabilities()
        
        # Log the processing results
        if result.get("status") == "success":
            function_logger.info(
                f"Vulnerability processing completed successfully! "
                f"Stats: Messages={result.get('messages_processed', 0)}, "
                f"S3Objects={result.get('s3_objects_processed', 0)}, "
                f"Events={result.get('events_sent', 0)}, "
                f"Failed={result.get('failed', 0)}, "
                f"Duration={result.get('duration_seconds', 0):.2f}s"
            )
        elif result.get("status") == "warning":
            function_logger.warning(
                f"Vulnerability processing completed with warnings: {result.get('message', 'Unknown warning')}"
            )
        else:
            function_logger.error(
                f"Vulnerability processing failed: {result.get('error', 'Unknown error')}"
            )
            # Don't raise exception for processing failures - let timer continue running
            
    except Exception as e:
        function_logger.exception(f"Unhandled exception in Azure Function: {e}")
        # Re-raise to mark the function execution as failed
        raise
    
    function_logger.info(f"Armory Vulnerability Processor completed at {utc_timestamp}")

