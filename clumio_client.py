import os
import requests
import json
from urllib.parse import quote
from typing import Optional, Dict, Any


class ClumioClient:
    """
    Client for interacting with the Clumio API
    """
    
    def __init__(self, api_token: str, api_base_url: str = 'https://api.clumio.com'):
        """
        Initialize Clumio API client
        
        Args:
            api_token: Clumio API token
            api_base_url: Base URL for Clumio API (default: https://api.clumio.com)
        """
        self.api_token = api_token
        self.api_base_url = api_base_url.rstrip('/')
        self.headers = {
            'Authorization': f'Bearer {api_token}',
            'Content-Type': 'application/json',
            'Clumio-Api-Version': '1.0'
        }
    
    def _make_request(self, method: str, endpoint: str, params: Optional[Dict] = None, 
                     data: Optional[Dict] = None) -> Dict[str, Any]:
        """
        Make a request to the Clumio API
        
        Args:
            method: HTTP method (GET, POST, etc.)
            endpoint: API endpoint path
            params: Query parameters
            data: Request body data
            
        Returns:
            Response JSON as dictionary
            
        Raises:
            requests.exceptions.HTTPError: If the HTTP request returns an error status
            requests.exceptions.RequestException: If the request fails
        """
        url = f"{self.api_base_url}/{endpoint.lstrip('/')}"
        
        try:
            response = requests.request(
                method=method,
                url=url,
                headers=self.headers,
                params=params,
                json=data,
                timeout=30
            )
            
            response.raise_for_status()
            return response.json()
        except requests.exceptions.HTTPError as e:
            # Try to extract error details from response
            try:
                error_detail = response.json()
            except:
                error_detail = {'message': response.text}
            raise requests.exceptions.HTTPError(
                f"Clumio API error: {e.response.status_code} - {error_detail}",
                response=e.response
            )
    
    def get_inventory(self, inventory_type: str, account_native_id: Optional[str] = None) -> Dict[str, Any]:
        """
        Retrieve inventory data from Clumio API
        
        Args:
            inventory_type: Type of inventory ('s3' or 'ec2')
            account_native_id: Optional AWS account ID (defaults to '761018876565' for backward compatibility)
            
        Returns:
            Inventory data from Clumio API
        """
        if inventory_type == 's3':
            # Use provided account_native_id or default to the hardcoded value for backward compatibility
            account_id = account_native_id or '761018876565'
            # Build and URL-encode the filter JSON
            filter_dict = {"account_native_id": {"$eq": account_id}}
            filter_json = json.dumps(filter_dict)
            filter_encoded = quote(filter_json)
            endpoint = f'/datasources/protection-groups/s3-assets?filter={filter_encoded}'
        elif inventory_type == 'ec2':
            endpoint = '/inventory/protected-items/aws/ec2'
        else:
            raise ValueError(f"Invalid inventory type: {inventory_type}")
        
        return self._make_request('GET', endpoint)
    
    def restore(self, restore_type: str, bucket_name: Optional[str] = None, 
               bucket_id: Optional[str] = None) -> Dict[str, Any]:
        """
        Restore data from Clumio API
        
        Args:
            restore_type: Type of restore ('s3' or 'ec2')
            bucket_name: Optional bucket name (string)
            bucket_id: Optional bucket ID (numeric string)
            
        Returns:
            Restore response from Clumio API
        """
        # Build request data
        data = {}
        
        if bucket_name:
            data['bucket_name'] = bucket_name
        
        if bucket_id:
            data['bucket_id'] = int(bucket_id) if bucket_id.isdigit() else bucket_id
        
        if restore_type == 's3':
            endpoint = '/restore/aws/s3'
        elif restore_type == 'ec2':
            endpoint = '/restore/aws/ec2'
        else:
            raise ValueError(f"Invalid restore type: {restore_type}")
        
        return self._make_request('POST', endpoint, data=data)

