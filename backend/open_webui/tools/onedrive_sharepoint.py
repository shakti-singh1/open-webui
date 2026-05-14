"""
OneDrive and SharePoint tools for Open WebUI.

These are builtin tools that become available automatically when:
  1. Admin has enabled ENABLE_MICROSOFT_TEAMS_INTEGRATION
  2. The current user has connected their Microsoft account via Settings → Integrations

They reuse the same microsoft_teams_integration OAuth token — no separate
OAuth app or connection is required. The token must include Files.Read,
Files.ReadWrite, and Sites.Read.All scopes (added in the connect flow).

Supported file formats for content extraction:
  - Excel (.xlsx, .xls)     → Microsoft Graph Workbook REST API (no library needed)
  - Word (.docx)            → zipfile + ElementTree (Python stdlib)
  - PowerPoint (.pptx)      → zipfile + ElementTree (Python stdlib)
  - PDF (.pdf)              → pdfminer.six (if installed)
  - Plain text / CSV / JSON / XML / Markdown → direct UTF-8 decode

Functions receive __request__ and __user__ injected at runtime; they are
never exposed to the LLM as required parameters.
"""

import io
import json
import logging
import time
import zipfile
import xml.etree.ElementTree as ET
from typing import Optional

import aiohttp
from fastapi import Request

from open_webui.env import AIOHTTP_CLIENT_SESSION_SSL

log = logging.getLogger(__name__)

MICROSOFT_PROVIDER = 'microsoft_teams_integration'
GRAPH_BASE = 'https://graph.microsoft.com/v1.0'

# Office XML namespaces
_W_NS = 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'
_A_NS = 'http://schemas.openxmlformats.org/drawingml/2006/main'


# ─── Token helper ─────────────────────────────────────────────────────────────


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


# ─── Content extraction helpers ───────────────────────────────────────────────


async def _fetch_excel_content(token: str, workbook_url_base: str) -> Optional[str]:
    """
    Read Excel content via the Graph Workbook REST API.
    workbook_url_base is e.g. /me/drive/items/{id} or /sites/{sid}/drive/items/{id}
    Returns formatted text or None on failure.
    """
    try:
        async with aiohttp.ClientSession() as session:
            # List worksheets
            async with session.get(
                f'{GRAPH_BASE}{workbook_url_base}/workbook/worksheets',
                headers=_headers(token),
                ssl=AIOHTTP_CLIENT_SESSION_SSL,
            ) as resp:
                if resp.status != 200:
                    return None
                sheets_data = await resp.json()

            sheets = sheets_data.get('value', [])
            if not sheets:
                return None

            parts = []
            for sheet in sheets[:10]:  # cap at 10 sheets
                sheet_name = sheet.get('name', 'Sheet')
                async with session.get(
                    f'{GRAPH_BASE}{workbook_url_base}/workbook/worksheets/{sheet_name}/usedRange?$select=values',
                    headers=_headers(token),
                    ssl=AIOHTTP_CLIENT_SESSION_SSL,
                ) as resp:
                    if resp.status != 200:
                        continue
                    range_data = await resp.json()

                values = range_data.get('values', [])
                if not values:
                    continue

                parts.append(f'=== Sheet: {sheet_name} ===')
                for row in values:
                    cell_strs = [str(cell) if cell is not None else '' for cell in row]
                    parts.append('\t'.join(cell_strs))

            return '\n'.join(parts) if parts else None

    except Exception as e:
        log.warning(f'Excel Workbook API extraction failed: {e}')
        return None


def _extract_docx_text(raw: bytes) -> Optional[str]:
    """Extract text from a Word .docx file using stdlib zipfile + ElementTree."""
    try:
        with zipfile.ZipFile(io.BytesIO(raw)) as z:
            with z.open('word/document.xml') as f:
                tree = ET.parse(f)

        root = tree.getroot()
        paragraphs = []
        for para in root.iter(f'{{{_W_NS}}}p'):
            texts = [el.text for el in para.iter(f'{{{_W_NS}}}t') if el.text]
            if texts:
                paragraphs.append(''.join(texts))

        return '\n'.join(paragraphs)
    except Exception as e:
        log.warning(f'Word extraction failed: {e}')
        return None


def _extract_pptx_text(raw: bytes) -> Optional[str]:
    """Extract text from a PowerPoint .pptx file using stdlib zipfile + ElementTree."""
    try:
        with zipfile.ZipFile(io.BytesIO(raw)) as z:
            slide_files = sorted(
                n for n in z.namelist()
                if n.startswith('ppt/slides/slide') and n.endswith('.xml')
            )
            all_texts = []
            for i, slide_file in enumerate(slide_files, 1):
                with z.open(slide_file) as f:
                    root = ET.parse(f).getroot()
                texts = [el.text for el in root.iter(f'{{{_A_NS}}}t') if el.text and el.text.strip()]
                if texts:
                    all_texts.append(f'--- Slide {i} ---')
                    all_texts.extend(texts)

        return '\n'.join(all_texts)
    except Exception as e:
        log.warning(f'PowerPoint extraction failed: {e}')
        return None


def _extract_pdf_text(raw: bytes) -> Optional[str]:
    """Extract text from a PDF using pdfminer.six (if installed)."""
    try:
        from pdfminer.high_level import extract_text_to_fp
        from pdfminer.layout import LAParams

        output = io.StringIO()
        extract_text_to_fp(io.BytesIO(raw), output, laparams=LAParams(), output_type='text', codec='utf-8')
        return output.getvalue()
    except ImportError:
        pass

    try:
        import pdfplumber
        with pdfplumber.open(io.BytesIO(raw)) as pdf:
            return '\n'.join(page.extract_text() or '' for page in pdf.pages)
    except ImportError:
        pass

    return None


async def _extract_content(
    token: str,
    raw: bytes,
    mime: str,
    name: str,
    workbook_url_base: Optional[str],
) -> tuple[Optional[str], str]:
    """
    Extract text content from a file.
    Returns (text_or_None, extraction_method_note).
    """
    lower_name = name.lower()

    # ── Excel ──────────────────────────────────────────────────────────────────
    is_excel = (
        'spreadsheetml' in mime
        or lower_name.endswith('.xlsx')
        or lower_name.endswith('.xls')
        or lower_name.endswith('.xlsm')
    )
    if is_excel and workbook_url_base:
        text = await _fetch_excel_content(token, workbook_url_base)
        if text:
            return text, 'Excel Workbook API'

    # ── Word ───────────────────────────────────────────────────────────────────
    is_word = (
        'wordprocessingml' in mime
        or lower_name.endswith('.docx')
        or lower_name.endswith('.doc')
    )
    if is_word:
        text = _extract_docx_text(raw)
        if text:
            return text, 'Word XML extraction'

    # ── PowerPoint ─────────────────────────────────────────────────────────────
    is_pptx = (
        'presentationml' in mime
        or lower_name.endswith('.pptx')
        or lower_name.endswith('.ppt')
    )
    if is_pptx:
        text = _extract_pptx_text(raw)
        if text:
            return text, 'PowerPoint XML extraction'

    # ── PDF ────────────────────────────────────────────────────────────────────
    if 'pdf' in mime or lower_name.endswith('.pdf'):
        text = _extract_pdf_text(raw)
        if text:
            return text, 'PDF extraction'

    # ── Plain text / CSV / JSON / XML / Markdown ───────────────────────────────
    text_mimes = ('text/', 'application/json', 'application/xml', 'application/csv')
    text_exts = ('.txt', '.md', '.csv', '.json', '.xml', '.yaml', '.yml', '.log', '.ini', '.toml')
    if any(mime.startswith(t) for t in text_mimes) or any(lower_name.endswith(e) for e in text_exts):
        return raw.decode('utf-8', errors='replace'), 'plain text'

    return None, 'unsupported format'


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
    url = f"{GRAPH_BASE}/me/drive/root/search(q='{query}')?$top={count}&$select=id,name,file,folder,size,lastModifiedDateTime,webUrl,parentReference"

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
    Read the content of a file stored in OneDrive.
    Supports Excel (.xlsx), Word (.docx), PowerPoint (.pptx), PDF, and plain text files.
    Excel files are read via the Graph Workbook API — all data across all sheets is returned.

    :param item_id: The OneDrive item ID (from list_onedrive_files or search_onedrive)
    :return: JSON with filename, mime type, size, and extracted text content (up to 12000 chars)
    """
    if not __request__ or not __user__:
        return json.dumps({'error': 'Request context not available'})

    token = await _get_valid_token(__request__, __user__.get('id', ''))
    if not token:
        return json.dumps({'error': 'Microsoft account not connected.'})

    workbook_base = f'/me/drive/items/{item_id}'

    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                f'{GRAPH_BASE}{workbook_base}?$select=name,file,size',
                headers=_headers(token),
                ssl=AIOHTTP_CLIENT_SESSION_SSL,
            ) as resp:
                if resp.status != 200:
                    return json.dumps({'error': f'Item not found (status {resp.status})'})
                meta = await resp.json()

            mime = meta.get('file', {}).get('mimeType', '')
            name = meta.get('name', '')
            size = meta.get('size', 0)

            # Download binary (needed for Word/PowerPoint/PDF/text; skipped for Excel via API)
            raw = b''
            lower_name = name.lower()
            needs_download = not (
                'spreadsheetml' in mime
                or lower_name.endswith('.xlsx')
                or lower_name.endswith('.xls')
                or lower_name.endswith('.xlsm')
            )
            if needs_download:
                async with session.get(
                    f'{GRAPH_BASE}{workbook_base}/content',
                    headers={'Authorization': f'Bearer {token}'},
                    ssl=AIOHTTP_CLIENT_SESSION_SSL,
                ) as dl_resp:
                    if dl_resp.status != 200:
                        return json.dumps({'error': f'Could not download file (status {dl_resp.status})'})
                    raw = await dl_resp.read()

        text, method = await _extract_content(token, raw, mime, name, workbook_base)

        if text is None:
            return json.dumps({
                'name': name,
                'mime_type': mime,
                'size_bytes': size,
                'content': None,
                'note': f'Cannot extract text from this file type ({mime}). Open it directly via its OneDrive URL.',
            })

        return json.dumps({
            'name': name,
            'mime_type': mime,
            'size_bytes': size,
            'extraction_method': method,
            'content': text[:12000],
            'truncated': len(text) > 12000,
        })

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
        url = f"{GRAPH_BASE}/sites/{site_id}/drive/root/search(q='{query}')?$top={count}&$select=id,name,file,folder,size,lastModifiedDateTime,webUrl,parentReference"
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
    Read the content of a file stored in a SharePoint site's document library.
    Supports Excel (.xlsx), Word (.docx), PowerPoint (.pptx), PDF, and plain text files.

    :param site_id: The SharePoint site ID (from list_sharepoint_sites)
    :param item_id: The item ID (from list_sharepoint_site_files or search_sharepoint)
    :return: JSON with filename, mime type, size, and extracted text content (up to 12000 chars)
    """
    if not __request__ or not __user__:
        return json.dumps({'error': 'Request context not available'})

    token = await _get_valid_token(__request__, __user__.get('id', ''))
    if not token:
        return json.dumps({'error': 'Microsoft account not connected.'})

    workbook_base = f'/sites/{site_id}/drive/items/{item_id}'

    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                f'{GRAPH_BASE}{workbook_base}?$select=name,file,size',
                headers=_headers(token),
                ssl=AIOHTTP_CLIENT_SESSION_SSL,
            ) as resp:
                if resp.status != 200:
                    return json.dumps({'error': f'Item not found (status {resp.status})'})
                meta = await resp.json()

            mime = meta.get('file', {}).get('mimeType', '')
            name = meta.get('name', '')
            size = meta.get('size', 0)

            lower_name = name.lower()
            needs_download = not (
                'spreadsheetml' in mime
                or lower_name.endswith('.xlsx')
                or lower_name.endswith('.xls')
                or lower_name.endswith('.xlsm')
            )

            raw = b''
            if needs_download:
                async with session.get(
                    f'{GRAPH_BASE}{workbook_base}/content',
                    headers={'Authorization': f'Bearer {token}'},
                    ssl=AIOHTTP_CLIENT_SESSION_SSL,
                ) as dl_resp:
                    if dl_resp.status != 200:
                        return json.dumps({'error': f'Could not download file (status {dl_resp.status})'})
                    raw = await dl_resp.read()

        text, method = await _extract_content(token, raw, mime, name, workbook_base)

        if text is None:
            return json.dumps({
                'name': name,
                'mime_type': mime,
                'size_bytes': size,
                'content': None,
                'note': f'Cannot extract text from this file type ({mime}). Open it directly via its SharePoint URL.',
            })

        return json.dumps({
            'name': name,
            'mime_type': mime,
            'size_bytes': size,
            'extraction_method': method,
            'content': text[:12000],
            'truncated': len(text) > 12000,
        })

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
