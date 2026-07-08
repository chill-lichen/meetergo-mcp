#!/usr/bin/env python3
"""
meetergo MCP server
===================

A Model Context Protocol (MCP) server exposing the meetergo scheduling + CRM API
(https://developer.meetergo.com) as tools an AI assistant can call.

Built against the meetergo Platform API **v4** (host: https://api.meetergo.com).
Most endpoints live under /v4; the CRM (/crm) and Webhooks (/webhooks) live at
the host root — this server handles both.

Because meetergo syncs natively with Proton Calendar (CalDAV), this lets you run
a Proton-backed scheduling + light-CRM stack entirely through an AI assistant,
with no Google Workspace in the loop.

Auth: a single Bearer token via MEETERGO_API_KEY. Two token types both work:
  * Personal Access Token ("rgo-...")        -> acts as YOU / your own workspace (recommended)
  * Platform API Key ("ak_live:uuid:secret") -> multi-user; also set MEETERGO_USER_ID

Environment variables:
  MEETERGO_API_KEY    (required)  the Bearer token
  MEETERGO_USER_ID    (optional)  user UUID -> sent as the x-meetergo-api-user-id header
  MEETERGO_API_BASE   (optional)  override base URL (default https://api.meetergo.com/v4)
  MEETERGO_TIMEOUT    (optional)  per-request timeout in seconds (default 30)

Run:  meetergo-mcp        (after `pip install .`)
 or:  python meetergo_mcp.py

⚠️  Community project, not affiliated with or endorsed by meetergo. Provided
    "as is" under the BSD-3-Clause license — no warranty. See README.
"""

from __future__ import annotations

import os
import time
from typing import Any, Optional

import httpx
from mcp.server.fastmcp import FastMCP

__version__ = "0.1.1"

API_BASE = os.environ.get("MEETERGO_API_BASE", "https://api.meetergo.com/v4").rstrip("/")
# Host root (for /crm and /webhooks, which are NOT under /v4)
API_ROOT = API_BASE[:-3].rstrip("/") if API_BASE.endswith("/v4") else API_BASE
API_KEY = os.environ.get("MEETERGO_API_KEY")
USER_ID = os.environ.get("MEETERGO_USER_ID")
TIMEOUT = float(os.environ.get("MEETERGO_TIMEOUT", "30"))
USER_AGENT = f"meetergo-mcp/{__version__} (+https://github.com/chill-lichen/meetergo-mcp)"

# HTTP statuses worth retrying (rate limit + transient upstream errors)
_RETRY_STATUSES = {429, 502, 503, 504}
_MAX_RETRIES = 3

mcp = FastMCP("meetergo")


# --------------------------------------------------------------------------- #
# HTTP helper
# --------------------------------------------------------------------------- #
def _headers() -> dict[str, str]:
    if not API_KEY:
        raise RuntimeError(
            "MEETERGO_API_KEY is not set. Add your meetergo Bearer token "
            "(Personal Access Token 'rgo-...' or Platform API Key 'ak_live:...') "
            "to the environment."
        )
    headers = {
        "Authorization": f"Bearer {API_KEY}",
        "Content-Type": "application/json",
        "User-Agent": USER_AGENT,
    }
    if USER_ID:
        headers["x-meetergo-api-user-id"] = USER_ID
    return headers


def _request(
    method: str,
    path: str,
    *,
    params: Optional[dict[str, Any]] = None,
    json: Optional[dict[str, Any]] = None,
    root: bool = False,
) -> Any:
    """Call the meetergo API and return parsed JSON, or a readable error string.

    - Set root=True for host-level endpoints (/crm, /webhooks) not under /v4.
    - Retries transient failures (429/502/503/504) with exponential backoff.
    - Never raises to the model; errors come back as strings prefixed "ERROR".
    """
    base = API_ROOT if root else API_BASE
    url = f"{base}{path}"
    clean_params = {k: v for k, v in (params or {}).items() if v is not None}

    last_err = "unknown error"
    for attempt in range(_MAX_RETRIES):
        try:
            with httpx.Client(timeout=TIMEOUT) as client:
                resp = client.request(
                    method,
                    url,
                    headers=_headers(),
                    params=clean_params or None,
                    json=json,
                )
        except Exception as exc:  # network / DNS / timeout
            last_err = f"ERROR: request to {method} {url} failed: {exc}"
            time.sleep(0.5 * (2 ** attempt))
            continue

        if resp.status_code in _RETRY_STATUSES and attempt < _MAX_RETRIES - 1:
            # honor Retry-After if present, else exponential backoff
            retry_after = resp.headers.get("Retry-After")
            delay = float(retry_after) if (retry_after or "").isdigit() else 0.5 * (2 ** attempt)
            time.sleep(delay)
            continue

        if resp.status_code == 429:
            return "ERROR 429: rate limited (~100 req/min per key). Back off and retry."
        if not resp.is_success:
            try:
                body = resp.json()
            except Exception:
                body = resp.text
            return f"ERROR {resp.status_code} on {method} {path}: {body}"
        if resp.status_code == 204 or not resp.content:
            return {"ok": True, "status": resp.status_code}
        try:
            return resp.json()
        except Exception:
            return resp.text

    return last_err


# ========================================================================== #
# ACCOUNT
# ========================================================================== #
@mcp.tool()
def get_me() -> Any:
    """Return the authenticated meetergo user (verifies auth; gives your userId).
    Call this first to confirm the connector works."""
    return _request("GET", "/user/me")


# ========================================================================== #
# MEETING TYPES & BOOKING PAGES
# ========================================================================== #
@mcp.tool()
def list_meeting_types() -> Any:
    """List bookable meeting types (event types) with ids, names, durations, slugs."""
    return _request("GET", "/meeting-type")


@mcp.tool()
def get_meeting_type(meeting_type_id: str) -> Any:
    """Get full config of a single meeting type (branding, CSS mode, redirect,
    reminders, CRM sync options)."""
    return _request("GET", f"/meeting-type/{meeting_type_id}")


@mcp.tool()
def create_meeting_type(payload: dict) -> Any:
    """Create a meeting type. meetergo requires a fairly complete `meetingInfo`
    object, so pass the full body as `payload` (schema: CreateMeetingTypeV4Dto).

    Minimal example:
        {
          "meetingInfo": {
            "name": "Discovery Call", "description": "",
            "duration": 30, "channel": "zoom",
            "enableRedirect": false, "redirect": "",
            "passEventDetailsToRedirect": false,
            "customChannelName": "", "customChannelLink": "",
            "groupBooking": false, "showAvailableSlots": false,
            "enrichInvitee": false, "bufferBefore": 0, "bufferAfter": 0,
            "confirmationButton": {"useConfirmationButton": false,
                                   "text": "", "color": "", "link": ""}
          },
          "slug": "discovery-call"
        }
    Tip: POST /v4/user creates a user + default meeting type in one call; use this
    endpoint when you need a specific configuration.
    """
    return _request("POST", "/meeting-type", json=payload)


@mcp.tool()
def update_meeting_type(meeting_type_id: str, patch: dict) -> Any:
    """Edit a meeting type (partial update — send only fields you want to change):
    rename, change duration/buffers, redirect URL, branding color, booking-page
    password, cancellation/rescheduling policy, CRM sync toggles, etc.

    Example: {"meetingInfo": {"duration": 60}, "slug": "discovery-call-60"}
    """
    return _request("PATCH", f"/meeting-type/{meeting_type_id}", json=patch)


@mcp.tool()
def create_one_time_booking_link(meeting_type_id: str) -> Any:
    """Generate a single-use booking link for a meeting type. Returns {id, url, ...}."""
    return _request("POST", f"/one-time-booking-link/create/{meeting_type_id}")


# ========================================================================== #
# PERSONAL PAGE BRANDING (colors & graphics)
# ========================================================================== #
@mcp.tool()
def get_personal_page() -> Any:
    """Get your personal booking page: colors, header image, description,
    online profiles, meeting-type order."""
    return _request("GET", "/personal-page/me")


@mcp.tool()
def update_personal_page(patch: dict) -> Any:
    """Update your personal page branding & graphics.

    Supported fields (all optional):
        useCustomColors (bool), primaryColor (str), secondaryColor (str),
        headerImage (url str or null), description (str),
        showAllMeetingTypes (bool), meetingTypeOrder (list[str]),
        onlineProfiles: { linkedIn, facebook, twitter, instagram, xing, phone,
            email, addressStreet, addressCity, addressPostalCode, addressCountry,
            customLinks: [{name, url, icon}] }

    Example: {"useCustomColors": true, "primaryColor": "#2E4A3F",
              "headerImage": "https://.../hero.jpg"}

    Note: covers brand colors, header image and profile links. Meeting-type pages
    also have CSS *modes* (see update_meeting_type -> cssSetting), but arbitrary raw
    CSS is configured in the meetergo dashboard, not this API.
    """
    return _request("PATCH", "/personal-page/me", json=patch)


# ========================================================================== #
# AVAILABILITY
# ========================================================================== #
@mcp.tool()
def get_availability(
    meeting_type_id: str,
    start: str,
    end: str,
    timezone: str = "America/New_York",
) -> Any:
    """Open bookable slots for a meeting type in a date range.

    Args:
        meeting_type_id: Meeting type to check.
        start: "YYYY-MM-DD".
        end: "YYYY-MM-DD".
        timezone: IANA tz for the returned times.
    """
    return _request(
        "GET",
        "/booking-availability",
        params={
            "meetingTypeId": meeting_type_id,
            "start": start,
            "end": end,
            "timezone": timezone,
        },
    )


# ========================================================================== #
# BOOKINGS / APPOINTMENTS
# ========================================================================== #
@mcp.tool()
def create_booking(
    meeting_type_id: str,
    start: str,
    attendee_email: str,
    attendee_firstname: str,
    attendee_lastname: str,
    timezone: Optional[str] = None,
    notes: Optional[str] = None,
) -> Any:
    """Book an appointment (metered/billed by meetergo).

    Args:
        meeting_type_id: Meeting type being booked.
        start: ISO 8601, e.g. "2026-07-15T14:00:00Z".
        attendee_email / attendee_firstname / attendee_lastname: attendee.
        timezone: optional IANA tz of attendee.
        notes: optional booking note.
    """
    body: dict[str, Any] = {
        "meetingTypeId": meeting_type_id,
        "start": start,
        "attendee": {
            "email": attendee_email,
            "firstname": attendee_firstname,
            "lastname": attendee_lastname,
        },
    }
    if timezone:
        body["timezone"] = timezone
    if notes:
        body["notes"] = notes
    return _request("POST", "/booking", json=body)


@mcp.tool()
def list_appointments(
    page: int = 0,
    page_size: int = 20,
    start: Optional[str] = None,
    end: Optional[str] = None,
    search: Optional[str] = None,
    status: Optional[str] = None,
    meeting_type_id: Optional[str] = None,
    sort_by: Optional[str] = None,
    sort_direction: Optional[str] = None,
) -> Any:
    """List appointments (paginated).

    Args:
        page: 0-indexed page number (0 = first page).
        page_size: items per page (1-100).
        start / end: optional ISO date/time bounds.
        search: optional free-text search.
        status: optional status filter.
        meeting_type_id: optional filter by meeting type.
        sort_by: "appointment.start" | "appointment.createdAt".
        sort_direction: "ASC" | "DESC".
    """
    return _request(
        "GET",
        "/appointment/paginated",
        params={
            "page": page,
            "pageSize": page_size,
            "start": start,
            "end": end,
            "search": search,
            "status": status,
            "meetingTypeId": meeting_type_id,
            "sortBy": sort_by,
            "sortDirection": sort_direction,
        },
    )


@mcp.tool()
def get_appointment(appointment_id: str) -> Any:
    """Get full details of a single appointment (attendees, hosts, notes, transcript)."""
    return _request("GET", f"/appointment/{appointment_id}")


@mcp.tool()
def cancel_appointment(
    appointment_id: str,
    reason: Optional[str] = None,
    attendee_id: Optional[str] = None,
    cancel_all: Optional[bool] = None,
    cancel_entire_series: Optional[bool] = None,
) -> Any:
    """Cancel an appointment. For group bookings, pass attendee_id (single) or
    cancel_all=True (whole appointment)."""
    body: dict[str, Any] = {}
    if reason is not None:
        body["reason"] = reason
    if attendee_id is not None:
        body["attendeeId"] = attendee_id
    if cancel_all is not None:
        body["cancelAll"] = cancel_all
    if cancel_entire_series is not None:
        body["cancelEntireSeries"] = cancel_entire_series
    return _request("POST", f"/appointment/{appointment_id}/cancel", json=body)


@mcp.tool()
def reschedule_appointment(
    appointment_id: str,
    start: str,
    ignore_availability: Optional[bool] = None,
) -> Any:
    """Move an appointment to a new start time (duration unchanged)."""
    body: dict[str, Any] = {"start": start}
    if ignore_availability is not None:
        body["ignoreAvailability"] = ignore_availability
    return _request("POST", f"/appointment/{appointment_id}/reschedule", json=body)


@mcp.tool()
def update_appointment_notes(appointment_id: str, note: str) -> Any:
    """Add/replace the host-side note on an appointment."""
    return _request("PATCH", f"/appointment/{appointment_id}/notes", json={"note": note})


@mcp.tool()
def update_meeting_transcription(
    appointment_id: str,
    transcription: Optional[str] = None,
    summary: Optional[str] = None,
) -> Any:
    """Store a meeting transcript and/or AI summary (markdown) on an appointment.

    meetergo does NOT record meetings itself — feed this from a notetaker
    (Granola, Fireflies, etc.). Only provided fields are sent; omit a field to
    leave it unchanged.

    Args:
        appointment_id: The appointment.
        transcription: Full transcript in markdown.
        summary: AI-generated summary in markdown.
    """
    body: dict[str, Any] = {}
    if transcription is not None:
        body["transcription"] = transcription
    if summary is not None:
        body["summary"] = summary
    return _request("PATCH", f"/appointment/{appointment_id}/transcription", json=body)


# ========================================================================== #
# FOLLOW-UP EMAILS
# ========================================================================== #
@mcp.tool()
def send_quick_email(attendee_id: str, title: str, content: str) -> Any:
    """Send a one-off email to an attendee (e.g. a manual/AI-drafted follow-up).
    Rate limited to 5 per 5 minutes.

    Args:
        attendee_id: Attendee to email (from a booking/webhook payload).
        title: Subject line.
        content: Body.
    """
    return _request(
        "POST",
        "/attendee/quick-mail",
        json={"attendeeId": attendee_id, "title": title, "content": content},
    )


# ========================================================================== #
# ROUTING FORMS + BRANCHING LOGIC
# ========================================================================== #
@mcp.tool()
def create_routing_form(form: dict) -> Any:
    """Create a routing form / multi-step funnel with branching logic.

    `form` is the full body. Key fields:
        name (str), structureType ("FORM_ONLY" | "FUNNEL_WITH_FORM" | "FUNNEL_ONLY"),
        showProgressBar (bool),
        funnelSteps: [{ dataFields: [{ dataField: {label, fieldType, options,...} }] }],
        fields: [{ dataFieldId, order }]  (reference existing data fields),
        qualifiers: [ routing rules ].

    A qualifier routes the visitor based on conditions:
        { "routingAction": "eventRedirect", "meetingTypeId": "...",
          "expression": {"operator":"and","operands":[
             {"operator":"equals","target":"attendeeOther",
              "customTarget":"Company Size","value":"enterprise"}]} }
    routingAction ∈ eventRedirect | customPage | externalRedirect | contactForm |
    requestCallback | instantCall | formRedirect. Include one qualifier with
    "isFallback": true as the default. Field types include text/email/phone/select/
    radio/checkbox/file/signature/pdf-template/currency/rating and more.
    """
    return _request("POST", "/routing-form", json=form)


@mcp.tool()
def list_routing_forms() -> Any:
    """List routing forms."""
    return _request("GET", "/routing-form")


@mcp.tool()
def get_routing_form(form_id: str) -> Any:
    """Get a routing form's full definition."""
    return _request("GET", f"/routing-form/{form_id}")


@mcp.tool()
def update_routing_form(form_id: str, patch: dict) -> Any:
    """Update a routing form. Qualifiers use declarative sync (items with `id` are
    updated, without `id` created, missing removed); funnelSteps/fields are full-replace.
    Set a `slug` here to create an unlimited public share link
    (https://cal.meetergo.com/f/<slug>)."""
    return _request("PATCH", f"/routing-form/{form_id}", json=patch)


@mcp.tool()
def delete_routing_form(form_id: str) -> Any:
    """Delete a routing form."""
    return _request("DELETE", f"/routing-form/{form_id}")


@mcp.tool()
def send_routing_form(
    form_id: str,
    recipient_name: str,
    email: Optional[str] = None,
    phone: Optional[str] = None,
    message: Optional[str] = None,
    delivery_method: str = "email",
) -> Any:
    """Send a routing form to a recipient (one-time link). Returns {publicUrl, status}.

    Args:
        form_id: Form to send.
        recipient_name: Recipient display name.
        email: Required if delivery_method="email".
        phone: Required if delivery_method="sms".
        message: Optional cover message.
        delivery_method: "email" | "sms" | "link" (link = generate URL only).
    """
    body: dict[str, Any] = {"recipientName": recipient_name, "deliveryMethod": delivery_method}
    if email:
        body["email"] = email
    if phone:
        body["phone"] = phone
    if message:
        body["message"] = message
    return _request("POST", f"/routing-form/{form_id}/send", json=body)


@mcp.tool()
def list_form_recipients(form_id: str) -> Any:
    """List recipients of a form with status (sent/opened/completed) and timestamps."""
    return _request("GET", f"/routing-form/{form_id}/recipients")


@mcp.tool()
def create_data_field(field: dict) -> Any:
    """Create a reusable form data field (company-scoped).
    Example: {"label":"Budget Range","fieldType":"select","required":true,
              "options":[{"label":"< $10k","value":"small"},
                         {"label":"$50k+","value":"large"}]}"""
    return _request("POST", "/data-field", json=field)


@mcp.tool()
def list_data_fields() -> Any:
    """List reusable form data fields."""
    return _request("GET", "/data-field")


# ========================================================================== #
# CONTACTS / CRM  (host root, /crm)
# ========================================================================== #
@mcp.tool()
def create_contact(
    first_name: Optional[str] = None,
    last_name: Optional[str] = None,
    email: Optional[str] = None,
    phone_number: Optional[str] = None,
    tags: Optional[list[str]] = None,
    notes: Optional[str] = None,
    additional_data: Optional[dict] = None,
) -> Any:
    """Create a CRM contact (either email or phone_number is required).

    Args:
        first_name, last_name, email, phone_number: basics.
        tags: labels like ["Lead", "Enterprise"].
        notes: internal notes.
        additional_data: custom fields (e.g. {"revenueBand": "$1-5M"}).
    """
    body: dict[str, Any] = {}
    for key, value in {
        "firstName": first_name,
        "lastName": last_name,
        "email": email,
        "phoneNumber": phone_number,
        "tags": tags,
        "notes": notes,
        "additionalData": additional_data,
    }.items():
        if value is not None:
            body[key] = value
    return _request("POST", "/crm", json=body, root=True)


@mcp.tool()
def search_contacts(
    search_term: Optional[str] = None,
    tags: Optional[list[str]] = None,
    owner_id: Optional[str] = None,
    sort_by: Optional[str] = None,
    sort_order: Optional[str] = None,
    page: Optional[int] = None,
    limit: Optional[int] = None,
) -> Any:
    """Search/list CRM contacts (paginated).

    Args:
        search_term: match name/email/phone.
        tags: filter by tags.
        owner_id: filter by account owner.
        sort_by: firstName|lastName|email|createdAt.
        sort_order: ASC|DESC.
        page, limit: pagination (limit max 100).
    """
    return _request(
        "GET",
        "/crm",
        params={
            "searchTerm": search_term,
            "tags": tags,
            "ownerId": owner_id,
            "sortBy": sort_by,
            "sortOrder": sort_order,
            "page": page,
            "limit": limit,
        },
        root=True,
    )


@mcp.tool()
def get_contact(contact_id: Optional[str] = None, attendee_id: Optional[str] = None) -> Any:
    """Get a contact's full record (incl. linked appointments and form answers).
    Look up by contact_id OR by attendee_id (from a booking webhook)."""
    if not contact_id and not attendee_id:
        return "ERROR: provide contact_id or attendee_id."
    return _request(
        "GET",
        "/crm/details",
        params={"contactId": contact_id, "attendeeId": attendee_id},
        root=True,
    )


@mcp.tool()
def update_contact(contact_id: str, patch: dict) -> Any:
    """Update a contact (partial). Example: {"tags":["Lead","Qualified"],
    "notes":"Interested in premium plan"}."""
    return _request("PATCH", f"/crm/{contact_id}", json=patch, root=True)


@mcp.tool()
def delete_contact(contact_id: str) -> Any:
    """Delete a CRM contact."""
    return _request("DELETE", f"/crm/{contact_id}", root=True)


@mcp.tool()
def bulk_create_contacts(contacts: list[dict]) -> Any:
    """Bulk-create contacts (e.g. a CSV import). Each item like the create_contact body:
    {"firstName","lastName","email","phoneNumber","tags","notes"}."""
    return _request("POST", "/crm/bulk", json={"contacts": contacts}, root=True)


# ========================================================================== #
# WEBHOOKS  (host root, /webhooks)
# ========================================================================== #
@mcp.tool()
def list_webhooks() -> Any:
    """List webhook endpoints (max 6 per company)."""
    return _request("GET", "/webhooks", root=True)


@mcp.tool()
def create_webhook(endpoint: str, event_types: list[str], description: Optional[str] = None) -> Any:
    """Register a webhook to drive automations (feed bookings/forms into a CRM,
    email drafts, your website, etc.).

    Args:
        endpoint: HTTPS URL to receive POSTs.
        event_types: any of booking_created, booking_cancelled, booking_rescheduled,
            form_submission, new_employee.
        description: optional label.
    """
    body: dict[str, Any] = {"endpoint": endpoint, "eventTypes": event_types}
    if description:
        body["description"] = description
    return _request("POST", "/webhooks", json=body, root=True)


@mcp.tool()
def update_webhook(webhook_id: str, patch: dict) -> Any:
    """Update a webhook (endpoint / description / eventTypes — all optional)."""
    return _request("PATCH", f"/webhooks/{webhook_id}", json=patch, root=True)


@mcp.tool()
def delete_webhook(webhook_id: str) -> Any:
    """Delete a webhook endpoint."""
    return _request("DELETE", f"/webhooks/{webhook_id}", root=True)


# ========================================================================== #
# CALENDAR CONNECTIONS
# ========================================================================== #
@mcp.tool()
def list_calendar_connections() -> Any:
    """List connected calendars (confirm your Proton Calendar CalDAV link is active)."""
    return _request("GET", "/calendar-connections/connections")


def main() -> None:
    """Console entry point (stdio transport)."""
    mcp.run()


if __name__ == "__main__":
    main()
