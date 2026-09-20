"""Identity of the development shop and user created by `python -m app.seed`.

Until authentication exists (Phase 14) every request acts as this user, in this shop.
"""

DEV_SHOP_NAME = "Development Shop"
DEV_SHOP_BUSINESS_TYPE = "GROCERY"
DEV_USER_EMAIL = "owner@dev.kirana.local"
# Not a valid hash of any password, so nobody can log in as this user.
UNUSABLE_PASSWORD_HASH = "!"
