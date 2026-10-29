"""Numbered pagination shared by every list page."""
from django import template

register = template.Library()

WINDOW = 2


def page_numbers(current, total):
    """Return page numbers with None marking a skipped range."""
    if total <= 7:
        return list(range(1, total + 1))
    numbers = []
    for number in range(1, total + 1):
        near_edge = number <= WINDOW + 1 or number > total - (WINDOW + 1)
        near_current = abs(number - current) <= WINDOW
        if near_edge or near_current:
            numbers.append(number)
        elif numbers and numbers[-1] is not None:
            numbers.append(None)
    return numbers


@register.inclusion_tag('core/partials/pagination.html', takes_context=True)
def pagination(context, page_obj):
    """Render page links while keeping the current query string."""
    request = context.get('request')
    query = request.GET.copy() if request else None
    if query is not None and 'page' in query:
        del query['page']
    querystring = query.urlencode() if query else ''
    return {
        'page_obj': page_obj,
        'page_count': page_obj.paginator.num_pages,
        'page_numbers': page_numbers(page_obj.number, page_obj.paginator.num_pages),
        'querystring': querystring,
    }
