"""Safe application errors raised by the Keitaro client."""

from app.errors import AppError


class KeitaroError(AppError):
    """Base class for upstream Keitaro failures."""

    status_code = 502


class KeitaroNotFoundError(KeitaroError):
    status_code = 404
    code = "keitaro_not_found"

    def __init__(self) -> None:
        super().__init__("Keitaro resource was not found")
        self.upstream_status = 404


class KeitaroTimeoutError(KeitaroError):
    status_code = 504
    code = "keitaro_timeout"

    def __init__(self) -> None:
        super().__init__("Keitaro request timed out")


class KeitaroUnavailableError(KeitaroError):
    code = "keitaro_unavailable"

    def __init__(self) -> None:
        super().__init__("Keitaro is unavailable")


class KeitaroUpstreamError(KeitaroError):
    code = "keitaro_upstream_error"

    def __init__(self, upstream_status: int) -> None:
        super().__init__("Keitaro rejected the request")
        self.upstream_status = upstream_status


class KeitaroInvalidResponseError(KeitaroError):
    code = "keitaro_invalid_response"

    def __init__(self) -> None:
        super().__init__("Keitaro returned an invalid response")


class KeitaroInvalidOfferWeightsError(KeitaroError):
    code = "keitaro_invalid_offer_weights"

    def __init__(self) -> None:
        super().__init__(
            "Offer weights in Keitaro are invalid. Check them in the tracker "
            "and fetch the streams again."
        )
