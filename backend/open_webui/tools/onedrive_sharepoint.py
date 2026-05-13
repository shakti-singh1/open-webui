"""
OneDrive and SharePoint tools for Open WebUI.

These are builtin tools that become available automatically when:
  1. Admin has enabled ENABLE_MICROSOFT_TEAMS_INTEGRATION
  2. The current user has connected their Microsoft account via Settings → Integrations

They reuse the same microsoft_teams_integration OAuth token — no separate
OAuth app or connection is required. The token must include Files.Read,
Files.ReadWrite, and Sites.Read.All scopes (added in the connect flow).

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


# ─── Token helper (shared with microsoft_integration.py) ─────────────────────


async def _get_valid_token(request: Request, user_id: str) -> Optional[str]:
    from open_webui.models.oauth_sessions import OAuthSessions
    from open_webui.routers.integrations import refresh_microsoft_token

    session = await OAuthSessions.get_session_by_provider_and_user_id(MICROSOFT_PROVIDER, user_id)
    if not session:
        return None

    if session.expires_at - int(time.time()) < 300:
        updated = await refresh_microsoft_token(request.app.state.config, session)
        if updated:
            return updated.get('access_token')
        return None

    return session.token.get('access_token')


def _headers(token: str) -> dict:
    return {'Authorization': f'Bearer {token}', 'Content-Type': 'application/json'}


# ─── OneDrive tools ───────────────────────────────────────────────────────────


async def list_onedrive_files(
    folder_path: str = 'root',
    count: int = 20,
    __request__: Request = None,
    __user__: dict = None,
) -> str:
    """
    List files and folders in the user's OneDrive.

    :param folder_path: Path to list. Use "root" for the top level, or a path like "Documents" or "Documents/Reports" (default: root)
    :param count: Maximum number of items to return (default: 20, max: 100)
    :return: JSON list of items with id, name, type (file/folder), size, lastModifiedDateTime, and webUrl
    """
    if not __request__ or not __user__:
        return json.dumps({'error': 'Request context not available'})

    token = await _get_valid_token(__request__, __user__.get('id', ''))
    if not token:
        return json.dumps({'error': 'Microsoft account not connected. Please connect via Settings → Integrations.'})

    count = min(max(1, int(count)), 100)

    if folder_path in ('root', '', '/'):
        url = f'{GRAPH_BASE}/me/drive/root/children?$top={count}&$select=id,name,file,folder,size,lastModifiedDateTime,webUrl'
    else:
        encoded = folder_path.strip('/')
        url = f'{GRAPH_BASE}/me/drive/root:/{encoded}:/children?$top={count}&$select=id,name,file,folder,size,lastModifiedDateTime,webUrl'

    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, headers=_headers(token), ssl=AIOHTTP_CLIENT_SESSION_SSL) as resp:
                if resp.status == 401:
                    return json.dumps({'error': 'Microsoft token expired. Please reconnect via Settings → Integrations.'})
                if resp.status != 200:
                    err = await resp.text()
                    return json.dumps({'error': f'Graph API error {resp.status}: {err[:200]}'})
                data = await resp.json()

        items = []
        for item in data.get('value', []):
            items.append({
                'id': item.get('id', ''),
                'name': item.get('name', ''),
                'type': 'folder' if 'folder' in item else 'file',
                'size_bytes': item.get('size', 0),
                'modified': item.get('lastModifiedDateTime', ''),
                'url': item.get('webUrl', ''),
            })

        return json.dumps({'items': items, 'folder': folder_path, 'count': len(items)})

    except Exception as e:
        log.error(f'list_onedrive_files error: {e}')
        return json.dumps({'error': str(e)})


async def search_onedrive(
    query: str,
    count: int = 15,
    __request__: Request = None,
    __user__: dict = None,
) -> str:
    """
    Search for files and folders across the user's entire OneDrive.

    :param query: Search keywords (filename, content, or metadata)
    :param count: Maximum number of results to return (default: 15, max: 50)
    :return: JSON list of matching items with id, name, path, size, lastModifiedDateTime, and webUrl
    """
    if not __request__ or not __user__:
        return json.dumps({'error': 'Request context not available'})

    token = await _get_valid_token(__request__, __user__.get('id', ''))
    if not token:
        return json.dumps({'error': 'Microsoft account not connected.'})

    count = min(max(1, int(count)), 50)
    url = f'{GRAPH_BASE}/me/drive/root/search(q=\'{query}\')?$top={count}&$select=id,name,file,folder,size,lastModifiedDateTime,webUrl,parentReference'

    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, headers=_headers(token), ssl=AIOHTTP_CLIENT_SESSION_SSL) as resp:
                if resp.status != 200:
                    err = await resp.text()
                    return json.dumps({'error': f'Graph API error {resp.status}: {err[:200]}'})
                data = await resp.json()

        items = []
        for item in data.get('value', []):
            parent_path = item.get('parentReference', {}).get('path', '').replace('/drive/root:', '')
            items.append({
                'id': item.get('id', ''),
                'name': item.get('name', ''),
                'type': 'folder' if 'folder' in item else 'file',
                'path': f'{parent_path}/{item.get("name", "")}',
                'size_bytes': item.get('size', 0),
                'modified': item.get('lastModifiedDateTime', ''),
                'url': item.get('webUrl', ''),
            })

        return json.dumps({'items': items, 'query': query, 'count': len(items)})

    except Exception as e:
        log.error(f'search_onedrive error: {e}')
        return json.dumps({'error': str(e)})


async def read_onedrive_file(
    item_id: str,
    __request__: Request = None,
    __user__: dict = None,
) -> str:
    """
    Read the text content of a file stored in OneDrive.
    Works best with plain text, markdown, CSV, and simple Office documents.

    :param item_id: The OneDrive item ID (from list_onedrive_files or search_onedrive)
    :return: JSON with filename, mime type, size, and extracted text content (up to 8000 chars)
    """
    if not __request__ or not __user__:
        return json.dumps({'error': 'Request context not available'})

    token = await _get_valid_token(__request__, __user__.get('id', ''))
    if not token:
        return json.dumps({'error': 'Microsoft account not connected.'})

    try:
        async with aiohttp.ClientSession() as session:
            # Get item metadata
            async with session.get(
                f'{GRAPH_BASE}/me/drive/items/{item_id}?$select=name,file,size',
                headers=_headers(token),
                ssl=AIOHTTP_CLIENT_SESSION_SSL,
            ) as resp:
                if resp.status != 200:
                    return json.dumps({'error': f'Item not found: {resp.status}'})
                meta = await resp.json()

            mime = meta.get('file', {}).get('mimeType', '')
            name = meta.get('name', '')
            size = meta.get('size', 0)

            # Only attempt text extraction for text-based types
            text_mimes = {'text/', 'application/json', 'application/xml', 'application/csv'}
            is_text = any(mime.startswith(t) for t in text_mimes)

            if not is_text:
                return json.dumps({
                    'name': name,
                    'mime_type': mime,
                    'size_bytes': size,
                    'content': None,
                    'note': f'Binary file ({mime}). Download the file to view its contents.',
                    'url': f'{GRAPH_BASE}/me/drive/items/{item_id}/content',
                })

            # Download content
            async with session.get(
                f'{GRAPH_BASE}/me/drive/items/{item_id}/content',
                headers={'Authorization': f'Bearer {token}'},
                ssl=AIOHTTP_CLIENT_SESSION_SSL,
            ) as resp:
                if resp.status != 200:
                    return json.dumps({'error': f'Could not download file: {resp.status}'})
                raw = await resp.read()

        text = raw.decode('utf-8', errors='replace')[:8000]
        return json.dumps({'name': name, 'mime_type': mime, 'size_bytes': size, 'content': text})

    except Exception as e:
        log.error(f'read_onedrive_file error: {e}')
        return json.dumps({'error': str(e)})


async def get_onedrive_file_link(
    item_id: str,
    link_type: str = 'view',
    __request__: Request = None,
    __user__: dict = None,
) -> str:
    """
    Generate a shareable link for a OneDrive file.

    :param item_id: The OneDrive item ID (from list_onedrive_files or search_onedrive)
    :param link_type: Type of link — "view" (read-only) or "edit" (read-write) (default: view)
    :return: JSON with the shareable link URL and expiry info
    """
    if not __request__ or not __user__:
        return json.dumps({'error': 'Request context not available'})

    token = await _get_valid_token(__request__, __user__.get('id', ''))
    if not token:
        return json.dumps({'error': 'Microsoft account not connected.'})

    scope = 'anonymous' if link_type == 'view' else 'organization'
    payload = {'type': link_type, 'scope': scope}

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                f'{GRAPH_BASE}/me/drive/items/{item_id}/createLink',
                headers=_headers(token),
                json=payload,
                ssl=AIOHTTP_CLIENT_SESSION_SSL,
            ) as resp:
                if resp.status not in (200, 201):
                    err = await resp.text()
                    return json.dumps({'error': f'Could not create link: {err[:200]}'})
                data = await resp.json()

        link = data.get('link', {})
        return json.dumps({
            'url': link.get('webUrl', ''),
            'type': link.get('type', link_type),
            'scope': link.get('scope', scope),
        })

    except Exception as e:
        log.error(f'get_onedrive_file_link error: {e}')
        return json.dumps({'error': str(e)})


# ─── SharePoint tools ─────────────────────────────────────────────────────────


async def list_sharepoint_sites(
    count: int = 20,
    __request__: Request = None,
    __user__: dict = None,
) -> str:
    """
    List SharePoint sites the user has access to in their organization.

    :param count: Maximum number of sites to return (default: 20, max: 50)
    :return: JSON list of sites with id, displayName, description, and webUrl
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
                f'{GRAPH_BASE}/sites?search=*&$top={count}&$select=id,displayName,description,webUrl',
                headers=_headers(token),
                ssl=AIOHTTP_CLIENT_SESSION_SSL,
            ) as resp:
                if resp.status != 200:
                    err = await resp.text()
                    return json.dumps({'error': f'Graph API error {resp.status}: {err[:200]}'})
                data = await resp.json()

        sites = [
            {
                'id': s.get('id', ''),
                'name': s.get('displayName', ''),
                'description': s.get('description', ''),
                'url': s.get('webUrl', ''),
            }
            for s in data.get('value', [])
        ]
        return json.dumps({'sites': sites, 'count': len(sites)})

    except Exception as e:
        log.error(f'list_sharepoint_sites error: {e}')
        return json.dumps({'error': str(e)})


async def search_sharepoint(
    query: str,
    site_id: Optional[str] = None,
    count: int = 15,
    __request__: Request = None,
    __user__: dict = None,
) -> str:
    """
    Search for documents and pages across SharePoint sites.

    :param query: Search keywords to find documents, pages, or list items
    :param site_id: Optional site ID to scope the search to a single site (from list_sharepoint_sites). Searches all sites if omitted.
    :param count: Maximum number of results to return (default: 15, max: 50)
    :return: JSON list of results with name, type, path, lastModifiedDateTime, and webUrl
    """
    if not __request__ or not __user__:
        return json.dumps({'error': 'Request context not available'})

    token = await _get_valid_token(__request__, __user__.get('id', ''))
    if not token:
        return json.dumps({'error': 'Microsoft account not connected.'})

    count = min(max(1, int(count)), 50)

    if site_id:
        url = f'{GRAPH_BASE}/sites/{site_id}/drive/root/search(q=\'{query}\')?$top={count}&$select=id,name,file,folder,size,lastModifiedDateTime,webUrl,parentReference'
    else:
        url = f'{GRAPH_BASE}/sites?search={query}&$top={count}&$select=id,displayName,description,webUrl'

    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, headers=_headers(token), ssl=AIOHTTP_CLIENT_SESSION_SSL) as resp:
                if resp.status != 200:
                    err = await resp.text()
                    return json.dumps({'error': f'Graph API error {resp.status}: {err[:200]}'})
                data = await resp.json()

        if site_id:
            # File search results
            items = []
            for item in data.get('value', []):
                parent_path = item.get('parentReference', {}).get('path', '').replace('/drive/root:', '')
                items.append({
                    'id': item.get('id', ''),
                    'name': item.get('name', ''),
                    'type': 'folder' if 'folder' in item else 'file',
                    'path': f'{parent_path}/{item.get("name", "")}',
                    'modified': item.get('lastModifiedDateTime', ''),
                    'url': item.get('webUrl', ''),
                })
            return json.dumps({'items': items, 'query': query, 'site_id': site_id, 'count': len(items)})
        else:
            # Site search results
            sites = [
                {
                    'id': s.get('id', ''),
                    'name': s.get('displayName', ''),
                    'description': s.get('description', ''),
                    'url': s.get('webUrl', ''),
                }
                for s in data.get('value', [])
            ]
            return json.dumps({'sites': sites, 'query': query, 'count': len(sites)})

    except Exception as e:
        log.error(f'search_sharepoint error: {e}')
        return json.dumps({'error': str(e)})


async def list_sharepoint_site_files(
    site_id: str,
    folder_path: str = 'root',
    count: int = 20,
    __request__: Request = None,
    __user__: dict = None,
) -> str:
    """
    List files and folders in a SharePoint site's document library.

    :param site_id: The SharePoint site ID (from list_sharepoint_sites)
    :param folder_path: Folder path within the site's drive. Use "root" for the top level (default: root)
    :param count: Maximum number of items to return (default: 20, max: 100)
    :return: JSON list of items with id, name, type, size, lastModifiedDateTime, and webUrl
    """
    if not __request__ or not __user__:
        return json.dumps({'error': 'Request context not available'})

    token = await _get_valid_token(__request__, __user__.get('id', ''))
    if not token:
        return json.dumps({'error': 'Microsoft account not connected.'})

    count = min(max(1, int(count)), 100)
    select = 'id,name,file,folder,size,lastModifiedDateTime,webUrl'

    if folder_path in ('root', '', '/'):
        url = f'{GRAPH_BASE}/sites/{site_id}/drive/root/children?$top={count}&$select={select}'
    else:
        encoded = folder_path.strip('/')
        url = f'{GRAPH_BASE}/sites/{site_id}/drive/root:/{encoded}:/children?$top={count}&$select={select}'

    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, headers=_headers(token), ssl=AIOHTTP_CLIENT_SESSION_SSL) as resp:
                if resp.status != 200:
                    err = await resp.text()
                    return json.dumps({'error': f'Graph API error {resp.status}: {err[:200]}'})
                data = await resp.json()

        items = [
            {
                'id': item.get('id', ''),
                'name': item.get('name', ''),
                'type': 'folder' if 'folder' in item else 'file',
                'size_bytes': item.get('size', 0),
                'modified': item.get('lastModifiedDateTime', ''),
                'url': item.get('webUrl', ''),
            }
            for item in data.get('value', [])
        ]
        return json.dumps({'items': items, 'site_id': site_id, 'folder': folder_path, 'count': len(items)})

    except Exception as e:
        log.error(f'list_sharepoint_site_files error: {e}')
        return json.dumps({'error': str(e)})


async def read_sharepoint_file(
    site_id: str,
    item_id: str,
    __request__: Request = None,
    __user__: dict = None,
) -> str:
    """
    Read the text content of a file stored in a SharePoint site's document library.
    Works best with plain text, markdown, CSV files.

    :param site_id: The SharePoint site ID (from list_sharepoint_sites)
    :param item_id: The item ID (from list_sharepoint_site_files or search_sharepoint)
    :return: JSON with filename, mime type, size, and extracted text content (up to 8000 chars)
    """
    if not __request__ or not __user__:
        return json.dumps({'error': 'Request context not available'})

    token = await _get_valid_token(__request__, __user__.get('id', ''))
    if not token:
        return json.dumps({'error': 'Microsoft account not connected.'})

    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                f'{GRAPH_BASE}/sites/{site_id}/drive/items/{item_id}?$select=name,file,size',
                headers=_headers(token),
                ssl=AIOHTTP_CLIENT_SESSION_SSL,
            ) as resp:
                if resp.status != 200:
                    return json.dumps({'error': f'Item not found: {resp.status}'})
                meta = await resp.json()

            mime = meta.get('file', {}).get('mimeType', '')
            name = meta.get('name', '')
            size = meta.get('size', 0)

            text_mimes = {'text/', 'application/json', 'application/xml', 'application/csv'}
            is_text = any(mime.startswith(t) for t in text_mimes)

            if not is_text:
                return json.dumps({
                    'name': name,
                    'mime_type': mime,
                    'size_bytes': size,
                    'content': None,
                    'note': f'Binary file ({mime}). Use the webUrl to open it in SharePoint.',
                })

            async with session.get(
                f'{GRAPH_BASE}/sites/{site_id}/drive/items/{item_id}/content',
                headers={'Authorization': f'Bearer {token}'},
                ssl=AIOHTTP_CLIENT_SESSION_SSL,
            ) as resp:
                if resp.status != 200:
                    return json.dumps({'error': f'Could not download file: {resp.status}'})
                raw = await resp.read()

        text = raw.decode('utf-8', errors='replace')[:8000]
        return json.dumps({'name': name, 'mime_type': mime, 'size_bytes': size, 'content': text})

    except Exception as e:
        log.error(f'read_sharepoint_file error: {e}')
        return json.dumps({'error': str(e)})


async def get_sharepoint_list_items(
    site_id: str,
    list_name: str,
    count: int = 20,
    __request__: Request = None,
    __user__: dict = None,
) -> str:
    """
    Retrieve items from a SharePoint list (custom list, tasks list, announcements, etc.).

    :param site_id: The SharePoint site ID (from list_sharepoint_sites)
    :param list_name: The display name of the SharePoint list (e.g. "Tasks", "Announcements", "Project Tracker")
    :param count: Maximum number of items to return (default: 20, max: 100)
    :return: JSON list of list items with their field values
    """
    if not __request__ or not __user__:
        return json.dumps({'error': 'Request context not available'})

    token = await _get_valid_token(__request__, __user__.get('id', ''))
    if not token:
        return json.dumps({'error': 'Microsoft account not connected.'})

    count = min(max(1, int(count)), 100)

    try:
        async with aiohttp.ClientSession() as session:
            url = f'{GRAPH_BASE}/sites/{site_id}/lists/{list_name}/items?$expand=fields&$top={count}'
            async with session.get(url, headers=_headers(token), ssl=AIOHTTP_CLIENT_SESSION_SSL) as resp:
                if resp.status != 200:
                    err = await resp.text()
                    return json.dumps({'error': f'Graph API error {resp.status}: {err[:200]}'})
                data = await resp.json()

        items = []
        for item in data.get('value', []):
            fields = item.get('fields', {})
            # Remove internal SharePoint metadata fields
            clean_fields = {k: v for k, v in fields.items() if not k.startswith('@') and not k.startswith('_')}
            items.append({
                'id': item.get('id', ''),
                'created': item.get('createdDateTime', ''),
                'modified': item.get('lastModifiedDateTime', ''),
                'fields': clean_fields,
            })

        return json.dumps({'items': items, 'list': list_name, 'site_id': site_id, 'count': len(items)})

    except Exception as e:
        log.error(f'get_sharepoint_list_items error: {e}')
        return json.dumps({'error': str(e)})
