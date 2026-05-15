import logging
import time
import uuid
import secrets
import hashlib
import base64

from typing import Optional
from urllib.parse import urlencode

import aiohttp
from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import RedirectResponse, JSONResponse

from open_webui.models.oauth_sessions import OAuthSessions
from open_webui.utils.auth import get_verified_user
from open_webui.env import AIOHTTP_CLIENT_SESSION_SSL

log = logging.getLogger(__name__)

router = APIRouter()

MICROSOFT_PROVIDER = 'microsoft_teams_integration'
SLACK_PROVIDER = 'slack_integration'
WORK_IQ_PROVIDER = 'workiq_integration'
WORK_IQ_SCOPE = 'api://workiq.svc.cloud.microsoft/WorkIQAgent.Ask offline_access'

# Microsoft Graph API scopes for Teams, Outlook, Calendar, OneDrive, SharePoint
MICROSOFT_SCOPES = [
    # Basic access
    'offline_access',
    'openid',
    'email',
    'profile',
    'User.Read',
    # User directory
    'User.ReadBasic.All',
    # Email (Outlook)
    'Mail.Read',
    'Mail.ReadBasic',
    'Mail.Read.Shared',
    'MailboxFolder.Read',
    'MailboxItem.Read',
    'Mail.Send',
    # Calendar
    'Calendars.Read',
    'Calendars.Read.Shared',
    'Calendars.ReadWrite',
    # Teams Chat
    'Chat.Read',
    'Chat.ReadBasic',
    'Chat.ReadWrite',
    'ChatMember.Read',
    'ChatMessage.Read',
    # Teams Channels
    'Channel.ReadBasic.All',
    'ChannelMessage.Read.All',
    'ChannelMessage.Send',
    # Teams membership
    'Team.ReadBasic.All',
    # Meetings
    'OnlineMeetings.Read',
    'OnlineMeetingTranscript.Read.All',
    'OnlineMeetingAiInsight.Read',
    'OnlineMeetingArtifact.Read.All',
    'OnlineMeetingRecording.Read.All',
    # Files (OneDrive and SharePoint)
    'Files.Read',
    'Files.Read.All',
    'Files.ReadWrite',
    'Sites.Read.All',
]

# Slack OAuth scopes
SLACK_SCOPES = [
    'channels:read',
    'channels:history',
    'chat:write',
    'users:read',
    'search:read',
    'im:write',
    'im:read',
    'im:history',
]


def _get_redirect_base(request: Request) -> str:
    """Derive the public base URL from the incoming request."""
    forwarded_proto = request.headers.get('x-forwarded-proto')
    scheme = forwarded_proto if forwarded_proto else request.url.scheme
    host = request.headers.get('x-forwarded-host') or request.headers.get('host') or request.url.netloc
    return f'{scheme}://{host}'


############################
# GET /status
############################


@router.get('/status')
async def get_integration_status(request: Request, user=Depends(get_verified_user)):
    """Return which integrations the current user has connected."""
    microsoft_enabled = request.app.state.config.ENABLE_MICROSOFT_TEAMS_INTEGRATION
    slack_enabled = request.app.state.config.ENABLE_SLACK_INTEGRATION
    work_iq_enabled = getattr(request.app.state.config, 'ENABLE_WORK_IQ_INTEGRATION', False)

    microsoft_session = None
    slack_session = None
    work_iq_session = None

    if microsoft_enabled:
        microsoft_session = await OAuthSessions.get_session_by_provider_and_user_id(
            MICROSOFT_PROVIDER, user.id
        )

    if slack_enabled:
        slack_session = await OAuthSessions.get_session_by_provider_and_user_id(
            SLACK_PROVIDER, user.id
        )

    if work_iq_enabled:
        work_iq_session = await OAuthSessions.get_session_by_provider_and_user_id(
            WORK_IQ_PROVIDER, user.id
        )

    return {
        'microsoft': {
            'enabled': microsoft_enabled,
            'connected': microsoft_session is not None,
            'account': microsoft_session.token.get('account') if microsoft_session else None,
            'expires_at': microsoft_session.expires_at if microsoft_session else None,
        },
        'slack': {
            'enabled': slack_enabled,
            'connected': slack_session is not None,
            'workspace': slack_session.token.get('team', {}).get('name') if slack_session else None,
            'expires_at': slack_session.expires_at if slack_session else None,
        },
        'work_iq': {
            'enabled': work_iq_enabled,
            'connected': work_iq_session is not None,
            'expires_at': work_iq_session.expires_at if work_iq_session else None,
        },
    }


############################
# Microsoft Integration
############################


@router.get('/microsoft/connect')
async def microsoft_connect(request: Request, user=Depends(get_verified_user)):
    """Start Microsoft OAuth2 flow."""
    config = request.app.state.config
    if not config.ENABLE_MICROSOFT_TEAMS_INTEGRATION:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail='Microsoft Teams integration is not enabled')

    client_id = config.MICROSOFT_TEAMS_INTEGRATION_CLIENT_ID
    tenant_id = config.MICROSOFT_TEAMS_INTEGRATION_TENANT_ID or 'common'

    if not client_id:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail='Microsoft integration not configured')

    state = _encode_state({'user_id': user.id, 'nonce': secrets.token_urlsafe(16)})
    redirect_uri = f'{_get_redirect_base(request)}/api/v1/integrations/microsoft/callback'

    params = {
        'client_id': client_id,
        'response_type': 'code',
        'redirect_uri': redirect_uri,
        'scope': ' '.join(MICROSOFT_SCOPES),
        'state': state,
        'response_mode': 'query',
        'prompt': 'select_account',
    }

    auth_url = f'https://login.microsoftonline.com/{tenant_id}/oauth2/v2.0/authorize?{urlencode(params)}'
    return RedirectResponse(auth_url)


@router.get('/microsoft/callback')
async def microsoft_callback(request: Request, code: Optional[str] = None, state: Optional[str] = None, error: Optional[str] = None):
    """Handle Microsoft OAuth2 callback."""
    if error:
        log.warning(f'Microsoft OAuth error: {error}')
        return RedirectResponse('/integrations/callback?provider=microsoft&status=error')

    if not code or not state:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail='Missing code or state')

    state_data = _decode_state(state)
    if not state_data or 'user_id' not in state_data:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail='Invalid state')

    user_id = state_data['user_id']
    config = request.app.state.config
    client_id = config.MICROSOFT_TEAMS_INTEGRATION_CLIENT_ID
    client_secret = config.MICROSOFT_TEAMS_INTEGRATION_CLIENT_SECRET
    tenant_id = config.MICROSOFT_TEAMS_INTEGRATION_TENANT_ID or 'common'
    redirect_uri = f'{_get_redirect_base(request)}/api/v1/integrations/microsoft/callback'

    token_url = f'https://login.microsoftonline.com/{tenant_id}/oauth2/v2.0/token'

    async with aiohttp.ClientSession() as session:
        # Exchange code for tokens
        async with session.post(
            token_url,
            data={
                'client_id': client_id,
                'client_secret': client_secret,
                'grant_type': 'authorization_code',
                'code': code,
                'redirect_uri': redirect_uri,
                'scope': ' '.join(MICROSOFT_SCOPES),
            },
            ssl=AIOHTTP_CLIENT_SESSION_SSL,
        ) as resp:
            if resp.status != 200:
                body = await resp.text()
                log.error(f'Microsoft token exchange failed: {body}')
                return RedirectResponse('/?integration=microsoft&status=error')
            token_data = await resp.json()

        # Fetch user profile to store account info
        access_token = token_data.get('access_token', '')
        account_info = {}
        if access_token:
            async with session.get(
                'https://graph.microsoft.com/v1.0/me',
                headers={'Authorization': f'Bearer {access_token}'},
                ssl=AIOHTTP_CLIENT_SESSION_SSL,
            ) as profile_resp:
                if profile_resp.status == 200:
                    profile = await profile_resp.json()
                    account_info = {
                        'name': profile.get('displayName', ''),
                        'email': profile.get('mail') or profile.get('userPrincipalName', ''),
                        'id': profile.get('id', ''),
                    }

    token_to_store = {
        'access_token': token_data.get('access_token', ''),
        'refresh_token': token_data.get('refresh_token', ''),
        'token_type': token_data.get('token_type', 'Bearer'),
        'scope': token_data.get('scope', ''),
        'expires_at': int(time.time()) + int(token_data.get('expires_in', 3600)),
        'account': account_info,
    }

    # Remove old session if any, then create new one
    await OAuthSessions.delete_sessions_by_user_id_and_provider(user_id, MICROSOFT_PROVIDER)
    await OAuthSessions.create_session(user_id, MICROSOFT_PROVIDER, token_to_store)

    # If Work IQ is enabled, also exchange the refresh token for a Work IQ token
    if getattr(config, 'ENABLE_WORK_IQ_INTEGRATION', False):
        wiq_token = await _fetch_work_iq_token(config, token_data.get('refresh_token', ''))
        if wiq_token:
            await OAuthSessions.delete_sessions_by_user_id_and_provider(user_id, WORK_IQ_PROVIDER)
            await OAuthSessions.create_session(user_id, WORK_IQ_PROVIDER, wiq_token)
        else:
            log.info('Work IQ token exchange skipped — WorkIQAgent.Ask permission may not be granted in Entra')

    return RedirectResponse('/integrations/callback?provider=microsoft&status=success')


@router.delete('/microsoft/disconnect')
async def microsoft_disconnect(request: Request, user=Depends(get_verified_user)):
    """Revoke Microsoft integration for the current user."""
    session = await OAuthSessions.get_session_by_provider_and_user_id(MICROSOFT_PROVIDER, user.id)
    if session:
        # Attempt token revocation (best-effort)
        refresh_token = session.token.get('refresh_token')
        if refresh_token:
            config = request.app.state.config
            try:
                async with aiohttp.ClientSession() as http:
                    tenant_id = config.MICROSOFT_TEAMS_INTEGRATION_TENANT_ID or 'common'
                    await http.post(
                        f'https://login.microsoftonline.com/{tenant_id}/oauth2/v2.0/token',
                        data={
                            'client_id': config.MICROSOFT_TEAMS_INTEGRATION_CLIENT_ID,
                            'client_secret': config.MICROSOFT_TEAMS_INTEGRATION_CLIENT_SECRET,
                            'grant_type': 'refresh_token',
                            'refresh_token': refresh_token,
                            'scope': 'offline_access',
                            'revoke_all': 'true',
                        },
                        ssl=AIOHTTP_CLIENT_SESSION_SSL,
                    )
            except Exception as e:
                log.warning(f'Microsoft token revocation failed (continuing disconnect): {e}')

        await OAuthSessions.delete_sessions_by_user_id_and_provider(user.id, MICROSOFT_PROVIDER)

    # Also clear Work IQ token (it shares the same Microsoft identity)
    await OAuthSessions.delete_sessions_by_user_id_and_provider(user.id, WORK_IQ_PROVIDER)

    return {'status': 'ok', 'message': 'Microsoft integration disconnected'}


############################
# Microsoft Token Refresh (internal helper)
############################


async def refresh_microsoft_token(config, session) -> Optional[dict]:
    """Refresh an expired Microsoft access token. Returns updated token dict or None."""
    refresh_token = session.token.get('refresh_token')
    if not refresh_token:
        return None

    tenant_id = config.MICROSOFT_TEAMS_INTEGRATION_TENANT_ID or 'common'
    token_url = f'https://login.microsoftonline.com/{tenant_id}/oauth2/v2.0/token'

    try:
        async with aiohttp.ClientSession() as http:
            async with http.post(
                token_url,
                data={
                    'client_id': config.MICROSOFT_TEAMS_INTEGRATION_CLIENT_ID,
                    'client_secret': config.MICROSOFT_TEAMS_INTEGRATION_CLIENT_SECRET,
                    'grant_type': 'refresh_token',
                    'refresh_token': refresh_token,
                    'scope': ' '.join(MICROSOFT_SCOPES),
                },
                ssl=AIOHTTP_CLIENT_SESSION_SSL,
            ) as resp:
                if resp.status != 200:
                    return None
                new_token_data = await resp.json()

        updated = {
            **session.token,
            'access_token': new_token_data.get('access_token', ''),
            'refresh_token': new_token_data.get('refresh_token', refresh_token),
            'expires_at': int(time.time()) + int(new_token_data.get('expires_in', 3600)),
        }
        await OAuthSessions.update_session_by_id(session.id, updated)
        return updated
    except Exception as e:
        log.error(f'Error refreshing Microsoft token: {e}')
        return None


############################
# Work IQ Token Helpers (internal)
############################


async def _fetch_work_iq_token(config, refresh_token: str) -> Optional[dict]:
    """Exchange a Microsoft refresh token for a Work IQ access token. Returns token dict or None."""
    if not refresh_token:
        return None

    tenant_id = config.MICROSOFT_TEAMS_INTEGRATION_TENANT_ID or 'common'
    token_url = f'https://login.microsoftonline.com/{tenant_id}/oauth2/v2.0/token'

    try:
        async with aiohttp.ClientSession() as http:
            async with http.post(
                token_url,
                data={
                    'client_id': config.MICROSOFT_TEAMS_INTEGRATION_CLIENT_ID,
                    'client_secret': config.MICROSOFT_TEAMS_INTEGRATION_CLIENT_SECRET,
                    'grant_type': 'refresh_token',
                    'refresh_token': refresh_token,
                    'scope': WORK_IQ_SCOPE,
                },
                ssl=AIOHTTP_CLIENT_SESSION_SSL,
            ) as resp:
                if resp.status != 200:
                    body = await resp.text()
                    log.debug(f'Work IQ token exchange failed ({resp.status}): {body[:300]}')
                    return None
                data = await resp.json()

        return {
            'access_token': data.get('access_token', ''),
            'refresh_token': data.get('refresh_token', refresh_token),
            'token_type': 'Bearer',
            'expires_at': int(time.time()) + int(data.get('expires_in', 3600)),
        }
    except Exception as e:
        log.debug(f'Work IQ token exchange error: {e}')
        return None


async def refresh_work_iq_token(config, session) -> Optional[dict]:
    """Refresh an expired Work IQ access token. Returns updated token dict or None."""
    refresh_token = session.token.get('refresh_token')
    if not refresh_token:
        return None

    updated = await _fetch_work_iq_token(config, refresh_token)
    if updated:
        await OAuthSessions.update_session_by_id(session.id, updated)
    return updated


############################
# Slack Integration
############################


@router.get('/slack/connect')
async def slack_connect(request: Request, user=Depends(get_verified_user)):
    """Start Slack OAuth2 flow."""
    config = request.app.state.config
    if not config.ENABLE_SLACK_INTEGRATION:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail='Slack integration is not enabled')

    client_id = config.SLACK_INTEGRATION_CLIENT_ID
    if not client_id:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail='Slack integration not configured')

    state = _encode_state({'user_id': user.id, 'nonce': secrets.token_urlsafe(16)})
    redirect_uri = f'{_get_redirect_base(request)}/api/v1/integrations/slack/callback'

    params = {
        'client_id': client_id,
        'scope': ','.join(SLACK_SCOPES),
        'redirect_uri': redirect_uri,
        'state': state,
    }

    auth_url = f'https://slack.com/oauth/v2/authorize?{urlencode(params)}'
    return RedirectResponse(auth_url)


@router.get('/slack/callback')
async def slack_callback(request: Request, code: Optional[str] = None, state: Optional[str] = None, error: Optional[str] = None):
    """Handle Slack OAuth2 callback."""
    if error:
        log.warning(f'Slack OAuth error: {error}')
        return RedirectResponse('/integrations/callback?provider=slack&status=error')

    if not code or not state:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail='Missing code or state')

    state_data = _decode_state(state)
    if not state_data or 'user_id' not in state_data:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail='Invalid state')

    user_id = state_data['user_id']
    config = request.app.state.config
    client_id = config.SLACK_INTEGRATION_CLIENT_ID
    client_secret = config.SLACK_INTEGRATION_CLIENT_SECRET
    redirect_uri = f'{_get_redirect_base(request)}/api/v1/integrations/slack/callback'

    async with aiohttp.ClientSession() as session:
        async with session.post(
            'https://slack.com/api/oauth.v2.access',
            data={
                'client_id': client_id,
                'client_secret': client_secret,
                'code': code,
                'redirect_uri': redirect_uri,
            },
            ssl=AIOHTTP_CLIENT_SESSION_SSL,
        ) as resp:
            token_data = await resp.json()

    if not token_data.get('ok'):
        log.error(f'Slack token exchange failed: {token_data.get("error")}')
        return RedirectResponse('/?integration=slack&status=error')

    token_to_store = {
        'access_token': token_data.get('access_token', ''),
        'token_type': 'Bearer',
        'scope': token_data.get('scope', ''),
        'team': token_data.get('team', {}),
        'authed_user': token_data.get('authed_user', {}),
        'bot_user_id': token_data.get('bot_user_id', ''),
        'app_id': token_data.get('app_id', ''),
        # Slack tokens don't expire by default, use far future
        'expires_at': int(time.time()) + (365 * 24 * 3600),
    }

    await OAuthSessions.delete_sessions_by_user_id_and_provider(user_id, SLACK_PROVIDER)
    await OAuthSessions.create_session(user_id, SLACK_PROVIDER, token_to_store)

    return RedirectResponse('/integrations/callback?provider=slack&status=success')


@router.delete('/slack/disconnect')
async def slack_disconnect(request: Request, user=Depends(get_verified_user)):
    """Revoke Slack integration for the current user."""
    session = await OAuthSessions.get_session_by_provider_and_user_id(SLACK_PROVIDER, user.id)
    if session:
        access_token = session.token.get('access_token')
        if access_token:
            try:
                async with aiohttp.ClientSession() as http:
                    await http.post(
                        'https://slack.com/api/auth.revoke',
                        data={'token': access_token},
                        ssl=AIOHTTP_CLIENT_SESSION_SSL,
                    )
            except Exception as e:
                log.warning(f'Slack token revocation failed (continuing disconnect): {e}')

        await OAuthSessions.delete_sessions_by_user_id_and_provider(user.id, SLACK_PROVIDER)

    return {'status': 'ok', 'message': 'Slack integration disconnected'}


############################
# State helpers
############################


def _encode_state(data: dict) -> str:
    import json
    raw = json.dumps(data)
    return base64.urlsafe_b64encode(raw.encode()).decode()


def _decode_state(state: str) -> Optional[dict]:
    import json
    try:
        raw = base64.urlsafe_b64decode(state.encode() + b'==').decode()
        return json.loads(raw)
    except Exception:
        return None
