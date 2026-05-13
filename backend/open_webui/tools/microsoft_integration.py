"""
Microsoft Graph API tools for Open WebUI.

These are builtin tools that become available automatically when:
  1. Admin has enabled ENABLE_MICROSOFT_TEAMS_INTEGRATION
  2. The current user has connected their Microsoft account via Settings → Integrations

Functions receive __request__ and __user__ injected at runtime; they are
never exposed to the LLM as required parameters.
"""

import json
import logging
import time
from typing import Optional

import aiohttp
from fastapi import Request

from open_webui.env import AIOHTTP_CLIENT_SESSION_SSL

log = logging.getLogger(__name__)

MICROSOFT_PROVIDER = 'microsoft_teams_integration'
GRAPH_BASE = 'https://graph.microsoft.com/v1.0'


# ─── Token helpers ────────────────────────────────────────────────────────────


async def _get_valid_token(request: Request, user_id: str) -> Optional[str]:
    """Return a valid (possibly refreshed) Microsoft access token for the user."""
    from open_webui.models.oauth_sessions import OAuthSessions
    from open_webui.routers.integrations import refresh_microsoft_token

    session = await OAuthSessions.get_session_by_provider_and_user_id(MICROSOFT_PROVIDER, user_id)
    if not session:
        return None

    # Refresh if expiring within 5 minutes
    if session.expires_at - int(time.time()) < 300:
        updated = await refresh_microsoft_token(request.app.state.config, session)
        if updated:
            return updated.get('access_token')
        return None

    return session.token.get('access_token')


def _graph_headers(access_token: str) -> dict:
    return {
        'Authorization': f'Bearer {access_token}',
        'Content-Type': 'application/json',
    }


# ─── Outlook Email tools ───────────────────────────────────────────────────────


async def get_outlook_emails(
    folder: str = 'inbox',
    count: int = 10,
    search: Optional[str] = None,
    __request__: Request = None,
    __user__: dict = None,
) -> str:
    """
    Retrieve emails from the user's Outlook mailbox.

    :param folder: Mail folder to read from. Options: inbox, sentitems, drafts, deleteditems (default: inbox)
    :param count: Maximum number of emails to return (default: 10, max: 50)
    :param search: Optional search query to filter emails by subject, body, or sender
    :return: JSON list of emails with id, subject, from, receivedDateTime, bodyPreview, isRead
    """
    if not __request__ or not __user__:
        return json.dumps({'error': 'Request context not available'})

    user_id = __user__.get('id', '')
    token = await _get_valid_token(__request__, user_id)
    if not token:
        return json.dumps({'error': 'Microsoft account not connected. Please connect via Settings → Integrations.'})

    count = min(max(1, int(count)), 50)
    select = 'id,subject,from,receivedDateTime,bodyPreview,isRead,hasAttachments'

    if search:
        url = f'{GRAPH_BASE}/me/messages?$search="{search}"&$top={count}&$select={select}'
    else:
        url = f'{GRAPH_BASE}/me/mailFolders/{folder}/messages?$top={count}&$select={select}&$orderby=receivedDateTime desc'

    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, headers=_graph_headers(token), ssl=AIOHTTP_CLIENT_SESSION_SSL) as resp:
                if resp.status == 401:
                    return json.dumps({'error': 'Microsoft token expired. Please reconnect via Settings → Integrations.'})
                if resp.status != 200:
                    err = await resp.text()
                    return json.dumps({'error': f'Graph API error {resp.status}: {err[:200]}'})
                data = await resp.json()

        emails = []
        for msg in data.get('value', []):
            emails.append({
                'id': msg.get('id', ''),
                'subject': msg.get('subject', '(no subject)'),
                'from': msg.get('from', {}).get('emailAddress', {}).get('address', ''),
                'from_name': msg.get('from', {}).get('emailAddress', {}).get('name', ''),
                'received': msg.get('receivedDateTime', ''),
                'preview': msg.get('bodyPreview', '')[:300],
                'is_read': msg.get('isRead', True),
                'has_attachments': msg.get('hasAttachments', False),
            })

        return json.dumps({'emails': emails, 'count': len(emails)})

    except Exception as e:
        log.error(f'get_outlook_emails error: {e}')
        return json.dumps({'error': str(e)})


async def read_outlook_email(
    email_id: str,
    __request__: Request = None,
    __user__: dict = None,
) -> str:
    """
    Read the full body of a specific Outlook email by its ID.

    :param email_id: The email ID (from get_outlook_emails results)
    :return: JSON with subject, from, to, receivedDateTime, and full body text
    """
    if not __request__ or not __user__:
        return json.dumps({'error': 'Request context not available'})

    user_id = __user__.get('id', '')
    token = await _get_valid_token(__request__, user_id)
    if not token:
        return json.dumps({'error': 'Microsoft account not connected.'})

    url = f'{GRAPH_BASE}/me/messages/{email_id}?$select=subject,from,toRecipients,receivedDateTime,body'

    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, headers=_graph_headers(token), ssl=AIOHTTP_CLIENT_SESSION_SSL) as resp:
                if resp.status != 200:
                    return json.dumps({'error': f'Graph API error {resp.status}'})
                msg = await resp.json()

        body_content = msg.get('body', {}).get('content', '')
        # Strip basic HTML tags for readability
        import re
        body_text = re.sub(r'<[^>]+>', ' ', body_content).strip()
        body_text = re.sub(r'\s+', ' ', body_text)[:3000]

        return json.dumps({
            'subject': msg.get('subject', ''),
            'from': msg.get('from', {}).get('emailAddress', {}).get('address', ''),
            'to': [r.get('emailAddress', {}).get('address', '') for r in msg.get('toRecipients', [])],
            'received': msg.get('receivedDateTime', ''),
            'body': body_text,
        })

    except Exception as e:
        log.error(f'read_outlook_email error: {e}')
        return json.dumps({'error': str(e)})


async def send_outlook_email(
    to: str,
    subject: str,
    body: str,
    cc: Optional[str] = None,
    __request__: Request = None,
    __user__: dict = None,
) -> str:
    """
    Send an email via the user's Outlook account.

    :param to: Recipient email address (or comma-separated list for multiple)
    :param subject: Email subject line
    :param body: Email body text (plain text)
    :param cc: CC recipient email address or comma-separated list (optional)
    :return: JSON with status indicating success or error
    """
    if not __request__ or not __user__:
        return json.dumps({'error': 'Request context not available'})

    user_id = __user__.get('id', '')
    token = await _get_valid_token(__request__, user_id)
    if not token:
        return json.dumps({'error': 'Microsoft account not connected.'})

    def _addr_list(addr_str: str) -> list:
        return [
            {'emailAddress': {'address': a.strip()}}
            for a in addr_str.split(',')
            if a.strip()
        ]

    message = {
        'subject': subject,
        'body': {'contentType': 'Text', 'content': body},
        'toRecipients': _addr_list(to),
    }
    if cc:
        message['ccRecipients'] = _addr_list(cc)

    payload = {'message': message, 'saveToSentItems': True}

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                f'{GRAPH_BASE}/me/sendMail',
                headers=_graph_headers(token),
                json=payload,
                ssl=AIOHTTP_CLIENT_SESSION_SSL,
            ) as resp:
                if resp.status == 202:
                    return json.dumps({'status': 'sent', 'to': to, 'subject': subject})
                err = await resp.text()
                return json.dumps({'error': f'Failed to send email: {err[:300]}'})

    except Exception as e:
        log.error(f'send_outlook_email error: {e}')
        return json.dumps({'error': str(e)})


# ─── Calendar tools ────────────────────────────────────────────────────────────


async def get_outlook_calendar_events(
    start: Optional[str] = None,
    end: Optional[str] = None,
    count: int = 10,
    __request__: Request = None,
    __user__: dict = None,
) -> str:
    """
    Retrieve events from the user's Outlook / Microsoft 365 calendar.

    :param start: Start of the date range in ISO format, e.g. "2026-05-13T00:00:00" (defaults to now)
    :param end: End of the date range in ISO format, e.g. "2026-05-20T00:00:00" (defaults to 7 days from start)
    :param count: Maximum number of events to return (default: 10, max: 50)
    :return: JSON list of events with subject, start, end, location, organizer, and body preview
    """
    if not __request__ or not __user__:
        return json.dumps({'error': 'Request context not available'})

    user_id = __user__.get('id', '')
    token = await _get_valid_token(__request__, user_id)
    if not token:
        return json.dumps({'error': 'Microsoft account not connected. Please connect via Settings → Integrations.'})

    from datetime import datetime, timezone, timedelta

    now = datetime.now(timezone.utc)
    start_dt = start or now.strftime('%Y-%m-%dT%H:%M:%S')
    end_dt = end or (now + timedelta(days=7)).strftime('%Y-%m-%dT%H:%M:%S')

    # Ensure timezone suffix for Graph API
    if not start_dt.endswith('Z') and '+' not in start_dt and len(start_dt) == 19:
        start_dt += 'Z'
    if not end_dt.endswith('Z') and '+' not in end_dt and len(end_dt) == 19:
        end_dt += 'Z'

    count = min(max(1, int(count)), 50)
    select = 'subject,start,end,location,organizer,bodyPreview,isAllDay'
    url = (
        f'{GRAPH_BASE}/me/calendarView'
        f'?startDateTime={start_dt}&endDateTime={end_dt}'
        f'&$top={count}&$select={select}&$orderby=start/dateTime'
    )

    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, headers=_graph_headers(token), ssl=AIOHTTP_CLIENT_SESSION_SSL) as resp:
                if resp.status != 200:
                    err = await resp.text()
                    return json.dumps({'error': f'Graph API error {resp.status}: {err[:200]}'})
                data = await resp.json()

        events = []
        for ev in data.get('value', []):
            events.append({
                'subject': ev.get('subject', ''),
                'start': ev.get('start', {}).get('dateTime', ''),
                'end': ev.get('end', {}).get('dateTime', ''),
                'all_day': ev.get('isAllDay', False),
                'location': ev.get('location', {}).get('displayName', ''),
                'organizer': ev.get('organizer', {}).get('emailAddress', {}).get('address', ''),
                'preview': ev.get('bodyPreview', '')[:200],
            })

        return json.dumps({'events': events, 'count': len(events)})

    except Exception as e:
        log.error(f'get_outlook_calendar_events error: {e}')
        return json.dumps({'error': str(e)})


async def create_outlook_calendar_event(
    subject: str,
    start: str,
    end: str,
    location: Optional[str] = None,
    body: Optional[str] = None,
    attendees: Optional[str] = None,
    __request__: Request = None,
    __user__: dict = None,
) -> str:
    """
    Create a new event in the user's Outlook / Microsoft 365 calendar.

    :param subject: Event title
    :param start: Start datetime in ISO format, e.g. "2026-05-15T14:00:00"
    :param end: End datetime in ISO format, e.g. "2026-05-15T15:00:00"
    :param location: Optional event location (room name, address, or Teams link)
    :param body: Optional event description/agenda
    :param attendees: Optional comma-separated list of attendee email addresses
    :return: JSON with the created event id, subject, start, end
    """
    if not __request__ or not __user__:
        return json.dumps({'error': 'Request context not available'})

    user_id = __user__.get('id', '')
    token = await _get_valid_token(__request__, user_id)
    if not token:
        return json.dumps({'error': 'Microsoft account not connected.'})

    event_payload: dict = {
        'subject': subject,
        'start': {'dateTime': start, 'timeZone': 'UTC'},
        'end': {'dateTime': end, 'timeZone': 'UTC'},
    }
    if location:
        event_payload['location'] = {'displayName': location}
    if body:
        event_payload['body'] = {'contentType': 'Text', 'content': body}
    if attendees:
        event_payload['attendees'] = [
            {'emailAddress': {'address': a.strip()}, 'type': 'required'}
            for a in attendees.split(',')
            if a.strip()
        ]

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                f'{GRAPH_BASE}/me/events',
                headers=_graph_headers(token),
                json=event_payload,
                ssl=AIOHTTP_CLIENT_SESSION_SSL,
            ) as resp:
                if resp.status == 201:
                    ev = await resp.json()
                    return json.dumps({
                        'status': 'created',
                        'id': ev.get('id', ''),
                        'subject': ev.get('subject', ''),
                        'start': ev.get('start', {}).get('dateTime', ''),
                        'end': ev.get('end', {}).get('dateTime', ''),
                        'webLink': ev.get('webLink', ''),
                    })
                err = await resp.text()
                return json.dumps({'error': f'Failed to create event: {err[:300]}'})

    except Exception as e:
        log.error(f'create_outlook_calendar_event error: {e}')
        return json.dumps({'error': str(e)})


# ─── Microsoft Teams tools ─────────────────────────────────────────────────────


async def list_teams(
    __request__: Request = None,
    __user__: dict = None,
) -> str:
    """
    List the Microsoft Teams teams the user is a member of.

    :return: JSON list of teams with id, displayName, and description
    """
    if not __request__ or not __user__:
        return json.dumps({'error': 'Request context not available'})

    user_id = __user__.get('id', '')
    token = await _get_valid_token(__request__, user_id)
    if not token:
        return json.dumps({'error': 'Microsoft account not connected. Please connect via Settings → Integrations.'})

    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                f'{GRAPH_BASE}/me/joinedTeams?$select=id,displayName,description',
                headers=_graph_headers(token),
                ssl=AIOHTTP_CLIENT_SESSION_SSL,
            ) as resp:
                if resp.status != 200:
                    err = await resp.text()
                    return json.dumps({'error': f'Graph API error {resp.status}: {err[:200]}'})
                data = await resp.json()

        teams = [
            {
                'id': t.get('id', ''),
                'name': t.get('displayName', ''),
                'description': t.get('description', ''),
            }
            for t in data.get('value', [])
        ]
        return json.dumps({'teams': teams, 'count': len(teams)})

    except Exception as e:
        log.error(f'list_teams error: {e}')
        return json.dumps({'error': str(e)})


async def get_teams_channels(
    team_id: str,
    __request__: Request = None,
    __user__: dict = None,
) -> str:
    """
    List channels in a Microsoft Teams team.

    :param team_id: The team ID (from list_teams results)
    :return: JSON list of channels with id, displayName, and description
    """
    if not __request__ or not __user__:
        return json.dumps({'error': 'Request context not available'})

    user_id = __user__.get('id', '')
    token = await _get_valid_token(__request__, user_id)
    if not token:
        return json.dumps({'error': 'Microsoft account not connected.'})

    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                f'{GRAPH_BASE}/teams/{team_id}/channels?$select=id,displayName,description',
                headers=_graph_headers(token),
                ssl=AIOHTTP_CLIENT_SESSION_SSL,
            ) as resp:
                if resp.status != 200:
                    err = await resp.text()
                    return json.dumps({'error': f'Graph API error {resp.status}: {err[:200]}'})
                data = await resp.json()

        channels = [
            {
                'id': c.get('id', ''),
                'name': c.get('displayName', ''),
                'description': c.get('description', ''),
            }
            for c in data.get('value', [])
        ]
        return json.dumps({'channels': channels, 'team_id': team_id, 'count': len(channels)})

    except Exception as e:
        log.error(f'get_teams_channels error: {e}')
        return json.dumps({'error': str(e)})


async def send_teams_message(
    team_id: str,
    channel_id: str,
    message: str,
    __request__: Request = None,
    __user__: dict = None,
) -> str:
    """
    Send a message to a Microsoft Teams channel.

    :param team_id: The team ID (from list_teams)
    :param channel_id: The channel ID (from get_teams_channels)
    :param message: The message text to send
    :return: JSON with status and the message ID
    """
    if not __request__ or not __user__:
        return json.dumps({'error': 'Request context not available'})

    user_id = __user__.get('id', '')
    token = await _get_valid_token(__request__, user_id)
    if not token:
        return json.dumps({'error': 'Microsoft account not connected.'})

    payload = {'body': {'content': message}}

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                f'{GRAPH_BASE}/teams/{team_id}/channels/{channel_id}/messages',
                headers=_graph_headers(token),
                json=payload,
                ssl=AIOHTTP_CLIENT_SESSION_SSL,
            ) as resp:
                if resp.status == 201:
                    msg = await resp.json()
                    return json.dumps({
                        'status': 'sent',
                        'id': msg.get('id', ''),
                        'channel_id': channel_id,
                        'team_id': team_id,
                    })
                err = await resp.text()
                return json.dumps({'error': f'Failed to send Teams message: {err[:300]}'})

    except Exception as e:
        log.error(f'send_teams_message error: {e}')
        return json.dumps({'error': str(e)})


async def get_teams_channel_messages(
    team_id: str,
    channel_id: str,
    count: int = 10,
    __request__: Request = None,
    __user__: dict = None,
) -> str:
    """
    Retrieve recent messages from a Microsoft Teams channel.

    :param team_id: The team ID (from list_teams)
    :param channel_id: The channel ID (from get_teams_channels)
    :param count: Number of recent messages to fetch (default: 10, max: 50)
    :return: JSON list of messages with sender, content, and timestamp
    """
    if not __request__ or not __user__:
        return json.dumps({'error': 'Request context not available'})

    user_id = __user__.get('id', '')
    token = await _get_valid_token(__request__, user_id)
    if not token:
        return json.dumps({'error': 'Microsoft account not connected.'})

    count = min(max(1, int(count)), 50)

    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                f'{GRAPH_BASE}/teams/{team_id}/channels/{channel_id}/messages?$top={count}',
                headers=_graph_headers(token),
                ssl=AIOHTTP_CLIENT_SESSION_SSL,
            ) as resp:
                if resp.status != 200:
                    err = await resp.text()
                    return json.dumps({'error': f'Graph API error {resp.status}: {err[:200]}'})
                data = await resp.json()

        import re as _re
        messages = []
        for m in data.get('value', []):
            body = m.get('body', {}).get('content', '')
            body_text = _re.sub(r'<[^>]+>', ' ', body).strip()[:500]
            messages.append({
                'id': m.get('id', ''),
                'sender': m.get('from', {}).get('user', {}).get('displayName', ''),
                'content': body_text,
                'timestamp': m.get('createdDateTime', ''),
            })

        return json.dumps({'messages': messages, 'count': len(messages)})

    except Exception as e:
        log.error(f'get_teams_channel_messages error: {e}')
        return json.dumps({'error': str(e)})
