import traceback

from fourplay.admin import decide_group_route, edit_group_route, list_groups_route
from fourplay.groups import submit_group_route
from fourplay.logger import log
from fourplay.puzzle import generate_puzzle
from fourplay.utils import (
    format_response,
    get_request_metadata,
    has_invalid_domain,
    logged_in_check_route,
    login_route,
    otp_route,
    path_equals,
)


def lambda_handler(event, context):
    # The EventBridge rule the developer creates (see backend/README.md)
    # targets this same function — no separate Lambda needed for puzzle
    # generation. EventBridge events carry "source": "aws.events" and have
    # no "headers"/"httpMethod", so this check has to come before anything
    # that assumes an API Gateway event shape.
    if event.get("source") == "aws.events":
        return generate_puzzle()

    try:
        log(get_request_metadata(event), event.get("headers"))
        return route(event)
    except Exception:
        traceback.print_exc()
        return format_response(event=event, http_code=500, body="Internal server error")


# Only using POST because I want to prevent CORS preflight checks — same
# rationale as kaios-calorie-counter/backend/lambda/lambda_function.py.
def route(event):
    if has_invalid_domain(event=event):
        return format_response(event=event, http_code=403, body={"message": "Forbidden"})

    if path_equals(event=event, method="POST", path="/submit-group"):
        return submit_group_route(event)

    # Admin moderation routes — phone-OTP login, then list/decide/edit.
    if path_equals(event=event, method="POST", path="/admin/otp"):
        return otp_route(event)
    if path_equals(event=event, method="POST", path="/admin/login"):
        return login_route(event)
    if path_equals(event=event, method="POST", path="/admin/logged-in-check"):
        return logged_in_check_route(event)
    if path_equals(event=event, method="POST", path="/admin/list-groups"):
        return list_groups_route(event)
    if path_equals(event=event, method="POST", path="/admin/decide-group"):
        return decide_group_route(event)
    if path_equals(event=event, method="POST", path="/admin/edit-group"):
        return edit_group_route(event)

    if path_equals(event=event, method="POST", path="/test"):
        return format_response(event=event, http_code=200, body={"status": "up"})

    return format_response(event=event, http_code=403, body={"message": "Forbidden"})
