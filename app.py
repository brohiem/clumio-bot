from flask import Flask, request, jsonify
import os
import json
from clumio_client import ClumioClient
from slack_bolt import App as SlackApp
from slack_bolt.adapter.flask import SlackRequestHandler

app = Flask(__name__)

# Configure Flask to handle different content types from Slack
# Slack sends application/x-www-form-urlencoded for slash commands
# and application/json for Events API
app.config['JSON_AS_ASCII'] = False

# Initialize Clumio client
clumio_client = ClumioClient(
    api_token=os.getenv('CLUMIO_API_TOKEN', ''),
    api_base_url=os.getenv('CLUMIO_API_BASE_URL', 'https://api.clumio.com')
)

# Initialize Slack app (only if tokens are provided)
slack_bot_token = os.getenv('SLACK_BOT_TOKEN')
slack_app = None
slack_handler = None

if slack_bot_token:
    # Configure for serverless/FaaS environment
    # process_before_response=False (default) ensures ack() response is sent immediately
    # This is critical for meeting Slack's 3-second acknowledgment requirement
    slack_app = SlackApp(
        token=slack_bot_token,
        process_before_response=False  # Send ack() response immediately, don't wait for handler
    )
    slack_handler = SlackRequestHandler(slack_app)


# Shared helper functions for inventory and restore
def get_inventory_data(inventory_type, account_native_id=None):
    """Shared function to get inventory data
    
    Args:
        inventory_type: Type of inventory ('s3' or 'ec2')
        account_native_id: Optional AWS account ID
    """
    result = clumio_client.get_inventory(inventory_type, account_native_id=account_native_id)
    
    if inventory_type == 's3':
        parsed_result = []
        items = result.get('_embedded', {}).get('items', [])
        
        for item in items:
            parsed_item = {
                'bucket-id': item.get('bucket_id', ''),
                'bucket-name': item.get('bucket_name', '')
            }
            parsed_result.append(parsed_item)
        
        return parsed_result
    else:
        return result


def format_slack_inventory_response(parsed_result):
    """Format inventory data for Slack as a formatted list"""
    blocks = [
        {
            "type": "header",
            "text": {
                "type": "plain_text",
                "text": "Clumio S3 Inventory",
                "emoji": True
            }
        },
        {
            "type": "divider"
        }
    ]
    
    # Format each item as a list entry
    if isinstance(parsed_result, list) and len(parsed_result) > 0:
        for idx, item in enumerate(parsed_result, 1):
            # Build the text for each item
            item_text = f"*{idx}. "
            if isinstance(item, dict):
                # Format dictionary items
                parts = []
                for key, value in item.items():
                    # Convert kebab-case to readable format
                    readable_key = key.replace('-', ' ').replace('_', ' ').title()
                    parts.append(f"*{readable_key}:* {value}")
                item_text += " | ".join(parts)
            else:
                item_text += str(item)
            item_text += "*"
            
            blocks.append({
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": item_text
                }
            })
        
        # Add summary
        blocks.append({
            "type": "divider"
        })
        blocks.append({
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f"*Total items:* {len(parsed_result)}"
            }
        })
    else:
        # If no items or unexpected format, show as JSON
        json_payload = json.dumps(parsed_result, indent=2)
        blocks.append({
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f"```{json_payload}```"
            }
        })
    
    return {
        "response_type": "ephemeral",
        "blocks": blocks
    }


@app.route('/inventory', methods=['GET', 'POST'])
def inventory():
    """
    Endpoint to retrieve inventory data from Clumio API
    
    Required input:
    - type: 's3' or 'ec2'
    
    Optional input:
    - account: AWS account ID (defaults to '761018876565' for backward compatibility)
    """
    # Get type and account parameters from query string (GET) or JSON/form body (POST)
    slack_form_data = None
    account_native_id = None
    
    if request.method == 'GET':
        inventory_type = request.args.get('type')
        account_native_id = request.args.get('account')
    else:
        # Handle both JSON and form-encoded POST requests (e.g., from Slack)
        # Slack sends application/x-www-form-urlencoded with properties like:
        # token, team_id, channel_id, user_id, command, text (e.g., "type=s3 region=us-west-2")
        inventory_type = None
        
        # 1. Try query string first (works for all POST requests)
        inventory_type = request.args.get('type')
        if not account_native_id:
            account_native_id = request.args.get('account')
        
        # 2. Try JSON body
        if not inventory_type or not account_native_id:
            try:
                json_data = request.get_json(silent=True, force=True)
                if json_data and isinstance(json_data, dict):
                    if not inventory_type:
                        inventory_type = json_data.get('type')
                    if not account_native_id:
                        account_native_id = json_data.get('account')
            except Exception as e:
                print(f"Error parsing JSON: {e}")
        
        # 3. Try form data (Slack sends form-encoded data)
        if request.form:
            # Extract type and account from form data if present
            if not inventory_type:
                inventory_type = request.form.get('type')
            if not account_native_id:
                account_native_id = request.form.get('account')
            
            # Parse the 'text' field from Slack (e.g., "type=s3 account=1234567890")
            slack_text = request.form.get('text', '')
            if slack_text:
                slack_text = slack_text.strip()
                
                # Parse text like "type=s3 account=1234567890" or just "s3"
                parts = slack_text.split()
                for part in parts:
                    if '=' in part:
                        key, value = part.split('=', 1)
                        key = key.strip()
                        value = value.strip()
                        if key == 'type' and not inventory_type:
                            inventory_type = value
                        elif key == 'account' and not account_native_id:
                            account_native_id = value
                
                # If no type found and text is just "s3" or "ec2", use it directly
                if not inventory_type and slack_text in ['s3', 'ec2']:
                    inventory_type = slack_text
        
        # 4. Try values (for form data that might not be in request.form)
        if not inventory_type or not account_native_id:
            if request.values:
                if not inventory_type:
                    inventory_type = request.values.get('type')
                if not account_native_id:
                    account_native_id = request.values.get('account')
                
                # Also try parsing text from values
                if not inventory_type or not account_native_id:
                    values_text = request.values.get('text', '')
                    if values_text:
                        values_text = values_text.strip()
                        if values_text in ['s3', 'ec2'] and not inventory_type:
                            inventory_type = values_text
                        else:
                            # Try parsing key=value format
                            parts = values_text.split()
                            for part in parts:
                                if '=' in part:
                                    key, value = part.split('=', 1)
                                    key = key.strip()
                                    value = value.strip()
                                    if key == 'type' and not inventory_type:
                                        inventory_type = value
                                    elif key == 'account' and not account_native_id:
                                        account_native_id = value
    
    # Validate required parameter - default to 's3' if not provided (matches Slack command behavior)
    if not inventory_type:
        # Default to s3 if no type specified (for backward compatibility)
        inventory_type = 's3'
        print(f"Inventory endpoint: No type provided, defaulting to s3")
    
    # Validate type value
    if inventory_type not in ['s3', 'ec2']:
        return jsonify({
            'error': f'Invalid type value: {inventory_type}. Accepted values: s3, ec2'
        }), 400
    
    try:
        # Get inventory data using shared function
        if inventory_type == 's3':
            parsed_result = get_inventory_data(inventory_type, account_native_id=account_native_id)
        else:
            # For EC2 or other types, return the raw response
            result = clumio_client.get_inventory(inventory_type, account_native_id=account_native_id)
            parsed_result = result
        
        # Check if this is a Slack request (has Slack form data like token, team_id, etc.)
        is_slack_request = request.method == 'POST' and (
            request.form.get('token') or 
            request.form.get('team_id') or 
            request.form.get('command')
        )
        
        # If it's a Slack request, format the response for Slack
        if is_slack_request:
            slack_response = format_slack_inventory_response(parsed_result)
            return jsonify(slack_response), 200
        else:
            # Return simple JSON array for REST API calls
            return jsonify(parsed_result), 200
            
    except Exception as e:
        import traceback
        error_trace = traceback.format_exc()
        print(f"Inventory error: {str(e)}")
        print(error_trace)
        return jsonify({
            'error': f'Failed to retrieve inventory: {str(e)}',
            'type': type(e).__name__,
            'inventory_type_requested': inventory_type
        }), 500


@app.route('/restore', methods=['GET', 'POST'])
def restore():
    """
    Endpoint to restore data from Clumio API
    
    Required input:
    - type: 's3' or 'ec2'
    
    Optional inputs:
    - bucket-name: string value
    - bucket-id: numeric value (string format)
    """
    # Get parameters from query string (GET) or JSON/form body (POST)
    if request.method == 'GET':
        restore_type = request.args.get('type')
        bucket_name = request.args.get('bucket-name')
        bucket_id = request.args.get('bucket-id')
    else:
        # Handle both JSON and form-encoded POST requests (e.g., from Slack)
        # Try multiple methods to extract parameters
        restore_type = request.args.get('type')
        bucket_name = request.args.get('bucket-name')
        bucket_id = request.args.get('bucket-id')
        
        # Try JSON body
        if not restore_type:
            try:
                data = request.get_json(silent=True, force=True)
                if data and isinstance(data, dict):
                    restore_type = restore_type or data.get('type')
                    bucket_name = bucket_name or data.get('bucket-name')
                    bucket_id = bucket_id or data.get('bucket-id')
            except:
                pass
        
        # Try form data
        if request.form:
            restore_type = restore_type or request.form.get('type')
            bucket_name = bucket_name or request.form.get('bucket-name')
            bucket_id = bucket_id or request.form.get('bucket-id')
        
        # Try values (for form data that might not be in request.form)
        if request.values:
            restore_type = restore_type or request.values.get('type')
            bucket_name = bucket_name or request.values.get('bucket-name')
            bucket_id = bucket_id or request.values.get('bucket-id')
    
    # Validate required parameter
    if not restore_type:
        return jsonify({
            'error': 'Missing required parameter: type'
        }), 400
    
    # Validate type value
    if restore_type not in ['s3', 'ec2']:
        return jsonify({
            'error': f'Invalid type value: {restore_type}. Accepted values: s3, ec2'
        }), 400
    
    # Validate bucket-id is numeric if provided
    if bucket_id is not None:
        try:
            int(bucket_id)
        except (ValueError, TypeError):
            return jsonify({
                'error': 'bucket-id must be a numeric value (string format)'
            }), 400
    
    try:
        # Call Clumio API
        result = clumio_client.restore(
            restore_type,
            bucket_name=bucket_name,
            bucket_id=bucket_id
        )
        return jsonify(result), 200
    except Exception as e:
        return jsonify({
            'error': f'Failed to restore: {str(e)}'
        }), 500


@app.route('/health', methods=['GET', 'POST'])
def health():
    """Health check endpoint"""
    return jsonify({'status': 'ok'}), 200


# Slack command handlers
if slack_app:
    @slack_app.command("/inventory")
    def handle_inventory_command(ack, respond, command):
        """Handle /inventory Slack command"""
        # Acknowledge immediately to satisfy Slack's 3-second requirement
        # This must be the first thing we do
        ack()
        
        text = command.get("text", "").strip()
        
        # Parse parameters from text (e.g., "type=s3 account=1234567890" or just "s3")
        inventory_type = None
        account_native_id = None
        
        if text:
            # Parse key=value pairs
            parts = text.split()
            for part in parts:
                if "=" in part:
                    key, value = part.split("=", 1)
                    key = key.strip()
                    value = value.strip()
                    if key == "type":
                        inventory_type = value
                    elif key == "account":
                        account_native_id = value
                else:
                    # If no =, treat as type value
                    if not inventory_type:
                        inventory_type = part.strip()
        
        # Default to s3 if no type specified
        if not inventory_type:
            inventory_type = "s3"
        
        if inventory_type not in ["s3", "ec2"]:
            respond(
                text=f"Invalid type: {inventory_type}. Accepted values: s3, ec2",
                response_type="ephemeral"
            )
            return
        
        try:
            parsed_result = get_inventory_data(inventory_type, account_native_id=account_native_id)
            slack_response = format_slack_inventory_response(parsed_result)
            respond(**slack_response)
        except Exception as e:
            respond(
                text=f"Error retrieving inventory: {str(e)}",
                response_type="ephemeral"
            )
    
    @slack_app.command("/restore")
    def handle_restore_command(ack, respond, command):
        """Handle /restore Slack command"""
        # Acknowledge immediately to satisfy Slack's 3-second requirement
        # This must be the first thing we do
        ack()
        
        text = command.get("text", "").strip()
        
        if not text:
            respond(
                text="Usage: /restore type=s3 [bucket-name=name] [bucket-id=id]",
                response_type="ephemeral"
            )
            return
        
        # Parse parameters
        params = {}
        parts = text.split()
        
        for part in parts:
            if "=" in part:
                key, value = part.split("=", 1)
                key = key.strip()
                value = value.strip()
                
                if key == "type":
                    params["type"] = value
                elif key == "bucket-name":
                    params["bucket-name"] = value
                elif key == "bucket-id":
                    params["bucket-id"] = value
        
        if "type" not in params:
            respond(
                text="Missing required parameter: type",
                response_type="ephemeral"
            )
            return
        
        if params["type"] not in ["s3", "ec2"]:
            respond(
                text=f"Invalid type: {params['type']}. Accepted values: s3, ec2",
                response_type="ephemeral"
            )
            return
        
        try:
            result = clumio_client.restore(
                params["type"],
                bucket_name=params.get("bucket-name"),
                bucket_id=params.get("bucket-id")
            )
            
            result_json = json.dumps(result, indent=2)
            slack_response = {
                "response_type": "ephemeral",
                "blocks": [
                    {
                        "type": "header",
                        "text": {
                            "type": "plain_text",
                            "text": "Clumio Restore",
                            "emoji": True
                        }
                    },
                    {
                        "type": "section",
                        "text": {
                            "type": "mrkdwn",
                            "text": f"```{result_json}```"
                        }
                    }
                ]
            }
            
            respond(**slack_response)
        except Exception as e:
            respond(
                text=f"Error calling restore: {str(e)}",
                response_type="ephemeral"
            )
    
    # Slack Events API endpoint
    @app.route("/slack/events", methods=["GET", "POST"])
    def slack_events():
        """Handle Slack events and commands"""
        # Handle GET requests (e.g., for health checks or URL verification)
        if request.method == 'GET':
            return jsonify({'status': 'ok'}), 200
        
        if not slack_handler:
            return jsonify({"error": "Slack not configured"}), 500
        
        try:
            # Handle URL verification challenge from Slack (Events API)
            # This happens when Slack first verifies your endpoint URL
            content_type = request.content_type or ''
            
            # Handle JSON content type (Events API) - check for URL verification
            if 'application/json' in content_type:
                # Use silent=True and force=True to avoid 415 errors
                data = request.get_json(silent=True, force=True)
                if data and isinstance(data, dict) and data.get('type') == 'url_verification':
                    return jsonify({'challenge': data.get('challenge')}), 200
            
            # For form-urlencoded (slash commands) or other content types,
            # let SlackRequestHandler handle it - it knows how to parse both
            # We don't try to parse the request ourselves to avoid Content-Type issues
            
            # The SlackRequestHandler.handle() method processes the request
            # and handles both application/x-www-form-urlencoded and application/json
            return slack_handler.handle(request)
            
        except Exception as e:
            # Log detailed error information for debugging
            import traceback
            error_msg = str(e)
            error_details = {
                "error": error_msg,
                "content_type": request.content_type,
                "method": request.method
            }
            print(f"Slack event error: {error_msg}")
            print(f"Content-Type: {request.content_type}")
            print(f"Request data available: {hasattr(request, 'get_data')}")
            print(traceback.format_exc())
            return jsonify(error_details), 500


# Vercel serverless function handler
# This allows Vercel to serve the Flask app
if __name__ == '__main__':
    app.run(debug=True)

