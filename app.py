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
    slack_app = SlackApp(token=slack_bot_token)
    slack_handler = SlackRequestHandler(slack_app)


# Shared helper functions for inventory and restore
def get_inventory_data(inventory_type):
    """Shared function to get inventory data"""
    result = clumio_client.get_inventory(inventory_type)
    
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
    """Format inventory data for Slack"""
    json_payload = json.dumps(parsed_result, indent=2)
    
    return {
        "response_type": "ephemeral",
        "blocks": [
            {
                "type": "header",
                "text": {
                    "type": "plain_text",
                    "text": "Clumio Inventory",
                    "emoji": True
                }
            },
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"```{json_payload}```"
                }
            }
        ]
    }


@app.route('/inventory', methods=['GET', 'POST'])
def inventory():
    """
    Endpoint to retrieve inventory data from Clumio API
    
    Required input:
    - type: 's3' or 'ec2'
    """
    # Get type parameter from query string (GET) or JSON body (POST)
    if request.method == 'GET':
        inventory_type = request.args.get('type')
    else:
        data = request.get_json() or {}
        inventory_type = data.get('type')
    
    # Validate required parameter
    if not inventory_type:
        return jsonify({
            'error': 'Missing required parameter: type'
        }), 400
    
    # Validate type value
    if inventory_type not in ['s3', 'ec2']:
        return jsonify({
            'error': f'Invalid type value: {inventory_type}. Accepted values: s3, ec2'
        }), 400
    
    try:
        # Get inventory data using shared function
        if inventory_type == 's3':
            parsed_result = get_inventory_data(inventory_type)
            slack_response = format_slack_inventory_response(parsed_result)
            return jsonify(slack_response), 200
        else:
            # For EC2 or other types, return the raw response
            result = clumio_client.get_inventory(inventory_type)
            return jsonify(result), 200
            
    except Exception as e:
        return jsonify({
            'error': f'Failed to retrieve inventory: {str(e)}'
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
    # Get parameters from query string (GET) or JSON body (POST)
    if request.method == 'GET':
        restore_type = request.args.get('type')
        bucket_name = request.args.get('bucket-name')
        bucket_id = request.args.get('bucket-id')
    else:
        data = request.get_json() or {}
        restore_type = data.get('type')
        bucket_name = data.get('bucket-name')
        bucket_id = data.get('bucket-id')
    
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


@app.route('/health', methods=['GET'])
def health():
    """Health check endpoint"""
    return jsonify({'status': 'ok'}), 200


# Slack command handlers
if slack_app:
    @slack_app.command("/inventory")
    def handle_inventory_command(ack, respond, command):
        """Handle /inventory Slack command"""
        ack()
        
        text = command.get("text", "").strip()
        
        # Default to s3 if no type specified
        if not text:
            inventory_type = "s3"
        else:
            if "=" in text:
                parts = text.split("=")
                if len(parts) == 2 and parts[0].strip() == "type":
                    inventory_type = parts[1].strip()
                else:
                    inventory_type = text
            else:
                inventory_type = text
        
        if inventory_type not in ["s3", "ec2"]:
            respond(
                text=f"Invalid type: {inventory_type}. Accepted values: s3, ec2",
                response_type="ephemeral"
            )
            return
        
        try:
            parsed_result = get_inventory_data(inventory_type)
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
    @app.route("/slack/events", methods=["POST"])
    def slack_events():
        """Handle Slack events and commands"""
        if not slack_handler:
            return jsonify({"error": "Slack not configured"}), 500
        
        try:
            # Handle URL verification challenge from Slack (Events API)
            # This happens when Slack first verifies your endpoint URL
            content_type = request.content_type or ''
            if 'application/json' in content_type:
                data = request.get_json(silent=True, force=True)
                if data and data.get('type') == 'url_verification':
                    return jsonify({'challenge': data.get('challenge')}), 200
            
            # SlackRequestHandler automatically handles:
            # - application/x-www-form-urlencoded (slash commands)
            # - application/json (Events API)
            # It processes the request and returns a Flask response
            return slack_handler.handle(request)
        except Exception as e:
            # Log detailed error information
            import traceback
            error_msg = str(e)
            error_details = {
                "error": error_msg,
                "content_type": request.content_type,
                "method": request.method
            }
            print(f"Slack event error: {error_msg}")
            print(f"Content-Type: {request.content_type}")
            print(traceback.format_exc())
            return jsonify(error_details), 500


# Vercel serverless function handler
# This allows Vercel to serve the Flask app
if __name__ == '__main__':
    app.run(debug=True)

