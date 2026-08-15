from django.core.paginator import Paginator


DEFAULT_PAGE_SIZE = 25


def paginate_queryset(request, queryset, *, per_page=DEFAULT_PAGE_SIZE):
    """Return a safe page and the current query string without its page number."""
    page = Paginator(queryset, per_page).get_page(request.GET.get('page'))
    query = request.GET.copy()
    query.pop('page', None)
    return page, query.urlencode()
