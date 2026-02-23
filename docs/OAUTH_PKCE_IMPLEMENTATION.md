# OAuth 2.0 with PKCE Support for MCP Server Registration

## Overview

This document describes the implementation of OAuth 2.0 with PKCE (Proof Key for Code Exchange) support for MCP (Model Context Protocol) server registration in Open WebUI. This enhancement allows MCP servers to authenticate using both OAuth 2.1 dynamic client registration and OAuth 2.0 manual configuration with optional PKCE support.

## Table of Contents

1. [Features](#features)
2. [Backend Changes](#backend-changes)
3. [Frontend Changes](#frontend-changes)
4. [Client ID Prefix Logic](#client-id-prefix-logic)
5. [OAuth Flow Diagrams](#oauth-flow-diagrams)
6. [API Endpoints](#api-endpoints)
7. [Usage Examples](#usage-examples)
8. [Testing](#testing)

## Features

- **Dual OAuth Support**: Both OAuth 2.1 dynamic registration and OAuth 2.0 manual configuration
- **PKCE Support**: Automatically enabled by Authlib when OAuth client is configured
- **Flexible Configuration**: Supports dynamic registration, manual configuration with client secret, and PKCE-only flows
- **Consistent Client ID Logic**: Intelligent prefix handling based on configuration type
- **Backward Compatible**: Existing OAuth 2.1 flows continue to work without changes
- **Secure**: Client secrets are encrypted using existing encryption infrastructure

## Backend Changes

### 1. Extended OAuth Client Registration Form

**File**: `backend/open_webui/routers/configs.py`

**Changes**:
- Extended `OAuthClientRegistrationForm` (lines 99-109) with optional fields for manual OAuth 2.0 configuration:
  - `client_secret`: OAuth client secret (optional for PKCE)
  - `authorization_endpoint`: Authorization server endpoint URL
  - `token_endpoint`: Token endpoint URL
  - `scope`: OAuth scopes (space-separated)
  - `token_endpoint_auth_method`: Authentication method for token endpoint

**Purpose**: Allows users to provide manual OAuth 2.0 configuration instead of relying solely on dynamic registration.

### 2. Updated OAuth Client Registration Endpoint

**File**: `backend/open_webui/routers/configs.py`

**Changes**:
- Modified `register_oauth_client` endpoint (lines 111-152) to:
  - Detect when manual configuration fields are provided
  - Build a `manual_config` dictionary with provided fields
  - Pass `manual_config` to the registration function when authorization_endpoint or token_endpoint are specified
  - Maintain backward compatibility with OAuth 2.1 dynamic registration

**Purpose**: Enables the API to accept and process manual OAuth 2.0 configuration.

### 3. Enhanced OAuth Client Info Registration

**File**: `backend/open_webui/utils/oauth.py`

**Changes**:
- Added `manual_config` parameter to `get_oauth_client_info_with_dynamic_client_registration` function (lines 340-422)
- When manual config is provided:
  - Bypasses dynamic client registration
  - Creates `OAuthClientInformationFull` directly from provided fields
  - Optionally fetches server metadata for validation
  - Merges custom endpoints with discovered metadata when available
  - Strips the prefix (e.g., "mcp:") from client_id for OAuth provider
  - Uses original prefixed client_id in redirect_uris for consistency

**Purpose**: Supports both dynamic registration (OAuth 2.1) and manual configuration (OAuth 2.0) flows.

### 4. Client ID Prefix Logic in Client Registration

**File**: `backend/open_webui/routers/configs.py`

**Changes**:
- Modified `set_tool_servers_config` endpoint (lines 224-232) to:
  - Check if `oauth_client_secret` exists in connection info
  - Only add "mcp:" prefix when NO client_secret is provided (OAuth 2.1 dynamic registration)
  - Use server_id as-is for manual OAuth 2.0 with client_secret

**Purpose**: Ensures consistent client_id format based on configuration type.

### 5. Client ID Prefix Logic in App Initialization

**File**: `backend/open_webui/main.py`

**Changes**:
- Modified OAuth client initialization (lines 2248-2256) to:
  - Check if `oauth_client_secret` exists in tool server connection
  - Only add "mcp:" prefix when NO client_secret is provided
  - Use server_id as-is for manual OAuth 2.0 with client_secret

**Purpose**: Ensures clients are registered with correct client_id format at startup.

### 6. Client ID Prefix Logic in Lazy Loading

**File**: `backend/open_webui/utils/oauth.py`

**Changes**:
- Modified `ensure_client_from_config` method (lines 580-652) to:
  - Check if `oauth_client_secret` exists in connection info
  - Determine the correct client_id format based on configuration type
  - Check if client is already registered before creating new registration
  - Return existing client instance when found

**Purpose**: Ensures lazy-loaded clients use consistent client_id format and prevents duplicate registrations.

### 7. Session Lookup with Correct Client ID

**File**: `backend/open_webui/routers/tools.py`

**Changes**:
- Modified `get_tools` endpoint (lines 123-137) to:
  - Decrypt `oauth_client_info` to check for `client_secret`
  - Determine `has_client_secret` flag from encrypted data
  - Use correct client_id format for session lookup (with or without prefix)
  - Include `has_client_secret` flag in tool metadata for frontend

**Purpose**: Ensures OAuth session lookup uses the correct client_id format matching how sessions were stored.

### 8. Authorization Endpoint Enhancement

**File**: `backend/open_webui/main.py`

**Changes**:
- Modified `oauth_client_authorize` endpoint (line 2357) to:
  - Call `ensure_client_from_config` as fallback when client not found in memory
  - Enable lazy loading of OAuth clients from configuration

**Purpose**: Allows authorization to proceed even if client wasn't loaded during app initialization.

## Frontend Changes

### 1. Extended OAuth Client Registration Type

**File**: `src/lib/apis/configs/index.ts`

**Changes**:
- Updated `RegisterOAuthClientForm` type (lines 205-215) with optional fields:
  - `client_secret`: OAuth client secret
  - `authorization_endpoint`: Authorization server endpoint URL
  - `token_endpoint`: Token endpoint URL
  - `scope`: OAuth scopes
  - `token_endpoint_auth_method`: Authentication method

**Purpose**: Enables TypeScript type safety for manual OAuth 2.0 configuration fields.

### 2. Authorization URL Generation with Prefix Logic

**File**: `src/lib/apis/configs/index.ts`

**Changes**:
- Modified `getOAuthClientAuthorizationUrl` function (lines 252-261) to:
  - Accept optional `hasClientSecret` parameter (default: `false`)
  - Conditionally add "mcp:" prefix only when `hasClientSecret` is `false`
  - Use client_id as-is when `hasClientSecret` is `true`

**Purpose**: Ensures authorization URLs use correct client_id format based on configuration type.

### 3. UI Form for Manual OAuth 2.0 Configuration

**File**: `src/lib/components/AddToolServerModal.svelte`

**Changes**:
- Added reactive variables (lines 59-64) for OAuth 2.0 manual configuration:
  - `oauthClientSecret`: Client secret input
  - `oauthAuthorizationEndpoint`: Authorization endpoint URL
  - `oauthTokenEndpoint`: Token endpoint URL
  - `oauthScope`: OAuth scopes
  - `oauthTokenEndpointAuthMethod`: Token endpoint auth method

- Updated `registerOAuthClientHandler` function (lines 69-122) to:
  - Build dynamic payload object
  - Conditionally include manual configuration fields when provided
  - Send all fields to backend API

- Added UI input fields (lines 768-829) for manual configuration:
  - Client Secret: Sensitive input field (password-masked)
  - Authorization Endpoint: Text input with placeholder
  - Token Endpoint: Text input with placeholder
  - Scope: Text input for space-separated scopes
  - Token Endpoint Auth Method: Dropdown with three options:
    - Client Secret POST (default)
    - Client Secret Basic
    - None (PKCE)

- Added form reset logic (lines 394-398) to clear manual configuration fields

**Purpose**: Provides user interface for entering manual OAuth 2.0 configuration.

### 4. Integration Menu with Prefix Logic

**File**: `src/lib/components/chat/MessageInput/IntegrationsMenu.svelte`

**Changes**:
- Modified tool authentication check (lines 341-342) to:
  - Extract `has_client_secret` flag from tool metadata
  - Pass flag to `getOAuthClientAuthorizationUrl` function
  - Generate correct authorization URL based on configuration type

**Purpose**: Ensures OAuth authorization links use correct client_id format when users click to authenticate.

## Client ID Prefix Logic

### Overview

The implementation uses intelligent client_id prefix logic to distinguish between OAuth 2.1 dynamic registration and OAuth 2.0 manual configuration. This ensures consistency across client registration, session storage, and session lookup.

### Rules

1. **OAuth 2.1 Dynamic Registration (NO client_secret provided)**:
   - Client registered with prefix: `"mcp:atlassian"`
   - Session stored with prefix: `"mcp:atlassian"`
   - Session lookup uses prefix: `"mcp:atlassian"`
   - Authorization URL uses prefix: `/oauth/clients/mcp:atlassian/authorize`

2. **OAuth 2.0 Manual Configuration (client_secret provided)**:
   - Client registered without prefix: `"3MVG9mLJ..."`
   - Session stored without prefix: `"3MVG9mLJ..."`
   - Session lookup uses no prefix: `"3MVG9mLJ..."`
   - Authorization URL uses no prefix: `/oauth/clients/3MVG9mLJ.../authorize`

3. **OAuth 2.0 with PKCE (NO client_secret, manual endpoints)**:
   - Client registered with prefix: `"mcp:pkce-provider"`
   - Session stored with prefix: `"mcp:pkce-provider"`
   - Session lookup uses prefix: `"mcp:pkce-provider"`
   - Authorization URL uses prefix: `/oauth/clients/mcp:pkce-provider/authorize`

### Implementation Locations

The prefix logic is consistently applied in:

1. **App Initialization** (`backend/open_webui/main.py`, lines 2248-2256)
2. **Configuration Updates** (`backend/open_webui/routers/configs.py`, lines 224-232)
3. **Lazy Loading** (`backend/open_webui/utils/oauth.py`, lines 623-626)
4. **Session Lookup** (`backend/open_webui/routers/tools.py`, lines 123-137)
5. **Frontend URL Generation** (`src/lib/apis/configs/index.ts`, lines 252-261)
6. **Integration Menu** (`src/lib/components/chat/MessageInput/IntegrationsMenu.svelte`, lines 341-342)

### Detection Method

The system determines whether to add the prefix by checking if `client_secret` exists in the encrypted `oauth_client_info`:

```python
# Backend detection
oauth_client_info_encrypted = connection.get("info", {}).get("oauth_client_info")
if oauth_client_info_encrypted:
    oauth_client_info = decrypt_data(oauth_client_info_encrypted)
    has_client_secret = bool(oauth_client_info.get("client_secret"))

# Apply prefix logic
client_id = server_id if has_client_secret else f"mcp:{server_id}"
```

```typescript
// Frontend detection
const hasClientSecret = tools[toolId]?.has_client_secret ?? false;
const oauthClientId = type && !hasClientSecret ? `${type}:${clientId}` : clientId;
```

## OAuth Flow Diagrams

### Flow 1: OAuth 2.1 Dynamic Registration (No Client Secret)

This is the original flow that continues to work without changes.

```
┌─────────────────────────────────────────────────────────────────────────┐
│ 1. USER REGISTRATION                                                    │
└─────────────────────────────────────────────────────────────────────────┘
   User fills form:
   - URL: https://mcp.atlassian.com/v1/mcp
   - Client ID: atlassian
   - Auth Type: OAuth 2.1
   - (No manual fields provided)
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────────────┐
│ 2. BACKEND REGISTRATION                                                 │
└─────────────────────────────────────────────────────────────────────────┘
   POST /api/v1/configs/oauth/clients/register?type=mcp
   {
     "url": "https://mcp.atlassian.com/v1/mcp",
     "client_id": "atlassian"
   }
                              │
                              ▼
   Backend performs dynamic client registration:
   - Fetches OAuth metadata from discovery URL
   - Registers client with OAuth provider
   - Receives client_id and client_secret from provider
   - Stores encrypted oauth_client_info
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────────────┐
│ 3. CLIENT REGISTRATION IN MEMORY                                        │
└─────────────────────────────────────────────────────────────────────────┘
   Client registered with PREFIX:
   - client_id: "mcp:atlassian"
   - Reason: NO client_secret in manual config
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────────────┐
│ 4. USER INITIATES AUTHORIZATION                                         │
└─────────────────────────────────────────────────────────────────────────┘
   User clicks "Authenticate" in UI
   Frontend generates URL with PREFIX:
   - GET /oauth/clients/mcp:atlassian/authorize
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────────────┐
│ 5. AUTHORIZATION REDIRECT                                               │
└─────────────────────────────────────────────────────────────────────────┘
   Backend:
   - Finds client with key "mcp:atlassian"
   - Generates authorization URL with PKCE parameters
   - Stores state in session with key "_state_mcp:atlassian_<state>"
   - Redirects user to OAuth provider
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────────────┐
│ 6. USER AUTHORIZES AT PROVIDER                                          │
└─────────────────────────────────────────────────────────────────────────┘
   User logs in and authorizes at OAuth provider
   Provider redirects back with code and state
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────────────┐
│ 7. CALLBACK PROCESSING                                                  │
└─────────────────────────────────────────────────────────────────────────┘
   GET /oauth/clients/mcp:atlassian/callback?code=...&state=...
   
   Backend:
   - Retrieves state from session using key "_state_mcp:atlassian_<state>"
   - Validates state parameter (CSRF protection)
   - Exchanges code for tokens using PKCE code_verifier
   - Stores session with provider="mcp:atlassian"
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────────────┐
│ 8. SESSION LOOKUP                                                       │
└─────────────────────────────────────────────────────────────────────────┘
   When user uses the tool:
   - Backend looks up session with provider="mcp:atlassian"
   - Session found ✓
   - Tool can make authenticated requests
```

### Flow 2: OAuth 2.0 Manual Configuration (With Client Secret)

This is the new flow for manual OAuth 2.0 configuration.

```
┌─────────────────────────────────────────────────────────────────────────┐
│ 1. USER REGISTRATION                                                    │
└─────────────────────────────────────────────────────────────────────────┘
   User fills form:
   - URL: https://api.salesforce.com/platform/mcp/v1-beta.2/sandbox/sobject-all
   - Client ID: 3MVG9mLJCAqQmppm329djS9WY6kp0ZBVLwWXgEeTaXjShfe_f_lHkJZI.yuuMJsIs22t2VRNv_fOKl1g8am8C
   - Auth Type: OAuth 2.1
   - Client Secret: <secret>
   - Authorization Endpoint: https://login.salesforce.com/services/oauth2/authorize
   - Token Endpoint: https://login.salesforce.com/services/oauth2/token
   - Scope: api refresh_token
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────────────┐
│ 2. BACKEND REGISTRATION                                                 │
└─────────────────────────────────────────────────────────────────────────┘
   POST /api/v1/configs/oauth/clients/register?type=mcp
   {
     "url": "https://api.salesforce.com/...",
     "client_id": "3MVG9mLJ...",
     "client_secret": "<secret>",
     "authorization_endpoint": "https://login.salesforce.com/services/oauth2/authorize",
     "token_endpoint": "https://login.salesforce.com/services/oauth2/token",
     "scope": "api refresh_token"
   }
                              │
                              ▼
   Backend creates OAuth client info from manual config:
   - Bypasses dynamic registration
   - Uses provided endpoints
   - Stores encrypted oauth_client_info with client_secret
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────────────┐
│ 3. CLIENT REGISTRATION IN MEMORY                                        │
└─────────────────────────────────────────────────────────────────────────┘
   Client registered WITHOUT PREFIX:
   - client_id: "3MVG9mLJ..."
   - Reason: client_secret EXISTS in oauth_client_info
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────────────┐
│ 4. USER INITIATES AUTHORIZATION                                         │
└─────────────────────────────────────────────────────────────────────────┘
   User clicks "Authenticate" in UI
   Frontend detects has_client_secret=true
   Frontend generates URL WITHOUT PREFIX:
   - GET /oauth/clients/3MVG9mLJ.../authorize
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────────────┐
│ 5. AUTHORIZATION REDIRECT                                               │
└─────────────────────────────────────────────────────────────────────────┘
   Backend:
   - Finds client with key "3MVG9mLJ..."
   - Generates authorization URL with PKCE parameters
   - Stores state in session with key "_state_3MVG9mLJ..._<state>"
   - Redirects user to OAuth provider
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────────────┐
│ 6. USER AUTHORIZES AT PROVIDER                                          │
└─────────────────────────────────────────────────────────────────────────┘
   User logs in and authorizes at OAuth provider
   Provider redirects back with code and state
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────────────┐
│ 7. CALLBACK PROCESSING                                                  │
└─────────────────────────────────────────────────────────────────────────┘
   GET /oauth/clients/3MVG9mLJ.../callback?code=...&state=...
   
   Backend:
   - Retrieves state from session using key "_state_3MVG9mLJ..._<state>"
   - Validates state parameter (CSRF protection)
   - Exchanges code for tokens using PKCE code_verifier
   - Stores session with provider="3MVG9mLJ..."
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────────────┐
│ 8. SESSION LOOKUP                                                       │
└─────────────────────────────────────────────────────────────────────────┘
   When user uses the tool:
   - Backend decrypts oauth_client_info
   - Detects client_secret exists
   - Looks up session with provider="3MVG9mLJ..." (no prefix)
   - Session found ✓
   - Tool can make authenticated requests
```

### Flow 3: OAuth 2.0 with PKCE Only (No Client Secret, Manual Endpoints)

This flow is for OAuth providers that support PKCE without requiring a client secret.

```
┌─────────────────────────────────────────────────────────────────────────┐
│ 1. USER REGISTRATION                                                    │
└─────────────────────────────────────────────────────────────────────────┘
   User fills form:
   - URL: https://oauth.example.com/mcp
   - Client ID: pkce-client
   - Auth Type: OAuth 2.1
   - Authorization Endpoint: https://oauth.example.com/authorize
   - Token Endpoint: https://oauth.example.com/token
   - Token Endpoint Auth Method: None (PKCE)
   - (No client_secret provided)
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────────────┐
│ 2. BACKEND REGISTRATION                                                 │
└─────────────────────────────────────────────────────────────────────────┘
   POST /api/v1/configs/oauth/clients/register?type=mcp
   {
     "url": "https://oauth.example.com/mcp",
     "client_id": "pkce-client",
     "authorization_endpoint": "https://oauth.example.com/authorize",
     "token_endpoint": "https://oauth.example.com/token",
     "token_endpoint_auth_method": "none"
   }
                              │
                              ▼
   Backend creates OAuth client info from manual config:
   - Bypasses dynamic registration
   - Uses provided endpoints
   - Stores encrypted oauth_client_info WITHOUT client_secret
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────────────┐
│ 3. CLIENT REGISTRATION IN MEMORY                                        │
└─────────────────────────────────────────────────────────────────────────┘
   Client registered WITH PREFIX:
   - client_id: "mcp:pkce-client"
   - Reason: NO client_secret in oauth_client_info
                              │
                              ▼
   [Flow continues same as Flow 1 with PKCE parameters]
   - Authorization uses "mcp:pkce-client"
   - Callback uses "mcp:pkce-client"
   - Session stored with provider="mcp:pkce-client"
   - Session lookup uses "mcp:pkce-client"
```

### Key Differences Summary

| Aspect | OAuth 2.1 Dynamic | OAuth 2.0 Manual (Secret) | OAuth 2.0 PKCE Only |
|--------|-------------------|---------------------------|---------------------|
| Client Secret | Provided by OAuth server | Provided by user | Not provided |
| Endpoints | Discovered automatically | Provided by user | Provided by user |
| Client ID Format | `mcp:atlassian` | `3MVG9mLJ...` | `mcp:pkce-client` |
| Prefix Used | Yes | No | Yes |
| PKCE | Automatic | Automatic | Automatic |
| State Key | `_state_mcp:atlassian_<state>` | `_state_3MVG9mLJ..._<state>` | `_state_mcp:pkce-client_<state>` |

## API Endpoints

### 1. Register OAuth Client

**Endpoint**: `POST /api/v1/configs/oauth/clients/register`

**Query Parameters**:
- `type` (optional): Client type (e.g., `mcp`)

**Request Body**:
```json
{
  "url": "string (required)",
  "client_id": "string (required)",
  "client_name": "string (optional)",
  "client_secret": "string (optional)",
  "authorization_endpoint": "string (optional)",
  "token_endpoint": "string (optional)",
  "scope": "string (optional)",
  "token_endpoint_auth_method": "string (optional)"
}
```

**Response**:
```json
{
  "status": true,
  "oauth_client_info": "encrypted_string"
}
```

### 2. Authorize OAuth Client

**Endpoint**: `GET /oauth/clients/{client_id}/authorize`

**Path Parameters**:
- `client_id`: The OAuth client ID (with or without prefix based on configuration)

**Response**: Redirects to OAuth provider's authorization page

### 3. OAuth Callback

**Endpoint**: `GET /oauth/clients/{client_id}/callback`

**Path Parameters**:
- `client_id`: The OAuth client ID (with or without prefix based on configuration)

**Query Parameters**:
- `code`: Authorization code from OAuth provider
- `state`: State parameter for CSRF protection

**Response**: Redirects to application home page with success or error message

## Usage Examples

### Example 1: OAuth 2.1 Dynamic Registration (Atlassian)

**Step 1: Register Client**

```bash
curl -X POST 'http://localhost:8080/api/v1/configs/oauth/clients/register?type=mcp' \
  -H 'Authorization: Bearer <token>' \
  -H 'Content-Type: application/json' \
  -d '{
    "url": "https://mcp.atlassian.com/v1/mcp",
    "client_id": "atlassian"
  }'
```

**Step 2: Configure Tool Server**

Add the tool server connection in the admin panel with:
- URL: `https://mcp.atlassian.com/v1/mcp`
- Type: MCP
- Auth Type: OAuth 2.1
- Paste the encrypted `oauth_client_info` from Step 1

**Step 3: Authorize**

Navigate to: `http://localhost:8080/oauth/clients/mcp:atlassian/authorize`

The system will:
- Redirect to Atlassian's OAuth page
- User authorizes the application
- Redirect back to callback URL
- Store OAuth session
- Tool is now authenticated and ready to use

### Example 2: OAuth 2.0 Manual Configuration (Salesforce)

**Step 1: Register Client with Manual Config**

```bash
curl -X POST 'http://localhost:8080/api/v1/configs/oauth/clients/register?type=mcp' \
  -H 'Authorization: Bearer <token>' \
  -H 'Content-Type: application/json' \
  -d '{
    "url": "https://api.salesforce.com/platform/mcp/v1-beta.2/sandbox/sobject-all",
    "client_id": "3MVG9mLJCAqQmppm329djS9WY6kp0ZBVLwWXgEeTaXjShfe_f_lHkJZI.yuuMJsIs22t2VRNv_fOKl1g8am8C",
    "client_secret": "YOUR_CLIENT_SECRET",
    "authorization_endpoint": "https://login.salesforce.com/services/oauth2/authorize",
    "token_endpoint": "https://login.salesforce.com/services/oauth2/token",
    "scope": "api refresh_token",
    "token_endpoint_auth_method": "client_secret_post"
  }'
```

**Step 2: Configure Tool Server**

Add the tool server connection in the admin panel with:
- URL: `https://api.salesforce.com/platform/mcp/v1-beta.2/sandbox/sobject-all`
- Type: MCP
- Auth Type: OAuth 2.1
- Fill in the manual OAuth fields:
  - Client Secret: `YOUR_CLIENT_SECRET`
  - Authorization Endpoint: `https://login.salesforce.com/services/oauth2/authorize`
  - Token Endpoint: `https://login.salesforce.com/services/oauth2/token`
  - Scope: `api refresh_token`
- Paste the encrypted `oauth_client_info` from Step 1

**Step 3: Authorize**

Navigate to: `http://localhost:8080/oauth/clients/3MVG9mLJCAqQmppm329djS9WY6kp0ZBVLwWXgEeTaXjShfe_f_lHkJZI.yuuMJsIs22t2VRNv_fOKl1g8am8C/authorize`

Note: No "mcp:" prefix in the URL because client_secret was provided.

### Example 3: OAuth 2.0 with PKCE Only

**Step 1: Register Client with PKCE**

```bash
curl -X POST 'http://localhost:8080/api/v1/configs/oauth/clients/register?type=mcp' \
  -H 'Authorization: Bearer <token>' \
  -H 'Content-Type: application/json' \
  -d '{
    "url": "https://oauth.example.com/mcp",
    "client_id": "pkce-client",
    "authorization_endpoint": "https://oauth.example.com/authorize",
    "token_endpoint": "https://oauth.example.com/token",
    "scope": "read write",
    "token_endpoint_auth_method": "none"
  }'
```

**Step 2: Configure Tool Server**

Add the tool server connection in the admin panel with:
- URL: `https://oauth.example.com/mcp`
- Type: MCP
- Auth Type: OAuth 2.1
- Fill in the manual OAuth fields:
  - Authorization Endpoint: `https://oauth.example.com/authorize`
  - Token Endpoint: `https://oauth.example.com/token`
  - Scope: `read write`
  - Token Endpoint Auth Method: None (PKCE)
- Paste the encrypted `oauth_client_info` from Step 1

**Step 3: Authorize**

Navigate to: `http://localhost:8080/oauth/clients/mcp:pkce-client/authorize`

Note: "mcp:" prefix is used because no client_secret was provided.

## Testing

### Backend Testing

**1. Test Imports**

```bash
cd backend
export PYTHONPATH=$PYTHONPATH:.
python3 -c "from open_webui.routers.configs import OAuthClientRegistrationForm; \
            from open_webui.utils.oauth import get_oauth_client_info_with_dynamic_client_registration; \
            print('✓ All imports successful')"
```

**2. Test OAuth Client Registration**

```bash
cd backend
export PYTHONPATH=$PYTHONPATH:.
python3 -c "from open_webui.utils.oauth import OAuthClientManager; \
            print('✓ OAuth client manager imports successfully')"
```

### Frontend Testing

**1. Test TypeScript Compilation**

```bash
npm run check
```

**2. Test in Browser**

1. Start the application
2. Navigate to Admin Panel → Connections
3. Add a new MCP tool server with OAuth 2.1
4. Fill in manual OAuth fields (optional)
5. Click "Register Client"
6. Verify registration status shows "Registered"
7. Save the connection
8. Navigate to chat and enable the tool
9. Click to authenticate
10. Verify redirect to OAuth provider
11. Authorize and verify redirect back
12. Verify tool shows as authenticated

### Integration Testing

**Test OAuth 2.1 Dynamic Registration**:
1. Register Atlassian MCP server without manual fields
2. Verify client registered with prefix `mcp:atlassian`
3. Authorize and verify session stored with prefix
4. Use tool and verify session lookup succeeds

**Test OAuth 2.0 Manual Configuration**:
1. Register Salesforce MCP server with client_secret
2. Verify client registered without prefix
3. Authorize and verify session stored without prefix
4. Use tool and verify session lookup succeeds

**Test OAuth 2.0 PKCE Only**:
1. Register MCP server with endpoints but no client_secret
2. Verify client registered with prefix
3. Authorize and verify PKCE parameters used
4. Use tool and verify session lookup succeeds

## Troubleshooting

### Issue: "No OAuth session found"

**Symptom**: Error message in logs: `No OAuth session found for user <user_id>, client_id <client_id>`

**Cause**: Mismatch between client_id format used during session storage and session lookup.

**Solution**: Verify that:
1. If `client_secret` was provided during registration, the client_id should NOT have "mcp:" prefix
2. If `client_secret` was NOT provided, the client_id SHOULD have "mcp:" prefix
3. Check the `has_client_secret` flag in tool metadata
4. Verify the authorization URL uses the correct client_id format

### Issue: "mismatching_state: CSRF Warning"

**Symptom**: OAuth callback fails with state mismatch error.

**Cause**: State parameter stored with different client_id format than used in callback.

**Solution**: Ensure the client_id format is consistent:
1. Check if client is registered with correct prefix/no-prefix
2. Verify authorization URL uses same client_id format
3. Verify callback URL uses same client_id format
4. Clear browser cookies and try again

### Issue: 404 on Authorization Endpoint

**Symptom**: `GET /oauth/clients/<client_id>/authorize` returns 404.

**Cause**: Client not registered in memory or wrong client_id format.

**Solution**:
1. Verify the tool server connection is saved in admin panel
2. Restart the application to reload OAuth clients
3. Check if client_id format matches registration (with/without prefix)
4. Verify `oauth_client_info` is present in connection configuration

## Security Considerations

1. **Client Secret Encryption**: All client secrets are encrypted using Fernet encryption before storage
2. **PKCE Protection**: PKCE (code_challenge and code_verifier) is automatically used for all OAuth flows
3. **State Parameter**: CSRF protection via state parameter is enforced for all authorization flows
4. **Session Storage**: OAuth tokens are encrypted in the database
5. **Token Refresh**: Automatic token refresh with 5-minute buffer before expiration

## Backward Compatibility

All changes are backward compatible:
- Existing OAuth 2.1 dynamic registration continues to work without modifications
- No changes required to existing tool server configurations
- Frontend automatically detects configuration type and adjusts behavior
- Session lookup logic handles both prefixed and non-prefixed client_ids

## Future Enhancements

Potential future improvements:
1. Support for additional OAuth grant types (client credentials, device code)
2. UI improvements for OAuth configuration validation
3. OAuth provider templates for common services
4. Bulk OAuth client registration
5. OAuth session management UI for users

