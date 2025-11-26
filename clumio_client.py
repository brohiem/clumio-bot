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
            account_native_id: Required AWS account ID for 's3' type, optional for 'ec2'
            
        Returns:
            Inventory data from Clumio API
            
        Raises:
            ValueError: If account_native_id is not provided for 's3' type
        """
        if inventory_type == 's3':
            # Require account_native_id for s3 type
            if not account_native_id:
                raise ValueError("account_native_id is required for s3 inventory type")
            
            # Build and URL-encode the filter JSON
            filter_dict = {"account_native_id": {"$eq": account_native_id}}
            filter_json = json.dumps(filter_dict)
            filter_encoded = quote(filter_json)
            endpoint = f'/datasources/protection-groups/s3-assets?filter={filter_encoded}'
        elif inventory_type == 'ec2':
            endpoint = '/inventory/protected-items/aws/ec2'
        else:
            raise ValueError(f"Invalid inventory type: {inventory_type}")
        
        return self._make_request('GET', endpoint)
    
    def get_s3_asset_backups(self, protection_group_s3_asset_id: str) -> Dict[str, Any]:
        """
        Retrieve backups for a specific S3 protection group asset.
        
        Args:
            protection_group_s3_asset_id: The asset ID to filter backups on.
        """
        filter_dict = {
            "protection_group_s3_asset_id": {
                "$eq": protection_group_s3_asset_id
            }
        }
        params = {
            "filter": json.dumps(filter_dict)
        }
        endpoint = '/backups/protection-groups/s3-assets'
        return self._make_request('GET', endpoint, params=params)
    
    def get_s3_buckets_for_restore(self, account_native_id: str) -> Dict[str, Any]:
        """
        Get list of S3 buckets available for restore.
        
        Args:
            account_native_id: AWS account ID
            
        Returns:
            List of buckets with id, bucket_name, bucket_id
        """
        return self.get_inventory('s3', account_native_id=account_native_id)
    
    def get_s3_bucket_objects(self, protection_group_s3_asset_id: str, 
                              backup_id: Optional[str] = None) -> Dict[str, Any]:
        """
        Get list of objects in an S3 bucket from backups.
        
        Args:
            protection_group_s3_asset_id: The protection group S3 asset ID
            backup_id: Optional backup ID to filter objects from specific backup
            
        Returns:
            List of objects in the bucket
        """
        # First get backups for this asset
        backups_data = self.get_s3_asset_backups(protection_group_s3_asset_id)
        backups = backups_data.get('_embedded', {}).get('items', [])
        
        if not backups:
            return {'_embedded': {'items': []}}
        
        # Use the latest backup if no backup_id specified
        if not backup_id:
            latest_backup = backups[0]
            backup_id = latest_backup.get('id')
        
        # Get objects from the backup
        # Note: This endpoint may vary based on Clumio API - adjust as needed
        filter_dict = {
            "backup_id": {"$eq": backup_id}
        }
        params = {
            "filter": json.dumps(filter_dict)
        }
        endpoint = '/backups/protection-groups/s3-assets/objects'
        return self._make_request('GET', endpoint, params=params)
    
    def restore(self, restore_type: str, bucket_name: Optional[str] = None, 
               bucket_id: Optional[str] = None, object_key: Optional[str] = None) -> Dict[str, Any]:
        """
        Restore data from Clumio API
        
        Args:
            restore_type: Type of restore ('s3' or 'ec2')
            bucket_name: Optional bucket name (string)
            bucket_id: Optional bucket ID (numeric string)
            object_key: Optional S3 object key to restore
            
        Returns:
            Restore response from Clumio API
        """
        # Build request data
        data = {}
        
        if bucket_name:
            data['bucket_name'] = bucket_name
        
        if bucket_id:
            data['bucket_id'] = int(bucket_id) if bucket_id.isdigit() else bucket_id
        
        if object_key:
            data['object_key'] = object_key
        
        if restore_type == 's3':
            endpoint = '/restore/aws/s3'
        elif restore_type == 'ec2':
            endpoint = '/restore/aws/ec2'
        else:
            raise ValueError(f"Invalid restore type: {restore_type}")
        
        return self._make_request('POST', endpoint, data=data)

