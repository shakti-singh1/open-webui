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

    # Refresh if the access token is expiring within 5 minutes
    # access_token_expires_at tracks the 1-hour window; expires_at is the 90-day refresh-token lifetime
    access_expires = session.token.get('access_token_expires_at', session.expires_at)
    if access_expires - int(time.time()) < 300:
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


# ─── Mailbox folder tools ──────────────────────────────────────────────────────


async def list_mailbox_folders(
    __request__: Request = None,
    __user__: dict = None,
) -> str:
    """
    List all folders in the user's Outlook mailbox (Inbox, Sent Items, Drafts, etc.).

    :return: JSON list of folders with id, displayName, totalItemCount, and unreadItemCount
    """
    if not __request__ or not __user__:
        return json.dumps({'error': 'Request context not available'})

    token = await _get_valid_token(__request__, __user__.get('id', ''))
    if not token:
        return json.dumps({'error': 'Microsoft account not connected. Please connect via Settings → Integrations.'})

    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                f'{GRAPH_BASE}/me/mailFolders?$select=id,displayName,totalItemCount,unreadItemCount&$top=50',
                headers=_graph_headers(token),
                ssl=AIOHTTP_CLIENT_SESSION_SSL,
            ) as resp:
                if resp.status != 200:
                    err = await resp.text()
                    return json.dumps({'error': f'Graph API error {resp.status}: {err[:200]}'})
                data = await resp.json()

        folders = [
            {
                'id': f.get('id', ''),
                'name': f.get('displayName', ''),
                'total': f.get('totalItemCount', 0),
                'unread': f.get('unreadItemCount', 0),
            }
            for f in data.get('value', [])
        ]
        return json.dumps({'folders': folders, 'count': len(folders)})

    except Exception as e:
        log.error(f'list_mailbox_folders error: {e}')
        return json.dumps({'error': str(e)})


async def get_shared_mailbox_emails(
    shared_mailbox_email: str,
    folder: str = 'inbox',
    count: int = 10,
    search: Optional[str] = None,
    __request__: Request = None,
    __user__: dict = None,
) -> str:
    """
    Read emails from a shared mailbox the user has access to.

    :param shared_mailbox_email: Email address of the shared mailbox (e.g. "support@company.com")
    :param folder: Mail folder to read: inbox, sentitems, drafts, deleteditems (default: inbox)
    :param count: Number of emails to return (default: 10, max: 50)
    :param search: Optional search query to filter by subject, sender, or body
    :return: JSON list of emails with subject, from, receivedDateTime, and bodyPreview
    """
    if not __request__ or not __user__:
        return json.dumps({'error': 'Request context not available'})

    token = await _get_valid_token(__request__, __user__.get('id', ''))
    if not token:
        return json.dumps({'error': 'Microsoft account not connected.'})

    count = min(max(1, int(count)), 50)
    select = 'id,subject,from,receivedDateTime,bodyPreview,isRead,hasAttachments'

    if search:
        url = f'{GRAPH_BASE}/users/{shared_mailbox_email}/messages?$search="{search}"&$top={count}&$select={select}'
    else:
        url = (
            f'{GRAPH_BASE}/users/{shared_mailbox_email}/mailFolders/{folder}/messages'
            f'?$top={count}&$select={select}&$orderby=receivedDateTime desc'
        )

    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, headers=_graph_headers(token), ssl=AIOHTTP_CLIENT_SESSION_SSL) as resp:
                if resp.status == 403:
                    return json.dumps({'error': f'Access denied to mailbox {shared_mailbox_email}. You must have Full Access permissions.'})
                if resp.status != 200:
                    err = await resp.text()
                    return json.dumps({'error': f'Graph API error {resp.status}: {err[:200]}'})
                data = await resp.json()

        emails = [
            {
                'id': m.get('id', ''),
                'subject': m.get('subject', '(no subject)'),
                'from': m.get('from', {}).get('emailAddress', {}).get('address', ''),
                'from_name': m.get('from', {}).get('emailAddress', {}).get('name', ''),
                'received': m.get('receivedDateTime', ''),
                'preview': m.get('bodyPreview', '')[:300],
                'is_read': m.get('isRead', True),
                'has_attachments': m.get('hasAttachments', False),
            }
            for m in data.get('value', [])
        ]
        return json.dumps({'emails': emails, 'mailbox': shared_mailbox_email, 'count': len(emails)})

    except Exception as e:
        log.error(f'get_shared_mailbox_emails error: {e}')
        return json.dumps({'error': str(e)})


# ─── Shared Calendar tools ─────────────────────────────────────────────────────


async def list_calendars(
    __request__: Request = None,
    __user__: dict = None,
) -> str:
    """
    List all calendars the user has access to, including shared calendars.

    :return: JSON list of calendars with id, name, owner, canEdit, and color
    """
    if not __request__ or not __user__:
        return json.dumps({'error': 'Request context not available'})

    token = await _get_valid_token(__request__, __user__.get('id', ''))
    if not token:
        return json.dumps({'error': 'Microsoft account not connected. Please connect via Settings → Integrations.'})

    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                f'{GRAPH_BASE}/me/calendars?$select=id,name,owner,canEdit,color,isDefaultCalendar&$top=50',
                headers=_graph_headers(token),
                ssl=AIOHTTP_CLIENT_SESSION_SSL,
            ) as resp:
                if resp.status != 200:
                    err = await resp.text()
                    return json.dumps({'error': f'Graph API error {resp.status}: {err[:200]}'})
                data = await resp.json()

        calendars = [
            {
                'id': c.get('id', ''),
                'name': c.get('name', ''),
                'owner': c.get('owner', {}).get('address', ''),
                'can_edit': c.get('canEdit', False),
                'is_default': c.get('isDefaultCalendar', False),
                'color': c.get('color', ''),
            }
            for c in data.get('value', [])
        ]
        return json.dumps({'calendars': calendars, 'count': len(calendars)})

    except Exception as e:
        log.error(f'list_calendars error: {e}')
        return json.dumps({'error': str(e)})


async def get_shared_calendar_events(
    calendar_id: str,
    start: Optional[str] = None,
    end: Optional[str] = None,
    count: int = 10,
    __request__: Request = None,
    __user__: dict = None,
) -> str:
    """
    Get events from a specific calendar, including shared or delegated calendars.

    :param calendar_id: Calendar ID from list_calendars
    :param start: Start datetime ISO format e.g. "2026-05-13T00:00:00" (defaults to now)
    :param end: End datetime ISO format e.g. "2026-05-20T00:00:00" (defaults to 7 days from start)
    :param count: Maximum events to return (default: 10, max: 50)
    :return: JSON list of events with subject, start, end, organizer, and attendees
    """
    if not __request__ or not __user__:
        return json.dumps({'error': 'Request context not available'})

    token = await _get_valid_token(__request__, __user__.get('id', ''))
    if not token:
        return json.dumps({'error': 'Microsoft account not connected.'})

    from datetime import datetime, timezone, timedelta
    now = datetime.now(timezone.utc)
    start_dt = start or now.strftime('%Y-%m-%dT%H:%M:%SZ')
    end_dt = end or (now + timedelta(days=7)).strftime('%Y-%m-%dT%H:%M:%SZ')
    if not start_dt.endswith('Z') and '+' not in start_dt and len(start_dt) == 19:
        start_dt += 'Z'
    if not end_dt.endswith('Z') and '+' not in end_dt and len(end_dt) == 19:
        end_dt += 'Z'

    count = min(max(1, int(count)), 50)
    select = 'subject,start,end,location,organizer,attendees,isAllDay'
    url = (
        f'{GRAPH_BASE}/me/calendars/{calendar_id}/calendarView'
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

        events = [
            {
                'subject': ev.get('subject', ''),
                'start': ev.get('start', {}).get('dateTime', ''),
                'end': ev.get('end', {}).get('dateTime', ''),
                'all_day': ev.get('isAllDay', False),
                'location': ev.get('location', {}).get('displayName', ''),
                'organizer': ev.get('organizer', {}).get('emailAddress', {}).get('address', ''),
                'attendees': [
                    a.get('emailAddress', {}).get('address', '')
                    for a in ev.get('attendees', [])
                ],
            }
            for ev in data.get('value', [])
        ]
        return json.dumps({'events': events, 'calendar_id': calendar_id, 'count': len(events)})

    except Exception as e:
        log.error(f'get_shared_calendar_events error: {e}')
        return json.dumps({'error': str(e)})


async def get_user_availability(
    emails: str,
    start: str,
    end: str,
    interval_minutes: int = 30,
    __request__: Request = None,
    __user__: dict = None,
) -> str:
    """
    Check the availability (free/busy schedule) of one or more users for meeting scheduling.

    :param emails: Comma-separated email addresses to check (e.g. "alice@company.com,bob@company.com")
    :param start: Start datetime ISO format e.g. "2026-05-15T09:00:00" (UTC assumed if no timezone)
    :param end: End datetime ISO format e.g. "2026-05-15T18:00:00"
    :param interval_minutes: Granularity of the schedule view in minutes (default: 30)
    :return: JSON with each user's free/busy blocks and an availability summary
    """
    if not __request__ or not __user__:
        return json.dumps({'error': 'Request context not available'})

    token = await _get_valid_token(__request__, __user__.get('id', ''))
    if not token:
        return json.dumps({'error': 'Microsoft account not connected.'})

    email_list = [e.strip() for e in emails.split(',') if e.strip()]
    if not email_list:
        return json.dumps({'error': 'No email addresses provided.'})

    if not start.endswith('Z') and '+' not in start and len(start) == 19:
        start += 'Z'
    if not end.endswith('Z') and '+' not in end and len(end) == 19:
        end += 'Z'

    payload = {
        'schedules': email_list,
        'startTime': {'dateTime': start, 'timeZone': 'UTC'},
        'endTime': {'dateTime': end, 'timeZone': 'UTC'},
        'availabilityViewInterval': min(max(15, interval_minutes), 60),
    }

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                f'{GRAPH_BASE}/me/calendar/getSchedule',
                headers=_graph_headers(token),
                json=payload,
                ssl=AIOHTTP_CLIENT_SESSION_SSL,
            ) as resp:
                if resp.status != 200:
                    err = await resp.text()
                    return json.dumps({'error': f'Graph API error {resp.status}: {err[:200]}'})
                data = await resp.json()

        results = []
        for sched in data.get('value', []):
            busy_blocks = [
                {
                    'start': item.get('start', {}).get('dateTime', ''),
                    'end': item.get('end', {}).get('dateTime', ''),
                    'status': item.get('status', ''),
                    'subject': item.get('subject', ''),
                }
                for item in sched.get('scheduleItems', [])
                if item.get('status', '') not in ('free', 'unknown')
            ]
            results.append({
                'email': sched.get('scheduleId', ''),
                'availability_view': sched.get('availabilityView', ''),
                'busy_blocks': busy_blocks,
            })

        return json.dumps({'schedules': results, 'start': start, 'end': end})

    except Exception as e:
        log.error(f'get_user_availability error: {e}')
        return json.dumps({'error': str(e)})


# ─── Teams Chat tools ──────────────────────────────────────────────────────────


async def list_teams_chats(
    count: int = 20,
    __request__: Request = None,
    __user__: dict = None,
) -> str:
    """
    List the user's Teams chat conversations (1:1 chats, group chats, and meeting chats).

    :param count: Number of chats to return (default: 20, max: 50)
    :return: JSON list of chats with id, topic, chatType, and last activity time
    """
    if not __request__ or not __user__:
        return json.dumps({'error': 'Request context not available'})

    token = await _get_valid_token(__request__, __user__.get('id', ''))
    if not token:
        return json.dumps({'error': 'Microsoft account not connected. Please connect via Settings → Integrations.'})

    count = min(max(1, int(count)), 50)

    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                f'{GRAPH_BASE}/me/chats?$select=id,topic,chatType,lastUpdatedDateTime&$top={count}',
                headers=_graph_headers(token),
                ssl=AIOHTTP_CLIENT_SESSION_SSL,
            ) as resp:
                if resp.status != 200:
                    err = await resp.text()
                    return json.dumps({'error': f'Graph API error {resp.status}: {err[:200]}'})
                data = await resp.json()

        chats = [
            {
                'id': c.get('id', ''),
                'topic': c.get('topic') or '(no topic)',
                'type': c.get('chatType', ''),
                'last_updated': c.get('lastUpdatedDateTime', ''),
            }
            for c in data.get('value', [])
        ]
        return json.dumps({'chats': chats, 'count': len(chats)})

    except Exception as e:
        log.error(f'list_teams_chats error: {e}')
        return json.dumps({'error': str(e)})


async def get_teams_chat_messages(
    chat_id: str,
    count: int = 20,
    __request__: Request = None,
    __user__: dict = None,
) -> str:
    """
    Retrieve recent messages from a Teams chat (1:1 or group chat).

    :param chat_id: The chat ID (from list_teams_chats)
    :param count: Number of recent messages to fetch (default: 20, max: 50)
    :return: JSON list of messages with sender, content, and timestamp
    """
    if not __request__ or not __user__:
        return json.dumps({'error': 'Request context not available'})

    token = await _get_valid_token(__request__, __user__.get('id', ''))
    if not token:
        return json.dumps({'error': 'Microsoft account not connected.'})

    count = min(max(1, int(count)), 50)

    try:
        import re as _re
        async with aiohttp.ClientSession() as session:
            async with session.get(
                f'{GRAPH_BASE}/me/chats/{chat_id}/messages?$top={count}',
                headers=_graph_headers(token),
                ssl=AIOHTTP_CLIENT_SESSION_SSL,
            ) as resp:
                if resp.status != 200:
                    err = await resp.text()
                    return json.dumps({'error': f'Graph API error {resp.status}: {err[:200]}'})
                data = await resp.json()

        messages = []
        for m in data.get('value', []):
            body = m.get('body', {}).get('content', '')
            body_text = _re.sub(r'<[^>]+>', ' ', body).strip()[:500]
            sender = (
                m.get('from', {}).get('user', {}).get('displayName')
                or m.get('from', {}).get('application', {}).get('displayName')
                or 'unknown'
            )
            messages.append({
                'id': m.get('id', ''),
                'sender': sender,
                'content': body_text,
                'timestamp': m.get('createdDateTime', ''),
            })

        return json.dumps({'messages': messages, 'chat_id': chat_id, 'count': len(messages)})

    except Exception as e:
        log.error(f'get_teams_chat_messages error: {e}')
        return json.dumps({'error': str(e)})


async def get_chat_members(
    chat_id: str,
    __request__: Request = None,
    __user__: dict = None,
) -> str:
    """
    List the participants of a Teams chat conversation.

    :param chat_id: The chat ID (from list_teams_chats)
    :return: JSON list of members with displayName, email, and role
    """
    if not __request__ or not __user__:
        return json.dumps({'error': 'Request context not available'})

    token = await _get_valid_token(__request__, __user__.get('id', ''))
    if not token:
        return json.dumps({'error': 'Microsoft account not connected.'})

    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                f'{GRAPH_BASE}/me/chats/{chat_id}/members',
                headers=_graph_headers(token),
                ssl=AIOHTTP_CLIENT_SESSION_SSL,
            ) as resp:
                if resp.status != 200:
                    err = await resp.text()
                    return json.dumps({'error': f'Graph API error {resp.status}: {err[:200]}'})
                data = await resp.json()

        members = [
            {
                'name': m.get('displayName', ''),
                'email': m.get('email', ''),
                'role': m.get('roles', ['member'])[0] if m.get('roles') else 'member',
            }
            for m in data.get('value', [])
        ]
        return json.dumps({'members': members, 'chat_id': chat_id, 'count': len(members)})

    except Exception as e:
        log.error(f'get_chat_members error: {e}')
        return json.dumps({'error': str(e)})


# ─── User directory tools ──────────────────────────────────────────────────────


async def find_org_users(
    query: str,
    count: int = 10,
    __request__: Request = None,
    __user__: dict = None,
) -> str:
    """
    Search for users in the organization by name or email address.
    Useful for finding colleagues, resolving names to emails, or setting up meetings.

    :param query: Name or partial email to search for (e.g. "John Smith" or "jsmith")
    :param count: Maximum number of results to return (default: 10, max: 25)
    :return: JSON list of users with displayName, email, jobTitle, and department
    """
    if not __request__ or not __user__:
        return json.dumps({'error': 'Request context not available'})

    token = await _get_valid_token(__request__, __user__.get('id', ''))
    if not token:
        return json.dumps({'error': 'Microsoft account not connected. Please connect via Settings → Integrations.'})

    count = min(max(1, int(count)), 25)
    select = 'id,displayName,mail,userPrincipalName,jobTitle,department,officeLocation'

    try:
        async with aiohttp.ClientSession() as session:
            # Use $search for display name matching (requires ConsistencyLevel header)
            headers = {**_graph_headers(token), 'ConsistencyLevel': 'eventual'}
            search_url = (
                f'{GRAPH_BASE}/users'
                f'?$search="displayName:{query}" OR "mail:{query}"'
                f'&$select={select}&$top={count}&$count=true'
            )
            async with session.get(search_url, headers=headers, ssl=AIOHTTP_CLIENT_SESSION_SSL) as resp:
                if resp.status != 200:
                    err = await resp.text()
                    return json.dumps({'error': f'Graph API error {resp.status}: {err[:200]}'})
                data = await resp.json()

        users = [
            {
                'id': u.get('id', ''),
                'name': u.get('displayName', ''),
                'email': u.get('mail') or u.get('userPrincipalName', ''),
                'job_title': u.get('jobTitle', ''),
                'department': u.get('department', ''),
                'office': u.get('officeLocation', ''),
            }
            for u in data.get('value', [])
        ]
        return json.dumps({'users': users, 'count': len(users), 'query': query})

    except Exception as e:
        log.error(f'find_org_users error: {e}')
        return json.dumps({'error': str(e)})
