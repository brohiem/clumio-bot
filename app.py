from flask import Flask, request, jsonify
import os
import json
from clumio_client import ClumioClient

app = Flask(__name__)

# Initialize Clumio client
clumio_client = ClumioClient(
    api_token=os.getenv('CLUMIO_API_TOKEN', ''),
    api_base_url=os.getenv('CLUMIO_API_BASE_URL', 'https://api.clumio.com')
)


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
        # Call Clumio API
        result = clumio_client.get_inventory(inventory_type)
        
        # Parse and format the response for S3 type
        if inventory_type == 's3':
            parsed_result = []
            items = result.get('_embedded', {}).get('items', [])
            
            for item in items:
                parsed_item = {
                    'bucket-id': item.get('bucket_id', ''),
                    'bucket-name': item.get('bucket_name', '')
                }
                parsed_result.append(parsed_item)
            
            # Format as JSON string for Slack
            json_payload = json.dumps(parsed_result, indent=2)
            
            # Create Slack-formatted response
            slack_response = {
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
            
            return jsonify(slack_response), 200
        else:
            # For EC2 or other types, return the raw response
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


# Vercel serverless function handler
# This allows Vercel to serve the Flask app
if __name__ == '__main__':
    app.run(debug=True)

