"""One sales scope for Web, API, documents, histories and payment replays."""
from apps.accounts.permissions import has_permission
from apps.inventory.models import LoadingOrder


def personal_sales_only(user):
    # Explicit global delegation remains authoritative. A seller must not gain
    # depot-wide access merely because their first loading has not been created.
    if has_permission(user, 'inventory.view_all_loadingorders'):
        return False
    return (
        getattr(user, 'role', None) == getattr(user, 'SELLER', 'seller')
        or LoadingOrder.objects.filter(operator=user).exists()
    )
