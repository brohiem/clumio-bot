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
            # Capture only the required fields: id, bucket_id, bucket_name
            parsed_item = {
                'id': item.get('id', ''),
                'bucket-id': item.get('bucket_id', ''),
                'bucket-name': item.get('bucket_name', '')
            }
            parsed_result.append(parsed_item)
        
        return parsed_result
    else:
        return result


def format_slack_inventory_response(parsed_result, account_native_id=None):
    """Format inventory data for Slack as a formatted list
    
    Args:
        parsed_result: The inventory data to format
        account_native_id: Optional AWS account ID to display in header (masked for security)
    """
    # Build header text with masked account number if provided
    header_text = "Clumio S3 Inventory"
    if account_native_id:
        # Mask all but last 4 digits for security
        account_str = str(account_native_id)
        if len(account_str) > 4:
            masked_account = '*' * (len(account_str) - 4) + account_str[-4:]
        else:
            masked_account = '*' * len(account_str)
        header_text = f"Clumio S3 Inventory for Account: {masked_account}"
    
    blocks = [
        {
            "type": "header",
            "text": {
                "type": "plain_text",
                "text": header_text,
                "emoji": True
            }
        },
        {
            "type": "divider"
        }
    ]
    
    # Format each item with action buttons
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
            
            # Create value string with item data (JSON encoded for button value)
            item_value = json.dumps(item) if isinstance(item, dict) else str(item)
            
            # Extract bucket name for button label
            bucket_name = item.get('bucket-name', 'Unknown Bucket') if isinstance(item, dict) else 'Unknown'
            
            # Add section with item info
            blocks.append({
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": item_text
                }
            })
            
            # Add action buttons for this item - use bucket name as label
            blocks.append({
                "type": "actions",
                "elements": [
                    {
                        "type": "button",
                        "text": {"type": "plain_text", "text": "View " + bucket_name},
                        "action_id": "view_bucket",
                        "value": item_value
                    }
                ]
            })
            
            # Add divider between items
            if idx < len(parsed_result):
                blocks.append({
                    "type": "divider"
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
    - account: AWS account ID (required for 's3' type, optional for 'ec2')
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
    
    # Validate account_native_id is provided for s3 type
    if inventory_type == 's3' and not account_native_id:
        return jsonify({
            'error': 'Missing required parameter: account',
            'detail': 'The account parameter (AWS account ID) is required for s3 inventory type',
            'example': 'Use format: type=s3 account=1234567890 or ?type=s3&account=1234567890'
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
            slack_response = format_slack_inventory_response(parsed_result, account_native_id=account_native_id)
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
    - object_key: S3 object key (optional)
    """
    # Log incoming request details
    log_data = {
        "path": "/restore",
        "method": request.method,
        "content_type": request.content_type,
        "headers": dict(request.headers),
        "args": dict(request.args),
        "form": dict(request.form) if request.form else {},
        "values": dict(request.values) if request.values else {},
    }
    
    # Try to get JSON data
    json_data = None
    try:
        json_data = request.get_json(silent=True, force=True)
        if json_data:
            log_data["json"] = json_data
    except Exception as e:
        log_data["json_error"] = str(e)
    
    # Try to get raw data preview
    try:
        raw_data = request.get_data(as_text=True)
        if raw_data:
            log_data["raw_data_preview"] = raw_data[:500]  # First 500 chars
    except:
        pass
    
    # Log the full payload
    print(f"Restore endpoint - Full request payload: {json.dumps(log_data, indent=2, default=str)}")
    
    # Get parameters from query string (GET) or JSON/form body (POST)
    # Similar to /inventory endpoint - handle Slack's form-encoded POST requests
    restore_type = None
    bucket_name = None
    bucket_id = None
    object_key = None
    
    if request.method == 'GET':
        restore_type = request.args.get('type')
        bucket_name = request.args.get('bucket-name')
        bucket_id = request.args.get('bucket-id')
        object_key = request.args.get('object_key') or request.args.get('object-key')
    else:
        # Handle both JSON and form-encoded POST requests (e.g., from Slack)
        # Try multiple methods to extract parameters, similar to /inventory endpoint
        # Priority: request.args -> request.get_json -> request.form -> request.values
        
        # Try query string first
        restore_type = request.args.get('type')
        bucket_name = request.args.get('bucket-name')
        bucket_id = request.args.get('bucket-id')
        object_key = request.args.get('object_key') or request.args.get('object-key')
        
        # Try JSON body
        if not restore_type:
            try:
                data = request.get_json(silent=True, force=True)
                if data and isinstance(data, dict):
                    restore_type = restore_type or data.get('type')
                    bucket_name = bucket_name or data.get('bucket-name') or data.get('bucket_name')
                    bucket_id = bucket_id or data.get('bucket-id') or data.get('bucket_id')
                    object_key = object_key or data.get('object_key') or data.get('object-key')
            except:
                pass
        
        # Try form data
        if request.form:
            print(f"Restore endpoint - Form data found: {dict(request.form)}")
            restore_type = restore_type or request.form.get('type')
            bucket_name = bucket_name or request.form.get('bucket-name') or request.form.get('bucket_name')
            bucket_id = bucket_id or request.form.get('bucket-id') or request.form.get('bucket_id')
            object_key = object_key or request.form.get('object_key') or request.form.get('object-key')
            
            # Parse the 'text' field from Slack (e.g., "type=s3 account=761018876565" or "type=s3 bucket-name=mybucket")
            slack_text = request.form.get('text', '')
            print(f"Restore endpoint - Slack text field: '{slack_text}'")
            if slack_text:
                slack_text = slack_text.strip()
                print(f"Restore endpoint - Parsing slack_text: '{slack_text}'")
                
                # Parse text like "type=s3 account=1234567890" or "type=s3 bucket-name=mybucket"
                parts = slack_text.split()
                print(f"Restore endpoint - Split parts: {parts}")
                for part in parts:
                    if '=' in part:
                        key, value = part.split('=', 1)
                        key = key.strip()
                        value = value.strip()
                        print(f"Restore endpoint - Parsed key='{key}', value='{value}', current restore_type='{restore_type}'")
                        if key == 'type' and not restore_type:
                            restore_type = value
                            print(f"Restore endpoint - Set restore_type to '{value}'")
                        elif key == 'bucket-name' and not bucket_name:
                            bucket_name = value
                        elif key == 'bucket-id' and not bucket_id:
                            bucket_id = value
                        elif key == 'object-key' and not object_key:
                            object_key = value
                        elif key == 'object_key' and not object_key:
                            object_key = value
                
                # If no type found and text is just "s3" or "ec2", use it directly
                if not restore_type and slack_text in ['s3', 'ec2']:
                    restore_type = slack_text
                    print(f"Restore endpoint - Set restore_type to '{slack_text}' (direct match)")
        else:
            print("Restore endpoint - No form data found")
        
        # Try values (for form data that might not be in request.form)
        if request.values:
            restore_type = restore_type or request.values.get('type')
            bucket_name = bucket_name or request.values.get('bucket-name') or request.values.get('bucket_name')
            bucket_id = bucket_id or request.values.get('bucket-id') or request.values.get('bucket_id')
            object_key = object_key or request.values.get('object_key') or request.values.get('object-key')
            
            # Also try parsing text from values
            if not restore_type or not bucket_name:
                values_text = request.values.get('text', '')
                if values_text:
                    values_text = values_text.strip()
                    if values_text in ['s3', 'ec2'] and not restore_type:
                        restore_type = values_text
                    else:
                        # Try parsing key=value format
                        parts = values_text.split()
                        for part in parts:
                            if '=' in part:
                                key, value = part.split('=', 1)
                                key = key.strip()
                                value = value.strip()
                                if key == 'type' and not restore_type:
                                    restore_type = value
                                elif key == 'bucket-name' and not bucket_name:
                                    bucket_name = value
                                elif key == 'bucket-id' and not bucket_id:
                                    bucket_id = value
                                elif key == 'object-key' and not object_key:
                                    object_key = value
                                elif key == 'object_key' and not object_key:
                                    object_key = value
    
    # Log parsed parameters
    parsed_params = {
        "restore_type": restore_type,
        "bucket_name": bucket_name,
        "bucket_id": bucket_id,
        "object_key": object_key
    }
    print(f"Restore endpoint - Parsed parameters: {json.dumps(parsed_params, indent=2)}")
    
    # Validate required parameter
    if not restore_type:
        error_response = {
            'error': 'Missing required parameter: type',
            'method': request.method,
            'content_type': request.content_type,
            'has_json': request.is_json if hasattr(request, 'is_json') else None,
            'form_keys': list(request.form.keys()) if request.form else [],
            'form_values': {k: str(v)[:100] for k, v in request.form.items()} if request.form else {},
            'args_keys': list(request.args.keys()) if request.args else [],
            'args_values': dict(request.args) if request.args else {},
            'values_keys': list(request.values.keys()) if request.values else [],
            'values_dict': {k: str(v)[:100] for k, v in request.values.items()} if request.values else {},
            'parsed_params': parsed_params
        }
        print(f"Restore endpoint - 400 error response: {json.dumps(error_response, indent=2)}")
        return jsonify(error_response), 400
    
    # Validate type value
    if restore_type not in ['s3', 'ec2']:
        error_response = {
            'error': f'Invalid type value: {restore_type}. Accepted values: s3, ec2',
            'parsed_params': parsed_params
        }
        print(f"Restore endpoint - 400 error (invalid type): {json.dumps(error_response, indent=2)}")
        return jsonify(error_response), 400
    
    # Validate bucket-id is numeric if provided
    if bucket_id is not None:
        try:
            int(bucket_id)
        except (ValueError, TypeError):
            error_response = {
                'error': 'bucket-id must be a numeric value (string format)',
                'bucket_id_received': bucket_id,
                'parsed_params': parsed_params
            }
            print(f"Restore endpoint - 400 error (invalid bucket-id): {json.dumps(error_response, indent=2)}")
            return jsonify(error_response), 400
    
    # Log API call parameters
    api_params = {
        "restore_type": restore_type,
        "bucket_name": bucket_name,
        "bucket_id": bucket_id,
        "object_key": object_key
    }
    print(f"Restore endpoint - Calling Clumio API with: {json.dumps(api_params, indent=2)}")
    
    try:
        # Call Clumio API
        result = clumio_client.restore(
            restore_type,
            bucket_name=bucket_name,
            bucket_id=bucket_id,
            object_key=object_key
        )
        print(f"Restore endpoint - Clumio API success: {json.dumps(result, indent=2, default=str)[:500]}")
        return jsonify(result), 200
    except Exception as e:
        import traceback
        error_trace = traceback.format_exc()
        print(f"Restore endpoint - Clumio API error: {str(e)}")
        print(f"Restore endpoint - Error traceback: {error_trace}")
        return jsonify({
            'error': f'Failed to restore: {str(e)}',
            'parsed_params': parsed_params
        }), 500


@app.route('/health', methods=['GET', 'POST'])
def health():
    """Health check endpoint"""
    return jsonify({'status': 'ok'}), 200


@app.route('/slack/options', methods=['POST'])
def slack_options():
    """Handle external_select options requests from Slack"""
    if slack_handler:
        try:
            return slack_handler.handle(request)
        except Exception as e:
            import traceback
            print(f"Slack options handler error: {str(e)}")
            print(traceback.format_exc())
            return jsonify({'options': []}), 200
    
    return jsonify({'options': []}), 200


@app.route("/interactive", methods=["POST"])
def slack_interactive():
    """
    Handler for Slack interactive components (buttons, menus, etc.)
    
    Handles button clicks manually to ensure proper response format.
    """
    try:
        form_data = request.form.to_dict(flat=False) if request.form else {}
        parsed_form = {
            key: (value[0] if isinstance(value, list) and len(value) == 1 else value)
            for key, value in form_data.items()
        } if form_data else {}
        
        payload_raw = parsed_form.get("payload")
        if not payload_raw:
            return jsonify({
                "response_type": "ephemeral",
                "text": "Error: No payload received"
            }), 200
        
        # Parse the JSON payload
        payload_json = json.loads(payload_raw)
        
        # Get response_url for async updates (Slack requires immediate ack, then async update)
        response_url = payload_json.get('response_url')
        
        # Log the full payload for debugging
        log_entry = {
            "path": "/interactive",
            "method": request.method,
            "content_type": request.content_type,
            "form": parsed_form,
            "payload": payload_json
        }
        print(f"Slack interactive payload: {json.dumps(log_entry, indent=2)}")
        
        # Extract the action and value from the payload
        actions = payload_json.get('actions', [])
        if not actions:
            # Acknowledge immediately
            return jsonify({}), 200
        
        # Get action_id to determine what to do
        action_id = actions[0].get('action_id', '')
        action_value = actions[0].get('value', '{}')
        trigger_id = payload_json.get('trigger_id')
        
        # Handle view_bucket action - open modal
        if action_id == 'view_bucket' and trigger_id:
            try:
                # Parse the value JSON to get the bucket info
                item_data = json.loads(action_value)
                item_id = item_data.get('id', '')
                bucket_id = item_data.get('bucket-id', '')
                bucket_name = item_data.get('bucket-name', '')
                
                # Retrieve backup metadata for this bucket
                backup_data = clumio_client.get_s3_asset_backups(item_id)
                backups = backup_data.get('_embedded', {}).get('items', [])
                
                # Build modal blocks
                blocks = [
                    {
                        "type": "section",
                        "text": {
                            "type": "mrkdwn",
                            "text": f"*Bucket:* {bucket_name}\n*Bucket ID:* {bucket_id or 'n/a'}\n*Asset ID:* {item_id or 'n/a'}"
                        }
                    },
                    {
                        "type": "divider"
                    }
                ]
                
                if backups:
                    # Add header for backups list
                    blocks.append({
                        "type": "section",
                        "text": {
                            "type": "mrkdwn",
                            "text": f"*Backups ({len(backups)} total):*"
                        }
                    })
                    
                    # Add each backup as a section (limit to 50 for modal)
                    for idx, backup in enumerate(backups[:50], 1):
                        backup_id = backup.get('id', 'unknown')
                        backup_time = backup.get('backup_timestamp', backup.get('created_at', 'unknown'))
                        backup_status = backup.get('status', 'unknown')
                        backup_size = backup.get('size', backup.get('backup_size', 'unknown'))
                        
                        blocks.append({
                            "type": "section",
                            "text": {
                                "type": "mrkdwn",
                                "text": (
                                    f"*{idx}. Backup ID:* `{backup_id}`\n"
                                    f"*Time:* {backup_time}\n"
                                    f"*Status:* {backup_status}\n"
                                    f"*Size:* {backup_size}"
                                )
                            }
                        })
                        
                        # Add divider between backups (except for last one)
                        if idx < min(len(backups), 50):
                            blocks.append({
                                "type": "divider"
                            })
                    
                    if len(backups) > 50:
                        blocks.append({
                            "type": "section",
                            "text": {
                                "type": "mrkdwn",
                                "text": f"*... and {len(backups) - 50} more backups*"
                            }
                        })
                else:
                    blocks.append({
                        "type": "section",
                        "text": {
                            "type": "mrkdwn",
                            "text": f"*No backups found for bucket: {bucket_name}*"
                        }
                    })
                
                # Open modal using Slack Web API
                import requests
                slack_bot_token = os.getenv('SLACK_BOT_TOKEN')
                if slack_bot_token:
                    modal_response = requests.post(
                        'https://slack.com/api/views.open',
                        headers={
                            'Authorization': f'Bearer {slack_bot_token}',
                            'Content-Type': 'application/json'
                        },
                        json={
                            'trigger_id': trigger_id,
                            'view': {
                                "type": "modal",
                                "callback_id": "view_bucket_modal",
                                "title": {
                                    "type": "plain_text",
                                    "text": f"Backups: {bucket_name[:50]}"
                                },
                                "close": {
                                    "type": "plain_text",
                                    "text": "Close"
                                },
                                "blocks": blocks,
                                "private_metadata": json.dumps({
                                    "item_id": item_id,
                                    "bucket_id": bucket_id,
                                    "bucket_name": bucket_name
                                })
                            }
                        }
                    )
                    print(f"Modal open response: {modal_response.status_code} - {modal_response.text}")
                else:
                    print("SLACK_BOT_TOKEN not found, cannot open modal")
                
                # Acknowledge immediately
                return jsonify({}), 200
                
            except Exception as e:
                import traceback
                print(f"Error opening modal for view_bucket: {str(e)}")
                print(traceback.format_exc())
                # Fall through to acknowledge
        
        # Parse the value JSON to get the asset_id (for other actions)
        try:
            item_data = json.loads(action_value)
            asset_id = item_data.get('id', '')
            bucket_name = item_data.get('bucket-name', 'Unknown')
            bucket_id = item_data.get('bucket-id', '')
        except Exception as e:
            # Acknowledge immediately, then send error via response_url
            if response_url:
                import requests
                requests.post(response_url, json={
                    "response_type": "ephemeral",
                    "replace_original": True,
                    "text": f"Error parsing button value: {str(e)}"
                })
            return jsonify({}), 200
        
        if not asset_id:
            # Acknowledge immediately, then send error via response_url
            if response_url:
                import requests
                requests.post(response_url, json={
                    "response_type": "ephemeral",
                    "replace_original": True,
                    "text": "Error: No asset_id found in button value"
                })
            return jsonify({}), 200
        
        # Acknowledge immediately (required by Slack within 3 seconds)
        # Then make API call and update via response_url
        import requests
        import threading
        
        def update_slack_message():
            try:
                backup_data = clumio_client.get_s3_asset_backups(asset_id)
                backups = backup_data.get('_embedded', {}).get('items', [])
                
                # Build response blocks
                blocks = [
                    {
                        "type": "section",
                        "text": {
                            "type": "mrkdwn",
                            "text": f"*Backup information for: {bucket_name}*"
                        }
                    },
                    {
                        "type": "divider"
                    }
                ]
                
                if backups:
                    # Show list of backups
                    for idx, backup in enumerate(backups[:10], 1):  # Limit to first 10 backups
                        backup_id = backup.get('id', 'unknown')
                        backup_time = backup.get('backup_timestamp', backup.get('created_at', 'unknown'))
                        backup_status = backup.get('status', 'unknown')
                        
                        blocks.append({
                            "type": "section",
                            "text": {
                                "type": "mrkdwn",
                                "text": f"*{idx}. Backup ID:* `{backup_id}`\n*Time:* {backup_time}\n*Status:* {backup_status}"
                            }
                        })
                    
                    if len(backups) > 10:
                        blocks.append({
                            "type": "section",
                            "text": {
                                "type": "mrkdwn",
                                "text": f"*... and {len(backups) - 10} more backups*"
                            }
                        })
                    
                    # Add full JSON as code block
                    backup_json = json.dumps(backup_data, indent=2)
                    blocks.append({
                        "type": "divider"
                    })
                    blocks.append({
                        "type": "section",
                        "text": {
                            "type": "mrkdwn",
                            "text": f"*Full API Response:*\n```{backup_json}```"
                        }
                    })
                else:
                    blocks.append({
                        "type": "section",
                        "text": {
                            "type": "mrkdwn",
                            "text": f"No backups found for bucket: {bucket_name} (Asset ID: {asset_id})"
                        }
                    })
                
                # Update the message via response_url
                if response_url:
                    update_response = requests.post(response_url, json={
                        "response_type": "ephemeral",
                        "replace_original": True,
                        "blocks": blocks
                    })
                    print(f"Posted to response_url, status: {update_response.status_code}")
                    print(f"Response: {update_response.text}")
                else:
                    print("No response_url available")
                    
            except Exception as e:
                import traceback
                error_trace = traceback.format_exc()
                print(f"Error calling Clumio API: {str(e)}")
                print(error_trace)
                
                # Send error via response_url
                if response_url:
                    requests.post(response_url, json={
                        "response_type": "ephemeral",
                        "replace_original": True,
                        "text": f"Error retrieving backups: {str(e)}"
                    })
        
        # Start async update in background thread
        if response_url:
            thread = threading.Thread(target=update_slack_message)
            thread.daemon = True
            thread.start()
            print(f"Started background thread to update Slack message via response_url")
        else:
            print("Warning: No response_url in payload, cannot update message")
        
        # Return immediate acknowledgment (empty response)
        return jsonify({}), 200
            
    except Exception as e:
        import traceback
        error_msg = str(e)
        error_details = {
            "error": error_msg,
            "content_type": request.content_type,
            "method": request.method
        }
        print(f"Slack interactive error: {error_msg}")
        print(f"Content-Type: {request.content_type}")
        print(f"Request data available: {hasattr(request, 'get_data')}")
        print(traceback.format_exc())
        return jsonify({
            "response_type": "ephemeral",
            "text": f"Error processing request: {error_msg}"
        }), 200


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
        
        # Validate account_native_id is provided for s3 type
        if inventory_type == "s3" and not account_native_id:
            respond(
                text="Error: Missing required parameter 'account'. Use format: /inventory type=s3 account=1234567890",
                response_type="ephemeral"
            )
            return
        
        try:
            parsed_result = get_inventory_data(inventory_type, account_native_id=account_native_id)
            slack_response = format_slack_inventory_response(parsed_result, account_native_id=account_native_id)
            respond(**slack_response)
        except Exception as e:
            respond(
                text=f"Error retrieving inventory: {str(e)}",
                response_type="ephemeral"
            )
    
    # Slack button action handlers
    @slack_app.action("view_bucket")
    def handle_view_bucket(ack, body, client, respond):
        """Handle View Bucket button click - opens modal with backup list"""
        ack()
        
        try:
            # Log the full body to debug
            print(f"View bucket action - Full body: {json.dumps(body, indent=2, default=str)}")
            print(f"View bucket action - body keys: {list(body.keys())}")
            print(f"View bucket action - trigger_id: {body.get('trigger_id', 'NOT FOUND')}")
            print(f"View bucket action - has actions: {bool(body.get('actions'))}")
            print(f"View bucket action - channel: {body.get('channel', {}).get('id', 'NOT FOUND')}")
            
            # Parse the value from the button
            value = body.get('actions', [{}])[0].get('value', '{}')
            item_data = json.loads(value)
            
            # Extract the required fields: id, bucket_id, bucket_name
            item_id = item_data.get('id', '')
            bucket_id = item_data.get('bucket-id', '')
            bucket_name = item_data.get('bucket-name', '')
            
            # Retrieve backup metadata for this bucket
            backup_data = clumio_client.get_s3_asset_backups(item_id)
            backups = backup_data.get('_embedded', {}).get('items', [])
            
            # Build response blocks (for message response)
            response_blocks = [
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": f"*Bucket:* {bucket_name}\n*Bucket ID:* {bucket_id or 'n/a'}\n*Asset ID:* {item_id or 'n/a'}"
                    }
                },
                {
                    "type": "divider"
                }
            ]
            
            if backups:
                # Add header for backups list
                response_blocks.append({
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": f"*Backups ({len(backups)} total):*"
                    }
                })
                
                # Add each backup as a section (limit to 50 for display)
                for idx, backup in enumerate(backups[:50], 1):
                    backup_id = backup.get('id', 'unknown')
                    backup_time = backup.get('backup_timestamp', backup.get('created_at', 'unknown'))
                    backup_status = backup.get('status', 'unknown')
                    backup_size = backup.get('size', backup.get('backup_size', 'unknown'))
                    
                    response_blocks.append({
                        "type": "section",
                        "text": {
                            "type": "mrkdwn",
                            "text": (
                                f"*{idx}. Backup ID:* `{backup_id}`\n"
                                f"*Time:* {backup_time}\n"
                                f"*Status:* {backup_status}\n"
                                f"*Size:* {backup_size}"
                            )
                        }
                    })
                    
                    # Add divider between backups (except for last one)
                    if idx < min(len(backups), 50):
                        response_blocks.append({
                            "type": "divider"
                        })
                
                if len(backups) > 50:
                    response_blocks.append({
                        "type": "section",
                        "text": {
                            "type": "mrkdwn",
                            "text": f"*... and {len(backups) - 50} more backups*"
                        }
                    })
            else:
                response_blocks.append({
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": f"*No backups found for bucket: {bucket_name}*"
                    }
                })
            
            # Try to open modal if trigger_id is available
            trigger_id = body.get('trigger_id')
            if trigger_id:
                try:
                    # Build modal blocks (same as response blocks)
                    modal_blocks = response_blocks.copy()
                    
                    client.views_open(
                        trigger_id=trigger_id,
                        view={
                            "type": "modal",
                            "callback_id": "view_bucket_modal",
                            "title": {
                                "type": "plain_text",
                                "text": f"Backups: {bucket_name[:50]}"  # Limit title length
                            },
                            "close": {
                                "type": "plain_text",
                                "text": "Close"
                            },
                            "blocks": modal_blocks,
                            "private_metadata": json.dumps({
                                "item_id": item_id,
                                "bucket_id": bucket_id,
                                "bucket_name": bucket_name
                            })
                        }
                    )
                    print(f"Successfully opened modal for bucket: {bucket_name}")
                    return
                except Exception as e:
                    import traceback
                    error_msg = str(e)
                    error_trace = traceback.format_exc()
                    print(f"Error opening view bucket modal: {error_msg}")
                    print(f"Traceback: {error_trace}")
                    # Fall through to respond with message
            
            # Fallback: respond with message if modal can't be opened
            print("Falling back to message response (no trigger_id or modal failed)")
            respond(
                blocks=response_blocks,
                replace_original=False,
                response_type="ephemeral"
            )
            
        except Exception as e:
            import traceback
            error_msg = str(e)
            error_trace = traceback.format_exc()
            print(f"Error in handle_view_bucket: {error_msg}")
            print(f"Traceback: {error_trace}")
            # Try to send error message
            try:
                respond(
                    text=f"Error retrieving bucket backups: {error_msg}",
                    replace_original=False,
                    response_type="ephemeral"
                )
            except:
                pass
    
    @slack_app.command("/restore")
    def handle_restore_command(ack, body, client, respond):
        """Handle /restore Slack command - opens modal for restore workflow or performs restore if parameters provided"""
        ack()
        
        text = body.get("text", "").strip()
        
        # Parse parameters from text (e.g., "type=s3 account=761018876565" or "type=s3 bucket-name=mybucket")
        restore_type = None
        account_native_id = None
        bucket_name = None
        bucket_id = None
        object_key = None
        
        if text:
            parts = text.split()
            for part in parts:
                if "=" in part:
                    key, value = part.split("=", 1)
                    key = key.strip()
                    value = value.strip()
                    if key == "type":
                        restore_type = value
                    elif key == "account":
                        account_native_id = value
                    elif key == "bucket-name":
                        bucket_name = value
                    elif key == "bucket-id":
                        bucket_id = value
                    elif key == "object-key" or key == "object_key":
                        object_key = value
        
        # If type is provided, try to perform restore directly (if we have bucket info)
        # Otherwise, open modal
        if restore_type and (bucket_name or bucket_id):
            # Perform restore directly
            try:
                result = clumio_client.restore(
                    restore_type,
                    bucket_name=bucket_name,
                    bucket_id=bucket_id,
                    object_key=object_key
                )
                
                result_json = json.dumps(result, indent=2)
                respond(
                    blocks=[
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
                                "text": f"*Restore initiated successfully* :rocket:\n\n*Type:* {restore_type}\n*Bucket:* {bucket_name or 'N/A'}\n*Bucket ID:* {bucket_id or 'N/A'}"
                            }
                        },
                        {
                            "type": "section",
                            "text": {
                                "type": "mrkdwn",
                                "text": f"```{result_json}```"
                            }
                        }
                    ],
                    response_type="ephemeral"
                )
            except Exception as e:
                import traceback
                print(f"Restore command error: {str(e)}")
                print(traceback.format_exc())
                respond(
                    text=f"Error performing restore: {str(e)}",
                    response_type="ephemeral"
                )
            return
        
        # Open modal (default behavior when no bucket info provided)
        try:
            result = client.views_open(
                trigger_id=body["trigger_id"],
                view={
                    "type": "modal",
                    "callback_id": "restore_modal",
                    "title": {
                        "type": "plain_text",
                        "text": "Restore from Clumio"
                    },
                    "submit": {
                        "type": "plain_text",
                        "text": "Restore"
                    },
                    "close": {
                        "type": "plain_text",
                        "text": "Cancel"
                    },
                    "blocks": [
                        {
                            "type": "input",
                            "block_id": "account_input",
                            "element": {
                                "type": "plain_text_input",
                                "action_id": "account_value",
                                "placeholder": {
                                    "type": "plain_text",
                                    "text": "Enter AWS Account ID (e.g., 1234567890)"
                                },
                                "initial_value": account_native_id or ""
                            },
                            "label": {
                                "type": "plain_text",
                                "text": "AWS Account ID"
                            }
                        },
                        {
                            "type": "input",
                            "block_id": "bucket_select",
                            "element": {
                                "type": "external_select",
                                "action_id": "bucket_selection",
                                "placeholder": {
                                    "type": "plain_text",
                                    "text": "Select a bucket..."
                                },
                                "min_query_length": 0
                            },
                            "label": {
                                "type": "plain_text",
                                "text": "Select Bucket"
                            }
                        },
                        {
                            "type": "input",
                            "block_id": "object_select",
                            "element": {
                                "type": "external_select",
                                "action_id": "object_selection",
                                "placeholder": {
                                    "type": "plain_text",
                                    "text": "Select a file/object..."
                                },
                                "min_query_length": 0
                            },
                            "label": {
                                "type": "plain_text",
                                "text": "Select File/Object"
                            },
                            "optional": True
                        }
                    ],
                    "private_metadata": json.dumps({
                        "account_native_id": account_native_id or ""
                    })
                }
            )
            print(f"Modal opened successfully: {result}")
        except Exception as e:
            import traceback
            error_msg = str(e)
            error_trace = traceback.format_exc()
            print(f"Error opening restore modal: {error_msg}")
            print(error_trace)
            # Send error message to user
            respond(
                text=f"Error opening restore modal: {error_msg}\n\nPlease check the logs for details.",
                response_type="ephemeral"
            )
    
    @slack_app.options("bucket_selection")
    def handle_bucket_options(ack, body):
        """Handle external_select options request for bucket selection"""
        try:
            query = body.get("value", "").strip()
            account_native_id = None
            
            # Try to get account from private_metadata if available
            view = body.get("view", {})
            private_metadata = view.get("private_metadata", "{}")
            try:
                metadata = json.loads(private_metadata)
                account_native_id = metadata.get("account_native_id", "")
            except:
                pass
            
            # If no account in metadata, try to get from user input
            if not account_native_id and view:
                state_values = view.get("state", {}).get("values", {})
                account_input = state_values.get("account_input", {})
                account_value = account_input.get("account_value", {})
                account_native_id = account_value.get("value", "")
            
            if not account_native_id:
                ack(options=[
                    {
                        "text": {
                            "type": "plain_text",
                            "text": "Please enter AWS Account ID first"
                        },
                        "value": "no_account"
                    }
                ])
                return
            
            # Get buckets from Clumio API
            buckets_data = clumio_client.get_s3_buckets_for_restore(account_native_id)
            buckets = buckets_data.get('_embedded', {}).get('items', [])
            
            options = []
            for bucket in buckets:
                bucket_name = bucket.get('bucket_name', '')
                bucket_id = bucket.get('bucket_id', '')
                asset_id = bucket.get('id', '')
                
                if query and query.lower() not in bucket_name.lower():
                    continue
                
                options.append({
                    "text": {
                        "type": "plain_text",
                        "text": bucket_name
                    },
                    "value": json.dumps({
                        "id": asset_id,
                        "bucket_id": bucket_id,
                        "bucket_name": bucket_name
                    })
                })
            
            if not options:
                options.append({
                    "text": {
                        "type": "plain_text",
                        "text": "No buckets found"
                    },
                    "value": "no_buckets"
                })
            
            ack(options=options[:100])  # Limit to 100 options
        except Exception as e:
            import traceback
            print(f"Error in bucket options: {str(e)}")
            print(traceback.format_exc())
            ack(options=[{
                "text": {
                    "type": "plain_text",
                    "text": f"Error: {str(e)}"
                },
                "value": "error"
            }])
    
    @slack_app.action("bucket_selection")
    def handle_bucket_selection(ack, body, client):
        """Handle bucket selection - update modal with objects"""
        ack()
        
        try:
            view = body.get("view", {})
            view_id = view.get("id")
            selected_bucket = body.get("actions", [{}])[0].get("selected_option", {}).get("value", "{}")
            bucket_data = json.loads(selected_bucket)
            asset_id = bucket_data.get("id")
            
            # Get objects from the selected bucket
            objects_data = clumio_client.get_s3_bucket_objects(asset_id)
            objects = objects_data.get('_embedded', {}).get('items', [])
            
            # Update the object select block
            object_block = {
                "type": "input",
                "block_id": "object_select",
                "element": {
                    "type": "external_select",
                    "action_id": "object_selection",
                    "placeholder": {
                        "type": "plain_text",
                        "text": "Select a file/object..."
                    },
                    "min_query_length": 0
                },
                "label": {
                    "type": "plain_text",
                    "text": "Select File/Object"
                },
                "optional": True
            }
            
            # If objects list is small enough, use static_select instead
            if len(objects) <= 100:
                object_block["element"] = {
                    "type": "static_select",
                    "action_id": "object_selection",
                    "placeholder": {
                        "type": "plain_text",
                        "text": "Select a file/object..."
                    },
                    "options": [
                        {
                            "text": {
                                "type": "plain_text",
                                "text": obj.get("key", obj.get("name", "Unknown"))
                            },
                            "value": json.dumps({
                                "key": obj.get("key", obj.get("name", "")),
                                "backup_id": obj.get("backup_id", "")
                            })
                        }
                        for obj in objects[:100]
                    ]
                }
            
            # Update the view
            client.views_update(
                view_id=view_id,
                view={
                    "type": "modal",
                    "callback_id": "restore_modal",
                    "title": {
                        "type": "plain_text",
                        "text": "Restore from Clumio"
                    },
                    "submit": {
                        "type": "plain_text",
                        "text": "Restore"
                    },
                    "close": {
                        "type": "plain_text",
                        "text": "Cancel"
                    },
                    "blocks": view.get("blocks", [])[:2] + [object_block],  # Keep account and bucket, update object
                    "private_metadata": view.get("private_metadata", "{}")
                }
            )
        except Exception as e:
            import traceback
            print(f"Error updating modal with objects: {str(e)}")
            print(traceback.format_exc())
    
    @slack_app.options("object_selection")
    def handle_object_options(ack, body):
        """Handle external_select options request for object selection"""
        try:
            query = body.get("value", "").strip()
            view = body.get("view", {})
            state_values = view.get("state", {}).get("values", {})
            
            # Get selected bucket
            bucket_select = state_values.get("bucket_select", {})
            bucket_selection = bucket_select.get("bucket_selection", {})
            selected_bucket = bucket_selection.get("selected_option", {}).get("value")
            
            if not selected_bucket:
                ack(options=[
                    {
                        "text": {
                            "type": "plain_text",
                            "text": "Please select a bucket first"
                        },
                        "value": "no_bucket"
                    }
                ])
                return
            
            try:
                bucket_data = json.loads(selected_bucket)
                asset_id = bucket_data.get("id")
            except:
                ack(options=[
                    {
                        "text": {
                            "type": "plain_text",
                            "text": "Invalid bucket selection"
                        },
                        "value": "invalid_bucket"
                    }
                ])
                return
            
            # Get objects from Clumio API
            objects_data = clumio_client.get_s3_bucket_objects(asset_id)
            objects = objects_data.get('_embedded', {}).get('items', [])
            
            options = []
            for obj in objects:
                obj_key = obj.get("key", obj.get("name", ""))
                
                if query and query.lower() not in obj_key.lower():
                    continue
                
                options.append({
                    "text": {
                        "type": "plain_text",
                        "text": obj_key
                    },
                    "value": json.dumps({
                        "key": obj_key,
                        "backup_id": obj.get("backup_id", "")
                    })
                })
            
            if not options:
                options.append({
                    "text": {
                        "type": "plain_text",
                        "text": "No objects found"
                    },
                    "value": "no_objects"
                })
            
            ack(options=options[:100])  # Limit to 100 options
        except Exception as e:
            import traceback
            print(f"Error in object options: {str(e)}")
            print(traceback.format_exc())
            ack(options=[{
                "text": {
                    "type": "plain_text",
                    "text": f"Error: {str(e)}"
                },
                "value": "error"
            }])
    
    @slack_app.view("restore_modal")
    def handle_restore_submission(ack, body, client, view):
        """Handle modal submission - perform restore"""
        ack()
        
        try:
            state_values = view.get("state", {}).get("values", {})
            
            # Get account ID
            account_input = state_values.get("account_input", {})
            account_value = account_input.get("account_value", {})
            account_native_id = account_value.get("value", "")
            
            if not account_native_id:
                client.views_update(
                    view_id=body["view"]["id"],
                    view={
                        "type": "modal",
                        "title": {
                            "type": "plain_text",
                            "text": "Error"
                        },
                        "blocks": [
                            {
                                "type": "section",
                                "text": {
                                    "type": "mrkdwn",
                                    "text": "❌ Error: AWS Account ID is required"
                                }
                            }
                        ]
                    }
                )
                return
            
            # Get selected bucket
            bucket_select = state_values.get("bucket_select", {})
            bucket_selection = bucket_select.get("bucket_selection", {})
            selected_bucket = bucket_selection.get("selected_option", {}).get("value", "{}")
            bucket_data = json.loads(selected_bucket)
            bucket_name = bucket_data.get("bucket_name", "")
            bucket_id = bucket_data.get("bucket_id", "")
            
            # Get selected object (optional)
            object_key = None
            object_select = state_values.get("object_select", {})
            if object_select:
                object_selection = object_select.get("object_selection", {})
                selected_object = object_selection.get("selected_option", {}).get("value")
                if selected_object:
                    object_data = json.loads(selected_object)
                    object_key = object_data.get("key", "")
            
            # Call restore API
            result = clumio_client.restore(
                's3',
                bucket_name=bucket_name,
                bucket_id=str(bucket_id) if bucket_id else None,
                object_key=object_key
            )
            
            # Show success message
            client.views_update(
                view_id=body["view"]["id"],
                view={
                    "type": "modal",
                    "title": {
                        "type": "plain_text",
                        "text": "Restore Successful"
                    },
                    "blocks": [
                        {
                            "type": "section",
                            "text": {
                                "type": "mrkdwn",
                                "text": f"✅ *Restore initiated successfully!*\n\n*Bucket:* {bucket_name}\n*Object:* {object_key or 'Entire bucket'}\n\n*Response:*\n```{json.dumps(result, indent=2)}```"
                            }
                        }
                    ]
                }
            )
        except Exception as e:
            import traceback
            error_msg = str(e)
            print(f"Error in restore submission: {error_msg}")
            print(traceback.format_exc())
            
            # Show error message
            client.views_update(
                view_id=body["view"]["id"],
                view={
                    "type": "modal",
                    "title": {
                        "type": "plain_text",
                        "text": "Restore Error"
                    },
                    "blocks": [
                        {
                            "type": "section",
                            "text": {
                                "type": "mrkdwn",
                                "text": f"❌ *Error:* {error_msg}"
                            }
                        }
                    ]
                }
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

