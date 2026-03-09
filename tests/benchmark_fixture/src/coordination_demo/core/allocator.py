class TokenBucket:
    def rate_limit(self):
        return "rate_limit"


def rate_limited() -> bool:
    return False
