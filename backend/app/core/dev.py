"""Identity of the development shop and user created by `python -m app.seed`.

Only when KIRANA_DEV_AUTH_BYPASS is on (never in production) does a request act as this user.
"""

DEV_SHOP_NAME = "Development Shop"
DEV_SHOP_BUSINESS_TYPE = "GROCERY"
DEV_USER_EMAIL = "owner@dev.kirana.local"
# Not a valid hash of any password, so nobody can log in as this user.
UNUSABLE_PASSWORD_HASH = "!"
