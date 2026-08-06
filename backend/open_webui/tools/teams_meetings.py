"""
Microsoft Teams meeting tools for Open WebUI — MOM / transcript / recording.

These are builtin tools activated when:
  1. Admin has ENABLE_MICROSOFT_TEAMS_INTEGRATION = True
  2. The user has connected their Microsoft account (microsoft_teams_integration token)

Flow for "give me a MOM for meeting X":
  1. find_teams_meeting(title)       — searches calendar, returns meeting details +
                                       attendees + online meeting join URL
  2. get_teams_meeting_transcript(…) — tries the Graph transcripts API first;
                                       if that fails (common for delegated tokens without
                                       admin consent) falls back to searching OneDrive
                                       and SharePoint for the .vtt transcript file that
                                       Teams saves alongside every recording
  3. The LLM uses the transcript text + attendee list to write the MOM

Teams recording / transcript storage locations:
  • Personal / chat meetings  → user's OneDrive  "Recordings/" folder
  • Channel meetings          → SharePoint site for that team, channel sub-folder
                                "Documents/{channel}/Recordings/"
"""

import io
import json
import logging
import re
import time
from datetime import datetime, timezone, timedelta
from typing import Optional

import aiohttp
from fastapi import Request

from open_webui.env import AIOHTTP_CLIENT_SESSION_SSL

log = logging.getLogger(__name__)

MICROSOFT_PROVIDER = 'microsoft_teams_integration'
GRAPH_BASE = 'https://graph.microsoft.com/v1.0'


async def _get_valid_token(request: Request, user_id: str) -> Optional[str]:
    from open_webui.models.oauth_sessions import OAuthSessions
    from open_webui.routers.integrations import refresh_microsoft_token

    session = await OAuthSessions.get_session_by_provider_and_user_id(MICROSOFT_PROVIDER, user_id)
    if not session:
        return None
    access_expires = session.token.get('access_token_expires_at', session.expires_at)
    if access_expires - int(time.time()) < 300:
        updated = await refresh_microsoft_token(request.app.state.config, session)
        return updated.get('access_token') if updated else None
    return session.token.get('access_token')


def _hdr(token: str) -> dict:
    return {'Authorization': f'Bearer {token}', 'Content-Type': 'application/json'}


def _parse_vtt(vtt_text: str) -> str:
    """Convert a WebVTT transcript to clean readable text."""
    lines = vtt_text.splitlines()
    out, seen = [], set()
    for line in lines:
        line = line.strip()
        # Skip timestamps, cue numbers, and WEBVTT header
        if not line or line.startswith('WEBVTT') or '-->' in line or line.isdigit():
            continue
        # Strip speaker tag  <v Speaker Name>text</v>
        clean = re.sub(r'<v[^>]*>(.*?)</v>', r'\1', line)
        clean = re.sub(r'<[^>]+>', '', clean).strip()
        if clean and clean not in seen:
            seen.add(clean)
            out.append(clean)
    return '\n'.join(out)


# ─── Tool 1: find_teams_meeting ───────────────────────────────────────────────

async def find_teams_meeting(
    title: str,
    date: Optional[str] = None,
    __request__: Request = None,
    __user__: dict = None,
) -> str:
    """
    Search the user's calendar for a Teams meeting by title and return its details,
    attendees, and a reference that can be used to fetch the transcript.

    :param title: Full or partial meeting title to search for (e.g. "ANVI For community - Sync")
    :param date: Optional date to narrow the search, e.g. "2026-05-13". Defaults to the last 7 days.
    :return: JSON with subject, start, end, attendees list, organizer, and meeting_join_url
    """
    if not __request__ or not __user__:
        return json.dumps({'error': 'Request context not available'})

    token = await _get_valid_token(__request__, __user__.get('id', ''))
    if not token:
        return json.dumps({'error': 'Microsoft account not connected. Please connect via Settings → Integrations.'})

    now = datetime.now(timezone.utc)
    if date:
        try:
            day = datetime.fromisoformat(date).replace(tzinfo=timezone.utc)
            start_dt = day.strftime('%Y-%m-%dT00:00:00Z')
            end_dt = (day + timedelta(days=1)).strftime('%Y-%m-%dT23:59:59Z')
        except ValueError:
            start_dt = (now - timedelta(days=7)).strftime('%Y-%m-%dT00:00:00Z')
            end_dt = now.strftime('%Y-%m-%dT23:59:59Z')
    else:
        start_dt = (now - timedelta(days=7)).strftime('%Y-%m-%dT00:00:00Z')
        end_dt = now.strftime('%Y-%m-%dT23:59:59Z')

    select = 'subject,start,end,organizer,attendees,onlineMeeting,isOnlineMeeting,webLink'
    url = (
        f"{GRAPH_BASE}/me/calendarView"
        f"?startDateTime={start_dt}&endDateTime={end_dt}"
        f"&$select={select}&$top=50&$orderby=start/dateTime desc"
    )

    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, headers=_hdr(token), ssl=AIOHTTP_CLIENT_SESSION_SSL) as resp:
                if resp.status != 200:
                    err = await resp.text()
                    return json.dumps({'error': f'Calendar API error {resp.status}: {err[:200]}'})
                data = await resp.json()

        title_lower = title.lower()
        matches = [
            ev for ev in data.get('value', [])
            if title_lower in ev.get('subject', '').lower()
        ]

        if not matches:
            return json.dumps({
                'found': False,
                'message': f'No meeting matching "{title}" found in the last 7 days.',
                'searched_range': f'{start_dt} to {end_dt}',
            })

        results = []
        for ev in matches:
            attendees = [
                {
                    'name': a.get('emailAddress', {}).get('name', ''),
                    'email': a.get('emailAddress', {}).get('address', ''),
                    'status': a.get('status', {}).get('response', ''),
                }
                for a in ev.get('attendees', [])
            ]
            results.append({
                'subject': ev.get('subject', ''),
                'start': ev.get('start', {}).get('dateTime', ''),
                'end': ev.get('end', {}).get('dateTime', ''),
                'organizer': ev.get('organizer', {}).get('emailAddress', {}).get('address', ''),
                'attendees': attendees,
                'is_online_meeting': ev.get('isOnlineMeeting', False),
                'meeting_join_url': ev.get('onlineMeeting', {}).get('joinUrl', ''),
                'web_link': ev.get('webLink', ''),
            })

        return json.dumps({'found': True, 'meetings': results, 'count': len(results)})

    except Exception as e:
        log.error(f'find_teams_meeting error: {e}')
        return json.dumps({'error': str(e)})


# ─── Tool 2: get_teams_meeting_transcript ─────────────────────────────────────

async def get_teams_meeting_transcript(
    meeting_title: str,
    date: Optional[str] = None,
    meeting_join_url: Optional[str] = None,
    __request__: Request = None,
    __user__: dict = None,
) -> str:
    """
    Retrieve the transcript for a Teams meeting so that a MOM can be generated.

    Tries three methods in order:
      1. Graph API onlineMeetings transcripts endpoint (requires OnlineMeetings.Read scope)
      2. Search OneDrive Recordings folder for a .vtt file matching the meeting title/date
      3. Search SharePoint (for channel meetings) for the .vtt transcript file

    :param meeting_title: The meeting title (e.g. "ANVI For community - Sync")
    :param date: Optional date of the meeting as "YYYY-MM-DD". Defaults to yesterday.
    :param meeting_join_url: Optional Teams join URL from find_teams_meeting results for a faster lookup
    :return: JSON with transcript_text, source (API/OneDrive/SharePoint), and meeting metadata
    """
    if not __request__ or not __user__:
        return json.dumps({'error': 'Request context not available'})

    token = await _get_valid_token(__request__, __user__.get('id', ''))
    if not token:
        return json.dumps({'error': 'Microsoft account not connected.'})

    # Determine the target date
    now = datetime.now(timezone.utc)
    if date:
        try:
            target_date = datetime.fromisoformat(date).replace(tzinfo=timezone.utc)
        except ValueError:
            target_date = now - timedelta(days=1)
    else:
        target_date = now - timedelta(days=1)

    date_str = target_date.strftime('%Y-%m-%d')

    # ── Method 1: Graph API transcripts ───────────────────────────────────────
    if meeting_join_url:
        transcript_text = await _try_graph_transcript(token, meeting_join_url)
        if transcript_text:
            return json.dumps({
                'transcript': transcript_text,
                'source': 'Teams API',
                'meeting_title': meeting_title,
                'date': date_str,
            })

    # ── Method 2: OneDrive Recordings folder ──────────────────────────────────
    transcript_text = await _try_onedrive_vtt(token, meeting_title, date_str)
    if transcript_text:
        return json.dumps({
            'transcript': transcript_text,
            'source': 'OneDrive Recordings',
            'meeting_title': meeting_title,
            'date': date_str,
        })

    # ── Method 3: SharePoint channel Recordings ───────────────────────────────
    transcript_text = await _try_sharepoint_vtt(token, meeting_title, date_str)
    if transcript_text:
        return json.dumps({
            'transcript': transcript_text,
            'source': 'SharePoint Recordings',
            'meeting_title': meeting_title,
            'date': date_str,
        })

    return json.dumps({
        'transcript': None,
        'source': None,
        'meeting_title': meeting_title,
        'date': date_str,
        'message': (
            'No transcript found via the Teams API, OneDrive Recordings, or SharePoint. '
            'The meeting may not have been recorded, or the recording is still processing. '
            'To generate a MOM without a transcript, use the attendees from find_teams_meeting '
            'and any notes or messages from the meeting.'
        ),
    })


async def _try_graph_transcript(token: str, join_url: str) -> Optional[str]:
    """Try the Graph API onlineMeetings transcripts endpoint."""
    try:
        async with aiohttp.ClientSession() as session:
            # Resolve joinUrl → meeting ID
            encoded_url = join_url.replace('/', '%2F').replace(':', '%3A')
            async with session.get(
                f"{GRAPH_BASE}/me/onlineMeetings?$filter=joinWebUrl eq '{join_url}'",
                headers=_hdr(token),
                ssl=AIOHTTP_CLIENT_SESSION_SSL,
            ) as resp:
                if resp.status != 200:
                    return None
                meetings_data = await resp.json()

            meetings = meetings_data.get('value', [])
            if not meetings:
                return None

            meeting_id = meetings[0].get('id', '')
            if not meeting_id:
                return None

            # List transcripts for this meeting
            async with session.get(
                f'{GRAPH_BASE}/me/onlineMeetings/{meeting_id}/transcripts',
                headers=_hdr(token),
                ssl=AIOHTTP_CLIENT_SESSION_SSL,
            ) as resp:
                if resp.status != 200:
                    return None
                transcripts_data = await resp.json()

            transcripts = transcripts_data.get('value', [])
            if not transcripts:
                return None

            # Get the most recent transcript content
            transcript_id = transcripts[-1].get('id', '')
            async with session.get(
                f'{GRAPH_BASE}/me/onlineMeetings/{meeting_id}/transcripts/{transcript_id}/content?$format=text/vtt',
                headers={'Authorization': f'Bearer {token}'},
                ssl=AIOHTTP_CLIENT_SESSION_SSL,
            ) as resp:
                if resp.status != 200:
                    return None
                vtt_text = await resp.text()

        return _parse_vtt(vtt_text) or None

    except Exception as e:
        log.debug(f'Graph transcript API failed: {e}')
        return None


async def _try_onedrive_vtt(token: str, meeting_title: str, date_str: str) -> Optional[str]:
    """Search personal OneDrive Recordings folder for the .vtt transcript file."""
    try:
        title_keywords = meeting_title.lower().replace(' ', '+').replace('-', '+')
        date_compact = date_str.replace('-', '')  # 20260513

        async with aiohttp.ClientSession() as session:
            # Search OneDrive for .vtt files matching the meeting title
            search_url = (
                f"{GRAPH_BASE}/me/drive/root/search(q='{meeting_title}')"
                f"?$select=id,name,file,parentReference,lastModifiedDateTime,size"
                f"&$top=20"
            )
            async with session.get(search_url, headers=_hdr(token), ssl=AIOHTTP_CLIENT_SESSION_SSL) as resp:
                if resp.status != 200:
                    return None
                data = await resp.json()

            # Find .vtt files that match the date
            vtt_items = [
                item for item in data.get('value', [])
                if item.get('name', '').lower().endswith('.vtt')
                and (
                    date_str in item.get('lastModifiedDateTime', '')
                    or date_compact in item.get('name', '').replace('-', '').replace(' ', '')
                )
            ]

            if not vtt_items:
                # Broader: any .vtt in Recordings from the target date range
                for item in data.get('value', []):
                    name = item.get('name', '').lower()
                    modified = item.get('lastModifiedDateTime', '')
                    if name.endswith('.vtt') and date_str[:7] in modified:  # same month
                        vtt_items.append(item)

            if not vtt_items:
                return None

            # Download the first matching .vtt file
            item_id = vtt_items[0]['id']
            async with session.get(
                f'{GRAPH_BASE}/me/drive/items/{item_id}/content',
                headers={'Authorization': f'Bearer {token}'},
                ssl=AIOHTTP_CLIENT_SESSION_SSL,
            ) as resp:
                if resp.status != 200:
                    return None
                vtt_text = await resp.text()

        return _parse_vtt(vtt_text) or None

    except Exception as e:
        log.debug(f'OneDrive VTT search failed: {e}')
        return None


async def _try_sharepoint_vtt(token: str, meeting_title: str, date_str: str) -> Optional[str]:
    """Search SharePoint sites (for channel meetings) for the .vtt transcript file."""
    try:
        async with aiohttp.ClientSession() as session:
            # Get all joined teams
            async with session.get(
                f'{GRAPH_BASE}/me/joinedTeams?$select=id,displayName',
                headers=_hdr(token),
                ssl=AIOHTTP_CLIENT_SESSION_SSL,
            ) as resp:
                if resp.status != 200:
                    return None
                teams_data = await resp.json()

            for team in teams_data.get('value', [])[:10]:  # cap at 10 teams
                team_id = team.get('id', '')

                # Get the team's SharePoint site
                async with session.get(
                    f'{GRAPH_BASE}/groups/{team_id}/sites/root?$select=id',
                    headers=_hdr(token),
                    ssl=AIOHTTP_CLIENT_SESSION_SSL,
                ) as resp:
                    if resp.status != 200:
                        continue
                    site_data = await resp.json()

                site_id = site_data.get('id', '')
                if not site_id:
                    continue

                # Search this site's drive for the .vtt file
                search_url = (
                    f"{GRAPH_BASE}/sites/{site_id}/drive/root"
                    f"/search(q='{meeting_title}')"
                    f"?$select=id,name,file,lastModifiedDateTime&$top=20"
                )
                async with session.get(search_url, headers=_hdr(token), ssl=AIOHTTP_CLIENT_SESSION_SSL) as resp:
                    if resp.status != 200:
                        continue
                    sp_data = await resp.json()

                vtt_items = [
                    item for item in sp_data.get('value', [])
                    if item.get('name', '').lower().endswith('.vtt')
                ]

                if not vtt_items:
                    continue

                # Filter by date if possible, otherwise take the latest
                dated = [
                    i for i in vtt_items
                    if date_str in i.get('lastModifiedDateTime', '')
                ]
                target = dated[0] if dated else vtt_items[0]

                async with session.get(
                    f"{GRAPH_BASE}/sites/{site_id}/drive/items/{target['id']}/content",
                    headers={'Authorization': f'Bearer {token}'},
                    ssl=AIOHTTP_CLIENT_SESSION_SSL,
                ) as resp:
                    if resp.status != 200:
                        continue
                    vtt_text = await resp.text()

                parsed = _parse_vtt(vtt_text)
                if parsed:
                    return parsed

        return None

    except Exception as e:
        log.debug(f'SharePoint VTT search failed: {e}')
        return None


# ─── Tool 3: get_meeting_recordings ───────────────────────────────────────────

async def get_meeting_recordings(
    meeting_join_url: str,
    __request__: Request = None,
    __user__: dict = None,
) -> str:
    """
    List recordings available for a Teams meeting.

    :param meeting_join_url: The Teams meeting join URL (from find_teams_meeting results)
    :return: JSON list of recordings with id, createdDateTime, and a downloadable content URL
    """
    if not __request__ or not __user__:
        return json.dumps({'error': 'Request context not available'})

    token = await _get_valid_token(__request__, __user__.get('id', ''))
    if not token:
        return json.dumps({'error': 'Microsoft account not connected.'})

    try:
        async with aiohttp.ClientSession() as session:
            # Resolve joinUrl → meeting ID
            async with session.get(
                f"{GRAPH_BASE}/me/onlineMeetings?$filter=joinWebUrl eq '{meeting_join_url}'",
                headers=_hdr(token),
                ssl=AIOHTTP_CLIENT_SESSION_SSL,
            ) as resp:
                if resp.status != 200:
                    return json.dumps({'error': f'Could not resolve meeting: {resp.status}'})
                meetings_data = await resp.json()

            meetings = meetings_data.get('value', [])
            if not meetings:
                return json.dumps({'error': 'No online meeting found for this join URL.'})

            meeting_id = meetings[0].get('id', '')

            # List recordings
            async with session.get(
                f'{GRAPH_BASE}/me/onlineMeetings/{meeting_id}/recordings',
                headers=_hdr(token),
                ssl=AIOHTTP_CLIENT_SESSION_SSL,
            ) as resp:
                if resp.status == 403:
                    return json.dumps({'error': 'OnlineMeetingRecording.Read.All scope required. Please reconnect your Microsoft account.'})
                if resp.status != 200:
                    err = await resp.text()
                    return json.dumps({'error': f'Recordings API error {resp.status}: {err[:200]}'})
                data = await resp.json()

        recordings = [
            {
                'id': r.get('id', ''),
                'created': r.get('createdDateTime', ''),
                'content_url': f'{GRAPH_BASE}/me/onlineMeetings/{meeting_id}/recordings/{r.get("id", "")}/content',
            }
            for r in data.get('value', [])
        ]

        if not recordings:
            return json.dumps({
                'recordings': [],
                'message': 'No recordings found for this meeting.',
            })

        return json.dumps({'recordings': recordings, 'count': len(recordings), 'meeting_id': meeting_id})

    except Exception as e:
        log.error(f'get_meeting_recordings error: {e}')
        return json.dumps({'error': str(e)})


# ─── Tool 4: get_meeting_ai_insights ──────────────────────────────────────────

async def get_meeting_ai_insights(
    meeting_join_url: str,
    __request__: Request = None,
    __user__: dict = None,
) -> str:
    """
    Retrieve AI-generated insights for a Teams meeting, including action items,
    follow-ups, and key discussion points generated by Microsoft Copilot.

    :param meeting_join_url: The Teams meeting join URL (from find_teams_meeting results)
    :return: JSON with AI insights including action items, mentions, and conversation segments
    """
    if not __request__ or not __user__:
        return json.dumps({'error': 'Request context not available'})

    token = await _get_valid_token(__request__, __user__.get('id', ''))
    if not token:
        return json.dumps({'error': 'Microsoft account not connected.'})

    try:
        async with aiohttp.ClientSession() as session:
            # Resolve joinUrl → meeting ID
            async with session.get(
                f"{GRAPH_BASE}/me/onlineMeetings?$filter=joinWebUrl eq '{meeting_join_url}'",
                headers=_hdr(token),
                ssl=AIOHTTP_CLIENT_SESSION_SSL,
            ) as resp:
                if resp.status != 200:
                    return json.dumps({'error': f'Could not resolve meeting: {resp.status}'})
                meetings_data = await resp.json()

            meetings = meetings_data.get('value', [])
            if not meetings:
                return json.dumps({'error': 'No online meeting found for this join URL.'})

            meeting_id = meetings[0].get('id', '')

            # Fetch AI insights
            async with session.get(
                f'{GRAPH_BASE}/me/onlineMeetings/{meeting_id}/aiInsights',
                headers=_hdr(token),
                ssl=AIOHTTP_CLIENT_SESSION_SSL,
            ) as resp:
                if resp.status == 403:
                    return json.dumps({'error': 'OnlineMeetingAiInsight.Read scope required. Please reconnect your Microsoft account.'})
                if resp.status == 404:
                    return json.dumps({'error': 'No AI insights available for this meeting. Copilot must have been enabled during the meeting.'})
                if resp.status != 200:
                    err = await resp.text()
                    return json.dumps({'error': f'AI Insights API error {resp.status}: {err[:200]}'})
                data = await resp.json()

        insights_list = data.get('value', [])
        if not insights_list:
            return json.dumps({'message': 'No AI insights found for this meeting.'})

        # Return all insight objects (action items, follow-ups, mentions, etc.)
        return json.dumps({
            'insights': insights_list,
            'count': len(insights_list),
            'meeting_id': meeting_id,
        })

    except Exception as e:
        log.error(f'get_meeting_ai_insights error: {e}')
        return json.dumps({'error': str(e)})
