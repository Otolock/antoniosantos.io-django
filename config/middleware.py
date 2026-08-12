from .ai_policy import CONTENT_SIGNAL


class ContentRightsReservationMiddleware:
    """Expose machine-readable restrictions on AI training and text mining."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        response["Content-Signal"] = CONTENT_SIGNAL
        response["TDM-Reservation"] = "1"
        return response
