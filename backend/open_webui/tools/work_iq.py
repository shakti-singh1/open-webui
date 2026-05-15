"""
Microsoft Work IQ tool for Open WebUI.

Work IQ is an AI-powered natural language query layer over Microsoft 365 data
(email, calendar, Teams messages, meetings, OneDrive/SharePoint, people).
It's the "ask anything about your M365 data" companion to the direct Graph API tools.

Becomes available automatically when:
  1. Admin has enabled ENABLE_WORK_IQ_INTEGRATION
  2. Admin has granted WorkIQAgent.Ask permission in the same Entra app registration
  3. User has connected their Microsoft account (the Work IQ token is fetched automatically
     during the existing Microsoft OAuth callback)

Requirements:
  - Microsoft 365 Copilot license per querying user
  - WorkIQAgent.Ask delegated permission on the Entra app
"""

import json
import logging
import time
import uuid
from typing import Optional

import aiohttp
from fastapi import Request

from open_webui.env import AIOHTTP_CLIENT_SESSION_SSL

log = logging.getLogger(__name__)

WORK_IQ_PROVIDER = 'workiq_integration'
WORK_IQ_BASE = 'https://workiq.svc.cloud.microsoft/a2a/'


# ─── Token helper ─────────────────────────────────────────────────────────────


async def _get_work_iq_token(request: Request, user_id: str) -> Optional[str]:
    """Return a valid (possibly refreshed) Work IQ access token for the user."""
    from open_webui.models.oauth_sessions import OAuthSessions
    from open_webui.routers.integrations import refresh_work_iq_token

    session = await OAuthSessions.get_session_by_provider_and_user_id(WORK_IQ_PROVIDER, user_id)
    if not session:
        return None

    if session.expires_at - int(time.time()) < 300:
        updated = await refresh_work_iq_token(request.app.state.config, session)
        return updated.get('access_token') if updated else None

    return session.token.get('access_token')


def _tz_offset(tz_name: str) -> int:
    """Return UTC offset in minutes for the given IANA timezone name."""
    try:
        from zoneinfo import ZoneInfo
        from datetime import datetime
        now = datetime.now(ZoneInfo(tz_name))
        return int(now.utcoffset().total_seconds() / 60)
    except Exception:
        return 0


# ─── Work IQ tool ─────────────────────────────────────────────────────────────


async def query_work_iq(
    question: str,
    timezone: str = 'UTC',
    context_id: Optional[str] = None,
    __request__: Request = None,
    __user__: dict = None,
) -> str:
    """
    Ask a natural language question about your Microsoft 365 data — email, calendar,
    Teams messages, meetings, OneDrive/SharePoint documents, and organizational context.

    Work IQ uses AI to understand intent and returns enriched, context-aware answers
    with automatic permission enforcement. Use it for complex queries that span
    multiple data sources, or wherever a smart summary is more useful than raw data.

    Examples:
      - "Summarize my unread emails from today"
      - "What action items came out of the ANVI For community Sync meeting?"
      - "Find all documents related to Project Falcon in SharePoint"
      - "What meetings do I have this week and who is organizing them?"
      - "Draft a MOM for yesterday's product review meeting"
      - "Who in my organization works on machine learning?"

    :param question: Natural language question about your Microsoft 365 data
    :param timezone: IANA timezone name for time-relative queries, e.g. "Asia/Kolkata",
                     "America/New_York", "Europe/London" (default: UTC)
    :param context_id: Context ID from a previous query_work_iq response to continue
                       a multi-turn conversation (e.g. ask follow-up questions)
    :return: JSON with answer text, citations, source, and context_id for follow-ups
    """
    if not __request__ or not __user__:
        return json.dumps({'error': 'Request context not available'})

    token = await _get_work_iq_token(__request__, __user__.get('id', ''))
    if not token:
        return json.dumps({
            'error': (
                'Work IQ not connected. This can happen if: (1) ENABLE_WORK_IQ_INTEGRATION is off, '
                '(2) WorkIQAgent.Ask permission was not granted in Entra, or '
                '(3) you need to reconnect your Microsoft account via Settings → Integrations.'
            )
        })

    payload = {
        'jsonrpc': '2.0',
        'id': str(uuid.uuid4()),
        'method': 'SendMessage',
        'params': {
            'message': {
                'role': 'ROLE_USER',
                'messageId': str(uuid.uuid4()),
                'parts': [{'text': question}],
                'metadata': {
                    'Location': {
                        'timeZone': timezone,
                        'timeZoneOffset': _tz_offset(timezone),
                    }
                },
            }
        },
    }

    if context_id:
        payload['params']['contextId'] = context_id

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                WORK_IQ_BASE,
                headers={
                    'Authorization': f'Bearer {token}',
                    'Content-Type': 'application/json',
                    'A2A-Version': '1.0',
                },
                json=payload,
                ssl=AIOHTTP_CLIENT_SESSION_SSL,
            ) as resp:
                if resp.status == 401:
                    return json.dumps({'error': 'Work IQ token expired. Please reconnect your Microsoft account via Settings → Integrations.'})
                if resp.status == 403:
                    return json.dumps({'error': 'Work IQ access denied. Ensure you have a Microsoft 365 Copilot license and WorkIQAgent.Ask permission is granted.'})
                if resp.status != 200:
                    err = await resp.text()
                    return json.dumps({'error': f'Work IQ API error {resp.status}: {err[:300]}'})
                data = await resp.json()

        # Extract answer from A2A response envelope
        task = data.get('result', {}).get('task', {})
        returned_context_id = task.get('contextId', '')
        state = task.get('status', {}).get('state', '')

        if state == 'TASK_STATE_FAILED':
            return json.dumps({'error': 'Work IQ query failed. Please try rephrasing your question.'})

        # Collect text parts from all artifacts
        answer_parts = []
        citations = []
        for artifact in task.get('artifacts', []):
            for part in artifact.get('parts', []):
                text = part.get('text', '').strip()
                if text:
                    answer_parts.append(text)
            # Citations may appear as artifact metadata or separate parts
            for citation in artifact.get('citations', []):
                citations.append({
                    'title': citation.get('title', ''),
                    'url': citation.get('url', ''),
                })

        answer = '\n\n'.join(answer_parts) if answer_parts else 'No answer returned from Work IQ.'

        return json.dumps({
            'answer': answer,
            'context_id': returned_context_id,
            'citations': citations,
            'source': 'Microsoft Work IQ',
        })

    except Exception as e:
        log.error(f'query_work_iq error: {e}')
        return json.dumps({'error': str(e)})
