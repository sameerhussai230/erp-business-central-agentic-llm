import os
import requests
import msal
import re
from dotenv import load_dotenv

load_dotenv()

class BusinessCentralAPI:
    def __init__(self):
        self.tenant_id = os.getenv("BC_TENANT_ID")
        raw_client_id = os.getenv("BC_CLIENT_ID", "")
        self.client_id = raw_client_id.strip("{}") 
        self.client_secret = os.getenv("BC_CLIENT_SECRET")
        self.environment = os.getenv("BC_ENVIRONMENT", "Production") 
        self.company_identifier = os.getenv("BC_COMPANY_ID") 
        
        self.authority = f"https://login.microsoftonline.com/{self.tenant_id}"
        self.scope = ["https://api.businesscentral.dynamics.com/.default"]

    def _get_access_token(self) -> str:
        app = msal.ConfidentialClientApplication(
            self.client_id, authority=self.authority, client_credential=self.client_secret
        )
        result = app.acquire_token_silent(self.scope, account=None)
        if not result:
            result = app.acquire_token_for_client(scopes=self.scope)
        
        if "access_token" in result:
            return result["access_token"]
        else:
            error_desc = result.get("error_description", str(result))
            raise Exception(f"MSAL Auth Error: {error_desc}")

    def _get_base_url(self) -> str:
        if hasattr(self, "_resolved_base_url"):
            return self._resolved_base_url
            
        if re.match(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$", self.company_identifier):
            self._resolved_base_url = f"https://api.businesscentral.dynamics.com/v2.0/{self.environment}/api/v2.0/companies({self.company_identifier})"
            return self._resolved_base_url
            
        token = self._get_access_token()
        headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
        companies_url = f"https://api.businesscentral.dynamics.com/v2.0/{self.environment}/api/v2.0/companies"
        
        response = requests.get(companies_url, headers=headers)
        if response.status_code != 200:
            raise Exception(f"Failed to fetch companies to resolve UUID. API Error {response.status_code}: {response.text}")
            
        companies = response.json().get("value", [])
        for comp in companies:
            if comp.get("name") == self.company_identifier or comp.get("displayName") == self.company_identifier:
                resolved_id = comp.get("id")
                self._resolved_base_url = f"https://api.businesscentral.dynamics.com/v2.0/{self.environment}/api/v2.0/companies({resolved_id})"
                return self._resolved_base_url
                
        available_companies = [c.get('name') for c in companies]
        raise Exception(f"Company '{self.company_identifier}' not found. Available companies in your environment: {available_companies}")

    def get_customers(self) -> list:
        token = self._get_access_token()
        base_url = self._get_base_url()
        headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
        
        response = requests.get(f"{base_url}/customers", headers=headers)
        if response.status_code != 200:
            raise Exception(f"BC API Error {response.status_code} at /customers: {response.text}")
            
        return response.json().get("value", [])

    def check_customer(self, customer_number: str) -> dict:
        """Validates whether a customer number exists in Business Central."""
        if not customer_number:
            return {"exists": False, "displayName": ""}
        token = self._get_access_token()
        base_url = self._get_base_url()
        headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
        
        response = requests.get(f"{base_url}/customers?$filter=number eq '{customer_number}'", headers=headers)
        if response.status_code != 200:
            raise Exception(f"BC API Error {response.status_code} checking customer: {response.text}")
            
        data = response.json().get("value", [])
        if data:
            return {"exists": True, "displayName": data[0].get("displayName", ""), "id": data[0].get("id")}
        return {"exists": False, "displayName": ""}

    def get_items(self) -> list:
        token = self._get_access_token()
        base_url = self._get_base_url()
        headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
        
        response = requests.get(f"{base_url}/items", headers=headers)
        if response.status_code != 200:
            raise Exception(f"BC API Error {response.status_code} at /items: {response.text}")
            
        return response.json().get("value", [])

    def check_inventory(self, product_code: str) -> dict:
        if not product_code:
            return {"exists": False, "inventory": 0}
        token = self._get_access_token()
        base_url = self._get_base_url()
        headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
        
        response = requests.get(f"{base_url}/items?$filter=number eq '{product_code}'", headers=headers)
        if response.status_code != 200:
            raise Exception(f"BC API Error {response.status_code} at inventory check: {response.text}")
            
        data = response.json().get("value", [])
        if data:
            return {
                "exists": True, 
                "inventory": data[0].get("inventory", 0), 
                "id": data[0].get("id")
            }
        return {"exists": False, "inventory": 0}

    def create_sales_order(self, customer_number: str, product_identifier: str, quantity: int) -> dict:
        token = self._get_access_token()
        base_url = self._get_base_url()
        headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
        
        item_guid = product_identifier
        if not re.match(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$", product_identifier):
            inv_check = self.check_inventory(product_identifier)
            if inv_check.get("exists") and inv_check.get("id"):
                item_guid = inv_check["id"]
            else:
                raise Exception(f"Could not resolve item GUID for product code: {product_identifier}")

        payload = {
            "customerNumber": customer_number,
            "salesOrderLines": [{"itemId": item_guid, "quantity": quantity}]
        }
        
        response = requests.post(f"{base_url}/salesOrders", headers=headers, json=payload)
        if response.status_code not in (200, 201):
            raise Exception(f"BC API Error {response.status_code} creating order: {response.text}")
            
        return response.json()