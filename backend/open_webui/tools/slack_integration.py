"""
Slack API tools for Open WebUI.

These are builtin tools that become available automatically when:
  1. Admin has enabled ENABLE_SLACK_INTEGRATION
  2. The current user has connected their Slack workspace via Settings → Integrations

Functions receive __request__ and __user__ injected at runtime; they are
never exposed to the LLM as required parameters.
"""

import json
import logging
from typing import Optional

import aiohttp
from fastapi import Request

from open_webui.env import AIOHTTP_CLIENT_SESSION_SSL

log = logging.getLogger(__name__)

SLACK_PROVIDER = 'slack_integration'
SLACK_API_BASE = 'https://slack.com/api'


# ─── Token helper ─────────────────────────────────────────────────────────────


async def _get_slack_token(user_id: str) -> Optional[str]:
    from open_webui.models.oauth_sessions import OAuthSessions

    session = await OAuthSessions.get_session_by_provider_and_user_id(SLACK_PROVIDER, user_id)
    if not session:
        return None
    return session.token.get('access_token')


async def _slack_get(token: str, method: str, params: dict = None) -> dict:
    url = f'{SLACK_API_BASE}/{method}'
    headers = {'Authorization': f'Bearer {token}'}
    async with aiohttp.ClientSession() as session:
        async with session.get(url, headers=headers, params=params or {}, ssl=AIOHTTP_CLIENT_SESSION_SSL) as resp:
            return await resp.json()


async def _slack_post(token: str, method: str, payload: dict) -> dict:
    url = f'{SLACK_API_BASE}/{method}'
    headers = {'Authorization': f'Bearer {token}', 'Content-Type': 'application/json'}
    async with aiohttp.ClientSession() as session:
        async with session.post(url, headers=headers, json=payload, ssl=AIOHTTP_CLIENT_SESSION_SSL) as resp:
            return await resp.json()


# ─── Slack tools ──────────────────────────────────────────────────────────────


async def list_slack_channels(
    types: str = 'public_channel,private_channel',
    count: int = 20,
    __request__: Request = None,
    __user__: dict = None,
) -> str:
    """
    List Slack channels in the connected workspace.

    :param types: Comma-separated channel types to include: public_channel, private_channel, mpim, im (default: public_channel,private_channel)
    :param count: Maximum number of channels to return (default: 20, max: 100)
    :return: JSON list of channels with id, name, topic, and member count
    """
    if not __request__ or not __user__:
        return json.dumps({'error': 'Request context not available'})

    token = await _get_slack_token(__user__.get('id', ''))
    if not token:
        return json.dumps({'error': 'Slack not connected. Please connect via Settings → Integrations.'})

    count = min(max(1, int(count)), 100)

    try:
        data = await _slack_get(token, 'conversations.list', {
            'types': types,
            'limit': count,
            'exclude_archived': 'true',
        })

        if not data.get('ok'):
            return json.dumps({'error': f'Slack API error: {data.get("error", "unknown")}'})

        channels = [
            {
                'id': c.get('id', ''),
                'name': c.get('name', ''),
                'topic': c.get('topic', {}).get('value', ''),
                'members': c.get('num_members', 0),
                'is_private': c.get('is_private', False),
            }
            for c in data.get('channels', [])
        ]
        return json.dumps({'channels': channels, 'count': len(channels)})

    except Exception as e:
        log.error(f'list_slack_channels error: {e}')
        return json.dumps({'error': str(e)})


async def get_slack_channel_history(
    channel_id: str,
    count: int = 15,
    __request__: Request = None,
    __user__: dict = None,
) -> str:
    """
    Retrieve recent messages from a Slack channel.

    :param channel_id: The Slack channel ID (e.g. C12345678, from list_slack_channels)
    :param count: Number of recent messages to retrieve (default: 15, max: 50)
    :return: JSON list of messages with user, text, and timestamp
    """
    if not __request__ or not __user__:
        return json.dumps({'error': 'Request context not available'})

    token = await _get_slack_token(__user__.get('id', ''))
    if not token:
        return json.dumps({'error': 'Slack not connected.'})

    count = min(max(1, int(count)), 50)

    try:
        data = await _slack_get(token, 'conversations.history', {
            'channel': channel_id,
            'limit': count,
        })

        if not data.get('ok'):
            return json.dumps({'error': f'Slack API error: {data.get("error", "unknown")}'})

        messages = []
        for m in data.get('messages', []):
            if m.get('type') == 'message' and not m.get('subtype'):
                messages.append({
                    'user': m.get('user', m.get('username', 'unknown')),
                    'text': m.get('text', ''),
                    'timestamp': m.get('ts', ''),
                    'thread_ts': m.get('thread_ts'),
                })

        return json.dumps({'messages': messages, 'channel_id': channel_id, 'count': len(messages)})

    except Exception as e:
        log.error(f'get_slack_channel_history error: {e}')
        return json.dumps({'error': str(e)})


async def send_slack_message(
    channel: str,
    text: str,
    thread_ts: Optional[str] = None,
    __request__: Request = None,
    __user__: dict = None,
) -> str:
    """
    Send a message to a Slack channel or reply to a thread.

    :param channel: Channel ID (e.g. C12345678) or channel name (e.g. #general)
    :param text: The message text to send (supports Slack markdown: *bold*, _italic_, `code`)
    :param thread_ts: Optional thread timestamp to reply in a thread (from get_slack_channel_history)
    :return: JSON with status, channel, and message timestamp
    """
    if not __request__ or not __user__:
        return json.dumps({'error': 'Request context not available'})

    token = await _get_slack_token(__user__.get('id', ''))
    if not token:
        return json.dumps({'error': 'Slack not connected.'})

    payload = {'channel': channel, 'text': text}
    if thread_ts:
        payload['thread_ts'] = thread_ts

    try:
        data = await _slack_post(token, 'chat.postMessage', payload)

        if data.get('ok'):
            return json.dumps({
                'status': 'sent',
                'channel': data.get('channel', channel),
                'ts': data.get('ts', ''),
            })
        return json.dumps({'error': f'Slack API error: {data.get("error", "unknown")}'})

    except Exception as e:
        log.error(f'send_slack_message error: {e}')
        return json.dumps({'error': str(e)})


async def search_slack_messages(
    query: str,
    count: int = 10,
    __request__: Request = None,
    __user__: dict = None,
) -> str:
    """
    Search for messages across all Slack channels in the workspace.

    :param query: Search query string (supports Slack search modifiers like in:#channel or from:@user)
    :param count: Maximum number of results to return (default: 10, max: 25)
    :return: JSON list of matching messages with channel, user, text, and permalink
    """
    if not __request__ or not __user__:
        return json.dumps({'error': 'Request context not available'})

    token = await _get_slack_token(__user__.get('id', ''))
    if not token:
        return json.dumps({'error': 'Slack not connected.'})

    count = min(max(1, int(count)), 25)

    try:
        data = await _slack_get(token, 'search.messages', {
            'query': query,
            'count': count,
            'sort': 'timestamp',
            'sort_dir': 'desc',
        })

        if not data.get('ok'):
            return json.dumps({'error': f'Slack API error: {data.get("error", "unknown")}'})

        matches = data.get('messages', {}).get('matches', [])
        results = [
            {
                'channel': m.get('channel', {}).get('name', ''),
                'channel_id': m.get('channel', {}).get('id', ''),
                'user': m.get('username', ''),
                'text': m.get('text', '')[:500],
                'timestamp': m.get('ts', ''),
                'permalink': m.get('permalink', ''),
            }
            for m in matches
        ]

        return json.dumps({'results': results, 'count': len(results), 'query': query})

    except Exception as e:
        log.error(f'search_slack_messages error: {e}')
        return json.dumps({'error': str(e)})


async def send_slack_dm(
    user_email: str,
    text: str,
    __request__: Request = None,
    __user__: dict = None,
) -> str:
    """
    Send a direct message to a user in Slack by their email address.

    :param user_email: The recipient's email address (must be a member of the Slack workspace)
    :param text: The message text to send
    :return: JSON with status and message timestamp
    """
    if not __request__ or not __user__:
        return json.dumps({'error': 'Request context not available'})

    token = await _get_slack_token(__user__.get('id', ''))
    if not token:
        return json.dumps({'error': 'Slack not connected.'})

    try:
        # Look up user by email
        lookup = await _slack_get(token, 'users.lookupByEmail', {'email': user_email})
        if not lookup.get('ok'):
            return json.dumps({'error': f'User not found: {lookup.get("error", "unknown")}'})

        slack_user_id = lookup['user']['id']

        # Open a DM channel
        dm_open = await _slack_post(token, 'conversations.open', {'users': slack_user_id})
        if not dm_open.get('ok'):
            return json.dumps({'error': f'Could not open DM: {dm_open.get("error", "unknown")}'})

        dm_channel = dm_open['channel']['id']

        # Send the message
        data = await _slack_post(token, 'chat.postMessage', {
            'channel': dm_channel,
            'text': text,
        })

        if data.get('ok'):
            return json.dumps({'status': 'sent', 'to': user_email, 'ts': data.get('ts', '')})
        return json.dumps({'error': f'Slack API error: {data.get("error", "unknown")}'})

    except Exception as e:
        log.error(f'send_slack_dm error: {e}')
        return json.dumps({'error': str(e)})
