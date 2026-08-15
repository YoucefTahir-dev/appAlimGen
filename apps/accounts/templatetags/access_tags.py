from django import template

from apps.accounts.permissions import has_permission, has_permissions


register = template.Library()


@register.simple_tag(takes_context=True)
def has_access(context, permission_name):
    return has_permission(context['request'].user, permission_name)


@register.simple_tag(takes_context=True)
def has_any_access(context, *permission_names):
    return has_permissions(
        context['request'].user,
        permission_names,
        any_permission=True,
    )
