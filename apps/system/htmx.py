"""
AquaFlow — HTMX helpers
Shared by all apps that use HTMX.
"""

import json


def is_htmx(request):
    """True when the request came from HTMX."""
    return request.headers.get('HX-Request') == 'true'


def htmx_trigger(response, message=None, toast_type='success',
                 close_modal=False, refresh=False):
    """
    Attach client-side events to an HTMX response.

    Example:
        response = HttpResponse(status=204)
        return htmx_trigger(response, 'Saved!', close_modal=True)
    """
    events = {}

    if message:
        events['showToast'] = {'message': message, 'type': toast_type}

    if close_modal:
        events['closeModal'] = True

    if events:
        response['HX-Trigger'] = json.dumps(events)

    if refresh:
        response['HX-Refresh'] = 'true'

    return response