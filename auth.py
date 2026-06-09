#!/usr/bin/env python3
"""
Microsoft Entra ID (Azure AD) Authentication Module
Handles MSAL integration for organizational authentication
"""

import os
import uuid
import msal
import logging
from urllib.parse import urlparse

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class AuthManager:
    """Manages Microsoft Entra ID authentication using MSAL"""

    def __init__(self):
        self.client_id = os.getenv('AZURE_CLIENT_ID')
        self.tenant_id = os.getenv('AZURE_TENANT_ID')
        self.redirect_uri = os.getenv('REDIRECT_URI')
        self.client_secret = os.getenv('AZURE_CLIENT_SECRET')

        missing = []
        if not self.client_id:
            missing.append('AZURE_CLIENT_ID')
        if not self.tenant_id:
            missing.append('AZURE_TENANT_ID')
        if not self.redirect_uri:
            missing.append('REDIRECT_URI')
        if not self.client_secret:
            missing.append('AZURE_CLIENT_SECRET')
        if missing:
            raise ValueError(f"Missing required environment variables: {', '.join(missing)}")

        self.scopes = ["User.Read"]
        self.authority = f"https://login.microsoftonline.com/{self.tenant_id}"

        logger.info(f"AuthManager initialized for tenant: {self.tenant_id}")

    def build_auth_url(self, state=None):
        """Build the authorization URL for Microsoft login."""
        try:
            msal_app = msal.ConfidentialClientApplication(
                client_id=self.client_id,
                client_credential=self.client_secret,
                authority=self.authority
            )

            if state is None:
                state = str(uuid.uuid4())

            auth_url = msal_app.get_authorization_request_url(
                scopes=self.scopes,
                state=state,
                redirect_uri=self.redirect_uri
            )

            logger.info("Authorization URL generated successfully")
            return {
                'auth_url': auth_url,
                'state': state
            }

        except Exception as e:
            logger.error(f"Error building authorization URL: {str(e)}")
            raise Exception(f"Failed to build authorization URL: {str(e)}")

    def acquire_token_by_authorization_code(self, authorization_code, state):
        """Acquire access token using authorization code."""
        try:
            msal_app = msal.ConfidentialClientApplication(
                client_id=self.client_id,
                client_credential=self.client_secret,
                authority=self.authority
            )

            token_response = msal_app.acquire_token_by_authorization_code(
                code=authorization_code,
                scopes=self.scopes,
                redirect_uri=self.redirect_uri
            )

            if 'error' in token_response:
                error_msg = f"Token acquisition failed: {token_response.get('error_description', 'Unknown error')}"
                logger.error(error_msg)
                raise Exception(error_msg)

            user_id = token_response.get('id_token_claims', {}).get('oid', 'Unknown')
            user_name = token_response.get('id_token_claims', {}).get('name', 'Unknown')
            logger.info(f"Successfully authenticated user: {user_name} (ID: {user_id})")

            return token_response

        except Exception as e:
            logger.error(f"Error acquiring token: {str(e)}")
            raise Exception(f"Failed to acquire token: {str(e)}")

    def validate_group_membership(self, id_token_claims, allowed_group_ids):
        """Validate if user is a member of allowed groups."""
        try:
            user_groups = id_token_claims.get('groups', [])

            if not user_groups:
                logger.warning("No group claims found in token. Ensure group claims are enabled in Azure app registration.")
                return False

            user_allowed_groups = set(user_groups) & set(allowed_group_ids)

            if user_allowed_groups:
                logger.info(f"User authorized - member of groups: {list(user_allowed_groups)}")
                return True
            else:
                user_id = id_token_claims.get('oid', 'Unknown')
                logger.warning(f"User {user_id} not in any allowed groups. User groups: {user_groups}")
                return False

        except Exception as e:
            logger.error(f"Error validating group membership: {str(e)}")
            return False

    def get_logout_url(self):
        """Get Microsoft logout URL to properly sign out user."""
        logout_url = f"https://login.microsoftonline.com/{self.tenant_id}/oauth2/v2.0/logout"

        post_logout_uri = os.getenv('POST_LOGOUT_REDIRECT_URI', '')
        if post_logout_uri:
            logout_url += f"?post_logout_redirect_uri={post_logout_uri}"

        return logout_url


def create_auth_manager():
    """
    Factory function to create AuthManager instance.
    Returns None when both public and admin authentication are disabled.
    """
    auth_enabled = os.getenv('AUTH_ENABLED', 'False').lower() == 'true'
    admin_auth_enabled = os.getenv('ADMIN_AUTH_ENABLED', 'True').lower() == 'true'

    if not auth_enabled and not admin_auth_enabled:
        logger.info("Authentication is disabled (AUTH_ENABLED=False, ADMIN_AUTH_ENABLED=False)")
        return None

    try:
        return AuthManager()
    except ValueError as e:
        logger.error(f"Authentication configuration error: {str(e)}")
        raise e
